import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync, sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections
from django.test import TestCase, TransactionTestCase

from apps.shifts.models import AuditLog, Organization
from apps.shifts.management.commands.run_shift_bot import process_ledger_callback
from .billing import approve, delete_invoice, prepare
from .models import Delivery, Invoice, OrganizationBilling, PaymentMessageUpdate, Record, Settings, TelegramContact
from .payments import claim_message_update, finish_message_update, mark_paid, payment_keyboard, update_message_once, web_payment
from .worker import deliver_once


class Fixture:
    def setUp(self):
        self.org = Organization.objects.create(name="Web payment studio")
        self.contact = TelegramContact.objects.create(user_id=88221, name="Плательщик")
        profile, _ = OrganizationBilling.objects.get_or_create(organization=self.org)
        profile.recipients.add(self.contact); profile.payment_recipients.add(self.contact)
        Settings.objects.update_or_create(pk=1, defaults={"approver": None})
        day = date(2026, 4, 12)
        Record.objects.create(kind="oneoff", organization=self.org, date=day, amount=1250, description="Работа")
        batch = prepare(day, day, [self.org.pk])
        approve(batch.pk, batch.version)
        self.invoice = batch.invoices.get()

    def payload(self):
        self.invoice.refresh_from_db()
        return {"version": self.invoice.version, "payment_revision": self.invoice.payment_revision}

    def sent(self):
        Delivery.objects.filter(invoice=self.invoice, purpose="owner").update(state="sent", message_id=120)

    def post(self, action, data):
        return self.client.post(f"/api/shifts/ledger/invoices/{self.invoice.pk}/{action}/", json.dumps(data), content_type="application/json")


