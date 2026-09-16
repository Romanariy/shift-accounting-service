import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync, sync_to_async
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.shifts.management.commands.run_shift_bot import process_ledger_callback
from apps.shifts.models import AuditLog, Employee, Organization
from .billing import approve, delete_batch, delete_invoice, prepare
from .engine import save_record
from .models import Delivery, Invoice, OrganizationBilling, PaymentMessageUpdate, Rate, Record, Service, Settings, TelegramContact
from .payments import claim_message_update, mark_paid, payment_keyboard, payment_status, update_message_once
from .worker import claim, deliver_once


class InvoicePaymentTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(name="Фокус")
        self.other = Organization.objects.exclude(pk=self.org.pk).first()
        self.employee = Employee.objects.first()
        self.day = date(2026, 4, 12)
        self.first = TelegramContact.objects.create(user_id=1101, name="Первый")
        self.second = TelegramContact.objects.create(user_id=1102, name="Второй")
        self.observer = TelegramContact.objects.create(user_id=1103, name="Без подтверждения")
        self.profile = OrganizationBilling.objects.get(organization=self.org)
        self.profile.recipients.set([self.first, self.second, self.observer])
        self.profile.payment_recipients.set([self.first, self.second])
        Settings.objects.filter(pk=1).update(approver=None)
        Record.objects.create(kind="oneoff", organization=self.org, employee=self.employee,
                              date=self.day, amount=Decimal("1250"), description="Работа")

    def approved_invoice(self):
        batch = prepare(self.day, self.day, [self.org.pk])
        self.assertTrue(approve(batch.pk, batch.version)["approved"])
        return batch.invoices.get()

    def sent(self, invoice):
        for delivery in Delivery.objects.filter(invoice=invoice, purpose="owner"):
            delivery.state, delivery.message_id = "sent", invoice.pk * 10000 + delivery.recipient + 100
            delivery.save()

    def bot(self):
        bot = AsyncMock()
        bot.send_document.side_effect = lambda recipient, *args, **kwargs: SimpleNamespace(message_id=recipient + 100)
        return bot

    def test_one_recipient_closes_shared_invoice_for_both_idempotently(self):
        invoice = self.approved_invoice()
        self.sent(invoice)
        first = mark_paid(invoice.pk, self.first.user_id)
        second = mark_paid(invoice.pk, self.second.user_id)
        self.assertFalse(first["already_paid"])
        self.assertTrue(second["already_paid"])
        self.assertEqual(first["paid_at"], second["paid_at"])
        self.assertEqual(second["paid_by"], self.first.user_id)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_state, "paid")
        self.assertEqual(PaymentMessageUpdate.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(entity_type="invoice", entity_id=invoice.pk, action="paid").count(), 1)

    def test_unauthorized_unopted_undelivered_and_forged_message_rejected(self):
        invoice = self.approved_invoice()
        with self.assertRaises(ValidationError):
            mark_paid(invoice.pk, self.first.user_id)
        self.sent(invoice)
        for user_id in (9999, self.observer.user_id):
            with self.subTest(user_id=user_id), self.assertRaises(ValidationError):
                mark_paid(invoice.pk, user_id)
        for kwargs in ({"message_id": 999}, {"chat_id": -100}, {"version": invoice.version + 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                mark_paid(invoice.pk, self.first.user_id, **kwargs)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_state, "open")
        self.assertFalse(PaymentMessageUpdate.objects.exists())

    def test_settings_are_organization_specific_and_snapshot_is_immutable(self):
        other_profile = OrganizationBilling.objects.get(organization=self.other)
        other_profile.recipients.add(self.first)
        Record.objects.create(kind="oneoff", organization=self.other, employee=self.employee, date=self.day, amount=200)
        batch = prepare(self.day, self.day, [self.org.pk, self.other.pk])
        approve(batch.pk, batch.version)
        invoice, other_invoice = batch.invoices.get(organization=self.org), batch.invoices.get(organization=self.other)
        self.assertEqual(invoice.payment_state, "open")
        self.assertEqual(other_invoice.payment_state, "none")
        self.assertEqual(other_invoice.payment_recipients, [])
        self.profile.payment_recipients.clear()
        self.profile.recipients.clear()
        self.sent(invoice)
        self.assertEqual(mark_paid(invoice.pk, self.first.user_id)["payment_state"], "paid")

    def test_changed_payment_options_require_fresh_approval(self):
        batch = prepare(self.day, self.day, [self.org.pk])
        self.profile.payment_recipients.set([self.second])
        result = approve(batch.pk, batch.version)
        self.assertTrue(result["refreshed"])
        self.assertFalse(result["approved"])
        invoice = batch.invoices.get()
        self.assertEqual(invoice.payment_recipients, [self.second.user_id])
        self.assertEqual(invoice.payment_state, "none")
        batch.refresh_from_db()
        approve(batch.pk, batch.version)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_state, "open")

    def test_unselected_contact_cannot_enter_snapshot_even_with_stale_relation(self):
        self.profile.recipients.remove(self.second)
        invoice = self.approved_invoice()
        self.assertEqual(invoice.payment_recipients, [self.first.user_id])

    def test_no_opt_in_or_zero_amount_does_not_open_ticket(self):
        self.profile.payment_recipients.clear()
        invoice = self.approved_invoice()
        self.assertEqual(invoice.payment_state, "none")
        self.assertIsNone(payment_keyboard(invoice, self.first.user_id))
        other_profile = OrganizationBilling.objects.get(organization=self.other)
        other_profile.recipients.add(self.first)
        other_profile.payment_recipients.add(self.first)
        batch = prepare(self.day, self.day, [self.other.pk])
        approve(batch.pk, batch.version)
        self.assertEqual(batch.invoices.get().payment_state, "none")
        self.assertFalse(Delivery.objects.filter(batch=batch, purpose="owner").exists())

    def test_replacement_supersedes_old_open_ticket_on_approval_only(self):
        original = self.approved_invoice()
        self.sent(original)
        draft = prepare(self.day, self.day, [self.org.pk], replaces=original.batch_id)
        original.refresh_from_db()
        self.assertEqual(original.payment_state, "open")
        approve(draft.pk, draft.version)
        original.refresh_from_db()
        replacement = draft.invoices.get()
        self.assertEqual(original.payment_state, "superseded")
        self.assertEqual(replacement.payment_state, "open")
        self.assertEqual(Invoice.objects.filter(payment_state="open").count(), 1)
        with self.assertRaises(ValidationError):
            mark_paid(original.pk, self.first.user_id)
        self.sent(replacement)
        mark_paid(replacement.pk, self.second.user_id)
        self.assertEqual(PaymentMessageUpdate.objects.count(), 4)

    def test_replacement_cancels_old_pending_delivery_and_preserves_paid_history(self):
        original = self.approved_invoice()
        first_delivery = Delivery.objects.get(invoice=original, recipient=self.first.user_id)
        first_delivery.state, first_delivery.message_id = "sent", 101
        first_delivery.save()
        mark_paid(original.pk, self.first.user_id)
        replacement = prepare(self.day, self.day, [self.org.pk], replaces=original.batch_id)
        approve(replacement.pk, replacement.version)
        original.refresh_from_db()
        self.assertEqual(original.payment_state, "paid")
        self.assertFalse(Delivery.objects.filter(invoice=original, state="pending").exists())
        self.assertEqual(replacement.invoices.get().payment_state, "open")
        with self.assertRaises(ValidationError):
            mark_paid(original.pk, self.first.user_id)

    def test_documents_have_buttons_only_for_opted_owner_recipients(self):
        invoice = self.approved_invoice()
        bot = self.bot()
        for _ in range(3):
            self.assertTrue(async_to_sync(deliver_once)(bot))
        self.assertFalse(async_to_sync(deliver_once)(bot))
        for call in bot.send_document.call_args_list:
            recipient, markup = call.args[0], call.kwargs["reply_markup"]
            if recipient == self.observer.user_id:
                self.assertIsNone(markup)
            else:
                button = markup.inline_keyboard[0][0]
                self.assertEqual(button.text, "Оплатил")
                self.assertEqual(button.callback_data, f"ledger:paid:{invoice.pk}:{invoice.version}")
        mark_paid(invoice.pk, self.first.user_id)
        self.assertTrue(async_to_sync(deliver_once)(bot))
        self.assertTrue(async_to_sync(deliver_once)(bot))
        self.assertFalse(async_to_sync(deliver_once)(bot))
        self.assertEqual(bot.edit_message_reply_markup.await_count, 2)
        self.assertEqual({c.kwargs["chat_id"] for c in bot.edit_message_reply_markup.call_args_list},
                         {self.first.user_id, self.second.user_id})
        self.assertTrue(all(c.kwargs["reply_markup"] is None for c in bot.edit_message_reply_markup.call_args_list))

    def test_worker_does_not_resend_replaced_failed_invoice(self):
        self.profile.payment_recipients.clear()  # Guard also applies without payment tracking.
        original = self.approved_invoice()
        delivery = Delivery.objects.get(invoice=original, recipient=self.first.user_id)
        Delivery.objects.filter(invoice=original).update(state="failed")
        replacement = prepare(self.day, self.day, [self.org.pk], replaces=original.batch_id)
        approve(replacement.pk, replacement.version)
        Delivery.objects.filter(pk=delivery.pk).update(state="pending")  # Stale retry/racing old worker.
        claimed = claim()
        self.assertEqual(claimed.invoice_id, replacement.invoices.get().pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "cancelled")

    def test_preview_never_contains_payment_button(self):
        Settings.objects.filter(pk=1).update(approver=self.first)
        prepare(self.day, self.day, [self.org.pk])
        bot = self.bot()
        self.assertTrue(async_to_sync(deliver_once)(bot))
        self.assertIsNone(bot.send_document.call_args.kwargs["reply_markup"])

    def test_payment_during_other_recipient_delivery_still_removes_both_buttons(self):
        self.profile.recipients.remove(self.observer)
        invoice = self.approved_invoice()
        bot = self.bot()
        async_to_sync(deliver_once)(bot)

        async def pay_while_sending(recipient, *args, **kwargs):
            await sync_to_async(mark_paid)(invoice.pk, self.first.user_id)
            return SimpleNamespace(message_id=recipient + 100)

        bot.send_document.side_effect = pay_while_sending
        async_to_sync(deliver_once)(bot)
        self.assertEqual(PaymentMessageUpdate.objects.count(), 2)
        async_to_sync(deliver_once)(bot)
        async_to_sync(deliver_once)(bot)
        self.assertEqual(bot.edit_message_reply_markup.await_count, 2)

    def test_payment_before_pending_delivery_never_shows_new_pay_button(self):
        invoice = self.approved_invoice()
        self.profile.recipients.remove(self.observer)
        bot = self.bot()
        async_to_sync(deliver_once)(bot)
        mark_paid(invoice.pk, self.first.user_id)
        async_to_sync(deliver_once)(bot)  # Remove the first recipient's button.
        async_to_sync(deliver_once)(bot)  # Send the second recipient's document.
        self.assertEqual(bot.send_document.call_args.args[0], self.second.user_id)
        self.assertIsNone(bot.send_document.call_args.kwargs["reply_markup"])

    def test_keyboard_timeout_backoff_and_crash_are_safely_retried(self):
        invoice = self.approved_invoice()
        self.sent(invoice)
        mark_paid(invoice.pk, self.first.user_id)
        bot = self.bot()
        bot.edit_message_reply_markup.side_effect = TimeoutError()
        self.assertTrue(async_to_sync(update_message_once)(bot))
        first = PaymentMessageUpdate.objects.order_by("id").first()
        self.assertEqual(first.state, "pending")
        self.assertGreater(first.retry_at, timezone.now())
        claimed = claim_message_update()
        self.assertNotEqual(claimed.pk, first.pk)
        PaymentMessageUpdate.objects.filter(pk=claimed.pk).update(started_at=timezone.now() - timedelta(minutes=6))
        reclaimed = claim_message_update()
        self.assertEqual(reclaimed.pk, claimed.pk)
        self.assertEqual(reclaimed.attempts, 2)
        PaymentMessageUpdate.objects.filter(pk=first.pk).update(retry_at=timezone.now() - timedelta(seconds=1))
        bot.edit_message_reply_markup.side_effect = None
        self.assertTrue(async_to_sync(update_message_once)(bot))
        first.refresh_from_db()
        self.assertEqual(first.state, "sent")
        self.assertEqual(first.attempts, 2)

    def test_keyboard_rate_limit_and_already_applied_update(self):
        from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
        from aiogram.methods import EditMessageReplyMarkup
        invoice = self.approved_invoice()
        self.sent(invoice)
        mark_paid(invoice.pk, self.first.user_id)
        method = EditMessageReplyMarkup(chat_id=self.first.user_id, message_id=1)
        bot = self.bot()
        bot.edit_message_reply_markup.side_effect = TelegramRetryAfter(method=method, message="Wait", retry_after=42)
        before = timezone.now()
        async_to_sync(update_message_once)(bot)
        first = PaymentMessageUpdate.objects.order_by("id").first()
        self.assertGreaterEqual(first.retry_at, before + timedelta(seconds=42))
        bot.edit_message_reply_markup.side_effect = TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        async_to_sync(update_message_once)(bot)
        self.assertEqual(PaymentMessageUpdate.objects.filter(state="sent").count(), 1)

    def test_delete_leaves_durable_keyboard_cleanup_and_stale_callback_safe(self):
        invoice = self.approved_invoice()
        self.sent(invoice)
        invoice_id = invoice.pk
        delete_invoice(invoice_id)
        self.assertFalse(Invoice.objects.filter(pk=invoice_id).exists())
        self.assertEqual(PaymentMessageUpdate.objects.filter(invoice=None).count(), 2)
        bot = self.bot()
        async_to_sync(deliver_once)(bot)
        async_to_sync(deliver_once)(bot)
        self.assertEqual(bot.edit_message_reply_markup.await_count, 2)
        query = SimpleNamespace(data=f"ledger:paid:{invoice_id}:1", from_user=SimpleNamespace(id=self.first.user_id),
            message=SimpleNamespace(message_id=1201, chat=SimpleNamespace(id=self.first.user_id)), answer=AsyncMock())
        async_to_sync(process_ledger_callback)(query, bot)
        self.assertIn("не найден", bot.send_message.call_args.args[1])

    def test_batch_deletion_cleans_all_organization_payment_messages(self):
        other_profile = OrganizationBilling.objects.get(organization=self.other)
        other_profile.recipients.add(self.first)
        other_profile.payment_recipients.add(self.first)
        Record.objects.create(kind="oneoff", organization=self.other, employee=self.employee, date=self.day, amount=200)
        batch = prepare(self.day, self.day, [self.org.pk, self.other.pk])
        approve(batch.pk, batch.version)
        for invoice in batch.invoices.all():
            for delivery in Delivery.objects.filter(invoice=invoice):
                delivery.state, delivery.message_id = "sent", delivery.pk + 100
                delivery.save()
        delete_batch(batch.pk)
        self.assertEqual(PaymentMessageUpdate.objects.filter(invoice=None).count(), 3)

    def test_callback_updates_shared_status_and_rejects_forwarded_message(self):
        invoice = self.approved_invoice()
        self.sent(invoice)
        bot = self.bot()
        query = SimpleNamespace(data=f"ledger:paid:{invoice.pk}:{invoice.version}", from_user=SimpleNamespace(id=self.first.user_id),
            message=SimpleNamespace(message_id=invoice.pk * 10000 + self.first.user_id + 100, chat=SimpleNamespace(id=-100)), answer=AsyncMock())
        async_to_sync(process_ledger_callback)(query, bot)
        self.assertIn("личном сообщении", bot.send_message.call_args.args[1])
        query.message.chat.id = self.first.user_id
        async_to_sync(process_ledger_callback)(query, bot)
        self.assertIn("всех получателей", bot.send_message.call_args.args[1])
        async_to_sync(process_ledger_callback)(query, bot)
        self.assertIn("уже подтверждена", bot.send_message.call_args.args[1])
        invoice.refresh_from_db()
        self.assertEqual(payment_status(invoice)["paid_by"], self.first.user_id)

    def test_archived_rate_keeps_recorded_invoice_amount(self):
        service = Service.objects.get(legacy_code="small_admin")
        row = save_record({"kind": "service", "organization": self.org.pk, "employee": self.employee.pk,
            "service": service.pk, "date": self.day.isoformat(), "start_time": "10:00", "end_time": "14:00"})
        amount = row.amount
        Rate.objects.filter(service=service, organization=self.org).update(active=False, deleted_at=timezone.now())
        invoice = self.approved_invoice()
        self.assertEqual(invoice.errors, [])
        self.assertEqual(invoice.total, Decimal("1250") + amount)
        row.refresh_from_db()
        self.assertEqual(row.amount, amount)

    def test_api_recipient_options_are_validated_atomically_and_removed_together(self):
        url = f"/api/shifts/ledger/organizations/{self.profile.pk}/"
        response = self.client.put(url, json.dumps({"recipients": [self.first.pk],
            "payment_recipients": [self.second.pk]}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.profile.recipients.count(), 3)
        self.assertEqual(self.profile.payment_recipients.count(), 2)
        response = self.client.put(url, json.dumps({"recipients": [self.first.pk]}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["payment_recipients"], [self.first.pk])
        response = self.client.get("/api/shifts/ledger/bootstrap/")
        profile = next(item for item in response.json()["organizations"] if item["id"] == self.profile.pk)
        self.assertEqual(profile["recipients"], [self.first.pk])
        self.assertEqual(profile["payment_recipients"], [self.first.pk])

    def test_api_payment_tickets_ignore_month_and_close_for_all_recipients(self):
        invoice = self.approved_invoice()
        self.sent(invoice)
        url = "/api/shifts/ledger/payments/"
        response = self.client.get(url, {"month": "2026-09", "limit": 1})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["open_count"], 1)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["invoices"][0]["id"], invoice.pk)
        self.assertEqual(response.json()["invoices"][0]["start"], self.day.isoformat())
        closed = self.client.get(url, {"state": "paid"}).json()
        self.assertEqual(closed["count"], 0)
        self.assertEqual(closed["open_count"], 1)
        mark_paid(invoice.pk, self.second.user_id)
        self.assertEqual(self.client.get(url).json()["open_count"], 0)
        self.assertEqual(self.client.get(url).json()["invoices"], [])
        closed = self.client.get(url, {"state": "paid"}).json()
        self.assertEqual(closed["count"], 1)
        self.assertEqual(closed["invoices"][0]["paid_by_name"], self.second.name)
        self.assertTrue(closed["invoices"][0]["paid_at"])
        batch = self.client.get(f"/api/shifts/ledger/batches/{invoice.batch_id}/").json()
        self.assertEqual(batch["invoices"][0]["payment_state"], "paid")
        self.assertEqual(batch["invoices"][0]["paid_at"], closed["invoices"][0]["paid_at"])
        self.assertEqual(self.client.get(url, {"state": "invalid"}).status_code, 400)

    def test_api_cannot_retry_failed_delivery_of_replaced_invoice(self):
        original = self.approved_invoice()
        Delivery.objects.filter(invoice=original).update(state="failed")
        delivery = Delivery.objects.get(invoice=original, recipient=self.first.user_id)
        replacement = prepare(self.day, self.day, [self.org.pk], replaces=original.batch_id)
        approve(replacement.pk, replacement.version)
        response = self.client.post(f"/api/shifts/ledger/deliveries/{delivery.pk}/retry/", "{}",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("заменён", response.json()["error"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "failed")
