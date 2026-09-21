from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from apps.ledger.models import Settings
from apps.shifts.dates import date_label, date_time_label, excel_date_format
from . import service
from .bot import callback_action
from .delivery import claim_delivery, finish_delivery, run_once, resolve_delivery
from .models import OfferConfig, OfferDelivery, OfferMessageEdit
from .presentation import describe
from .routing import validate_routes
from .tests import Fixture


class TopicLifecycleTests(Fixture, TestCase):
    def setUp(self):
        super().setUp()
        self.message_id = 100
        async def sent(*args, **kwargs):
            self.message_id += 1
            return SimpleNamespace(message_id=self.message_id)
        self.bot = SimpleNamespace(send_photo=AsyncMock(side_effect=sent), send_message=AsyncMock(side_effect=sent),
            send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=700), SimpleNamespace(message_id=701)]),
            delete_message=AsyncMock(), edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock())

    def drain(self):
        for _ in range(80):
            if not async_to_sync(run_once)(self.bot):
                return
        self.fail("Delivery queue did not settle")

    def published(self):
        offer = self.offer()
        self.image(offer)
        service.publish(offer.pk)
        self.drain()
        offer.refresh_from_db()
        return offer

    def test_claim_moves_photos_and_release_returns_to_active_without_private_duplicate(self):
        offer = self.published()
        initial = list(offer.deliveries.filter(purpose__in=service.ACTIVE_PURPOSES))
        self.assertTrue(all(d.thread_id == 77 for d in initial))
        claimed = service.claim(offer.pk, 90003, offer.version)
        self.drain()
        for d in initial:
            d.refresh_from_db()
            self.assertIsNotNone(d.deleted_at)
        self.assertEqual(offer.deliveries.filter(purpose="claimed_photos", thread_id=78, state="sent").count(), 1)
        self.assertEqual(offer.deliveries.filter(purpose="claimed_topic", thread_id=78, state="sent").count(), 1)
        for recipient in (90001, 90003):
            self.assertTrue(offer.deliveries.filter(purpose="assignment_photos", recipient=recipient, state="sent").exists())
            self.assertTrue(offer.deliveries.filter(purpose="assignment", recipient=recipient, state="sent").exists())
        before_private = len([c for c in self.bot.send_message.await_args_list if c.kwargs["chat_id"] > 0])
        released = service.release(offer.pk, user_id=90003, version=claimed.version)
        self.drain()
        self.assertEqual(released.state, "open")
        self.assertEqual(len([c for c in self.bot.send_message.await_args_list if c.kwargs["chat_id"] > 0]), before_private)
        self.assertFalse(offer.deliveries.filter(purpose__in=service.CLAIMED_PURPOSES, deleted_at=None, state="sent").exists())
        active = offer.deliveries.get(purpose="topic", generation=released.delivery_generation)
        self.assertEqual((active.thread_id, active.state), (77, "sent"))
        notice = offer.deliveries.get(purpose="release_notice")
        self.assertEqual((notice.recipient, notice.thread_id, notice.state), (-100123, 79, "sent"))
        self.assertIn("Снят сотрудник: OCR Employee", notice.payload["text"])
        self.assertFalse(offer.deliveries.filter(purpose="released").exists())
        # Even a forged latest version cannot make an old, deleted control valid.
        old_control = next(d for d in initial if d.purpose == "topic")
        with self.assertRaises(ValidationError):
            callback_action(f"offer:claim:{offer.pk}:{released.version}", 90003, -100123, old_control.message_ids[0])
        callback_action(f"offer:claim:{offer.pk}:{released.version}", 90003, -100123, active.message_ids[0])
        self.drain()
        notice.refresh_from_db()
        self.assertIn("Свободна", notice.payload["text"])  # Notification is an immutable event.

    def test_release_while_assignment_photos_are_in_flight_cleans_late_send(self):
        offer = self.published()
        claimed = service.claim(offer.pk, 90003)
        late = offer.deliveries.get(purpose="assignment_photos", recipient=90003)
        late.state, late.attempts = "sending", 1
        late.save()
        service.release(offer.pk, user_id=90003, version=claimed.version)
        late.refresh_from_db()
        self.assertTrue(late.delete_requested)
        finish_delivery(late.pk, [801, 802], attempt=1)
        self.drain()
        late.refresh_from_db()
        self.assertIsNotNone(late.deleted_at)
        removed = {c.kwargs["message_id"] for c in self.bot.delete_message.await_args_list}
        self.assertTrue({801, 802}.issubset(removed))
        self.assertFalse(offer.deliveries.filter(purpose="assignment", state="sent").exists())

    def test_unknown_old_photo_delivery_is_deleted_after_manual_reconciliation(self):
        offer = self.published()
        service.claim(offer.pk, 90003)
        late = offer.deliveries.get(purpose="assignment_photos", recipient=90003)
        late.state = "unknown"; late.save()
        released = service.release(offer.pk, user_id=90003)
        resolve_delivery(offer.pk, late.pk, "sent", [851, 852], released.version)
        self.drain()
        late.refresh_from_db()
        self.assertIsNotNone(late.deleted_at)

    def test_missing_message_deletion_is_successful_and_permanent_failure_is_visible(self):
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import DeleteMessage
        offer = self.published()
        service.claim(offer.pk, 90003)
        self.bot.delete_message.side_effect = TelegramBadRequest(method=DeleteMessage(chat_id=-100123, message_id=1), message="message to delete not found")
        self.drain()
        self.assertFalse(offer.deliveries.filter(purpose__in=service.ACTIVE_PURPOSES, deleted_at=None).exists())
        self.bot.delete_message.side_effect = TelegramBadRequest(method=DeleteMessage(chat_id=90003, message_id=1), message="message can't be deleted")
        service.release(offer.pk, user_id=90003)
        private_before = self.bot.send_message.await_count
        self.drain()
        self.assertTrue(OfferMessageEdit.objects.filter(delivery__offer=offer, state="failed", error__contains="48").exists())
        self.assertGreater(self.bot.edit_message_reply_markup.await_count, 0)
        self.assertFalse(offer.deliveries.filter(purpose="released").exists())
        self.assertEqual(self.bot.send_message.await_count - private_before, 2)  # Active control + public notice.

    def test_transient_delete_failure_is_retried_without_duplicate_sends(self):
        offer = self.published()
        service.claim(offer.pk, 90003)
        self.bot.delete_message.side_effect = [TimeoutError(), None, None]
        async_to_sync(run_once)(self.bot)
        edit = OfferMessageEdit.objects.get(delivery__offer=offer, delivery__purpose="photos")
        self.assertEqual(edit.state, "pending")
        OfferMessageEdit.objects.filter(pk=edit.pk).update(available_at=timezone.now())
        self.bot.delete_message.side_effect = None
        self.drain()
        edit.delivery.refresh_from_db()
        self.assertIsNotNone(edit.delivery.deleted_at)
        self.assertEqual(offer.deliveries.filter(purpose="claimed_topic").count(), 1)

    def test_releasing_ongoing_offer_works_with_intake_disabled(self):
        offer = self.published()
        claimed = service.claim(offer.pk, 90003)
        OfferConfig.objects.filter(pk=1).update(enabled=False)
        # Date/time validation for a brand-new proposal must not prevent returning an ongoing shift.
        from unittest.mock import patch
        with patch("apps.offers.service.validate_publish", side_effect=AssertionError("Do not republish as a new draft")):
            service.release(offer.pk, user_id=90003, version=claimed.version)
            self.drain()
        offer.refresh_from_db()
        self.assertEqual(offer.state, "open")
        self.assertTrue(offer.deliveries.filter(purpose="topic", generation=offer.delivery_generation, state="sent").exists())

    def test_all_offer_routes_are_distinct_and_accounting_conflicts_are_checked(self):
        ledger = Settings.objects.get(pk=1)
        config = OfferConfig.objects.get(pk=1)
        for field in ("claimed_thread_id", "released_thread_id"):
            previous = getattr(config, field)
            setattr(config, field, config.thread_id)
            with self.assertRaises(ValidationError): validate_routes(ledger, config)
            setattr(config, field, previous)
            ledger.expense_chat, ledger.expense_thread = config.chat_id, previous
            with self.assertRaises(ValidationError): validate_routes(ledger, config)
            ledger.expense_chat, ledger.expense_thread = None, None
        config.claimed_thread_id = None
        with self.assertRaises(ValidationError): validate_routes(ledger, config)


class WeekdayTests(SimpleTestCase):
    def test_weekday_labels_are_locale_independent_and_timestamps_use_local_day(self):
        self.assertEqual(date_label(date(2026, 9, 27)), "27.09.2026 (Вс)")
        self.assertEqual(date_label("2026-09-28"), "28.09.2026 (Пн)")
        self.assertEqual(date_time_label("2026-09-27T20:30:00+00:00"), "28.09.2026 (Пн) 01:30:00")
        self.assertEqual(excel_date_format(date(2026, 9, 27)), 'DD.MM.YYYY" (Вс)"')