class WebPaymentTests(Fixture, TestCase):
    def test_api_confirm_undelivered_cancel_and_history_no_financial_changes(self):
        artifact = bytes(self.invoice.artifact)
        deliveries = list(Delivery.objects.filter(invoice=self.invoice).values())
        payload = self.payload()
        first = self.post("confirm-payment", payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["payment_revision"], 1)
        self.assertEqual(self.post("confirm-payment", payload).json()["already_paid"], True)
        listing = self.client.get("/api/shifts/ledger/invoices/?payment_state=paid").json()
        self.assertEqual(listing["open_count"], 0)
        self.assertTrue(listing["invoices"][0]["payment_action_allowed"])
        self.assertEqual(self.post("cancel-payment", self.payload()).status_code, 400)
        cancel = {**self.payload(), "reason": "Ошибка сверки"}
        self.assertEqual(self.post("cancel-payment", cancel).status_code, 200)
        self.assertEqual(self.post("cancel-payment", cancel).status_code, 400)
        self.assertEqual(self.post("confirm-payment", payload).status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.payment_state, self.invoice.paid_at, self.invoice.paid_by, self.invoice.payment_revision), ("open", None, None, 2))
        self.assertEqual(bytes(self.invoice.artifact), artifact)
        self.assertEqual(list(Delivery.objects.filter(invoice=self.invoice).values()), deliveries)
        self.assertEqual(Record.objects.get(organization=self.org).amount, 1250)
        self.assertEqual(self.client.get("/api/shifts/ledger/invoices/").json()["open_count"], 1)
        events = list(AuditLog.objects.filter(entity_type="invoice", entity_id=self.invoice.pk, action__in=("paid", "payment_cancelled")).order_by("id"))
        self.assertEqual([e.actor for e in events], ["web", "web"])
        self.assertEqual(events[1].before["paid_at"], events[0].after["paid_at"])
        self.assertEqual(events[1].after["reason"], "Ошибка сверки")

    def test_telegram_cancel_restores_revision_button_and_old_callback_is_rejected(self):
        self.sent()
        legacy = f"ledger:paid:{self.invoice.pk}:{self.invoice.version}"
        mark_paid(self.invoice.pk, self.contact.user_id)
        bot = AsyncMock()
        async_to_sync(update_message_once)(bot)
        self.assertIsNone(bot.edit_message_reply_markup.call_args.kwargs["reply_markup"])
        web_payment(self.invoice.pk, {**self.payload(), "reason": "Платёж другого счёта"}, cancel=True)
        event = AuditLog.objects.get(entity_type="invoice", entity_id=self.invoice.pk, action="payment_cancelled")
        self.assertEqual(event.before["paid_by"], self.contact.user_id)
        async_to_sync(update_message_once)(bot)
        markup = bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, legacy + ":2")
        for callback in (legacy, legacy + ":0"):
            query = SimpleNamespace(data=callback, from_user=SimpleNamespace(id=self.contact.user_id),
                message=SimpleNamespace(message_id=120, chat=SimpleNamespace(id=self.contact.user_id)), answer=AsyncMock())
            async_to_sync(process_ledger_callback)(query, bot)
            self.invoice.refresh_from_db(); self.assertEqual(self.invoice.payment_state, "open")
        query.data = legacy + ":2"
        async_to_sync(process_ledger_callback)(query, bot)
        self.invoice.refresh_from_db(); self.assertEqual(self.invoice.payment_state, "paid")

    def test_stale_completion_cannot_consume_new_keyboard_generation(self):
        self.sent(); web_payment(self.invoice.pk, self.payload())
        item = claim_message_update()
        web_payment(self.invoice.pk, {**self.payload(), "reason": "Ошибка"}, cancel=True)
        self.assertIsNone(claim_message_update())
        finish_message_update(item)
        current = PaymentMessageUpdate.objects.get(pk=item.pk)
        self.assertEqual(current.state, "pending")
        bot = AsyncMock(); async_to_sync(update_message_once)(bot)
        self.assertIsNotNone(bot.edit_message_reply_markup.call_args.kwargs["reply_markup"])
        # Even an old network request arriving after a newer attempt finishes
        # schedules a final reconciliation instead of marking it complete.
        finish_message_update(item)
        current.refresh_from_db(); self.assertEqual(current.state, "pending")

    def test_deleting_invoice_removes_restored_button(self):
        self.sent()
        bot = AsyncMock()
        web_payment(self.invoice.pk, self.payload())
        async_to_sync(update_message_once)(bot)
        web_payment(self.invoice.pk, {**self.payload(), "reason": "Ошибка"}, cancel=True)
        async_to_sync(update_message_once)(bot)
        self.assertIsNotNone(bot.edit_message_reply_markup.call_args.kwargs["reply_markup"])
        delete_invoice(self.invoice.pk)
        async_to_sync(update_message_once)(bot)
        self.assertIsNone(bot.edit_message_reply_markup.call_args.kwargs["reply_markup"])
        self.assertEqual(bot.edit_message_reply_markup.await_count, 3)

    def test_status_changes_while_document_delivery_in_flight(self):
        bot = AsyncMock()
        async def send(*args, **kwargs):
            await sync_to_async(web_payment)(self.invoice.pk, {"version": 1, "payment_revision": 0})
            await sync_to_async(web_payment)(self.invoice.pk, {"version": 1, "payment_revision": 1, "reason": "Ошибка"}, cancel=True)
            return SimpleNamespace(message_id=120)
        bot.send_document.side_effect = send
        async_to_sync(deliver_once)(bot)
        async_to_sync(update_message_once)(bot)
        self.assertTrue(bot.edit_message_reply_markup.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.endswith(":2"))
        self.assertEqual(bot.send_document.await_count, 1)

    def test_invalid_states_versions_and_replacements(self):
        payload = self.payload()
        for data in ({}, {**payload, "version": 99}, {**payload, "payment_revision": 99}):
            self.assertEqual(self.post("confirm-payment", data).status_code, 400)
        for state in ("none", "superseded"):
            Invoice.objects.filter(pk=self.invoice.pk).update(payment_state=state)
            self.assertEqual(self.post("confirm-payment", payload).status_code, 400)
        Invoice.objects.filter(pk=self.invoice.pk).update(payment_state="open")
        self.invoice.batch.state = "draft"; self.invoice.batch.save()
        self.assertEqual(self.post("confirm-payment", payload).status_code, 400)
        self.invoice.batch.state = "approved"; self.invoice.batch.save()
        web_payment(self.invoice.pk, payload)
        Invoice.objects.create(batch=self.invoice.batch, organization=self.org, replaces=self.invoice, payment_state="open")
        self.assertEqual(self.post("cancel-payment", {**self.payload(), "reason": "Ошибка"}).status_code, 400)
        row = next(i for i in self.client.get("/api/shifts/ledger/invoices/").json()["invoices"] if i["id"] == self.invoice.pk)
        self.assertFalse(row["payment_action_allowed"])


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL locks")
class WebPaymentConcurrencyTests(Fixture, TransactionTestCase):
    def race(self, actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=15); return action()
            except ValidationError: return "stale"
            finally: connections.close_all()
        with ThreadPoolExecutor(len(actions)) as pool: return list(pool.map(run, actions))

    def test_web_and_telegram_confirm_once(self):
        self.sent(); payload = self.payload()
        self.race([lambda:web_payment(self.invoice.pk, payload), lambda:mark_paid(self.invoice.pk, self.contact.user_id, payment_revision=0)])
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.payment_state, self.invoice.payment_revision), ("paid", 1))
        self.assertEqual(AuditLog.objects.filter(entity_type="invoice", entity_id=self.invoice.pk, action="paid").count(), 1)

    def test_cancel_once_and_reject_old_confirm(self):
        self.sent(); web_payment(self.invoice.pk, self.payload())
        payload = {**self.payload(), "reason": "Ошибка"}
        result = self.race([lambda:web_payment(self.invoice.pk, payload, cancel=True), lambda:web_payment(self.invoice.pk, payload, cancel=True)])
        self.assertEqual(result.count("stale"), 1)
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.payment_state, self.invoice.payment_revision), ("open", 2))
        with self.assertRaises(ValidationError): mark_paid(self.invoice.pk, self.contact.user_id, payment_revision=0)
