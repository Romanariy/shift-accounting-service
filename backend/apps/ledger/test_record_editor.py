import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.shifts.models import AuditLog, CompanionEntry, Employee, Organization, ShiftEntry, SyncOutbox
from .billing import prepare
from .engine import save_record
from .models import Accrual, DailyCalculation, Rate, Record, Service


@override_settings(SHIFT_SYNC_ENDPOINT="", SHIFT_SYNC_AFTER_WRITE=False, TELEGRAM_BOT_TOKEN="", SHARED_SHIFT_ALLOCATION_ENABLED=True)
class RecordEditorTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(name="Фокус")
        self.employee = Employee.objects.first()
        self.service = Service.objects.get(legacy_code="small_admin")
        self.day = date(2026, 4, 12)
        self.payload = {"kind": "service", "date": str(self.day), "organization": self.org.pk,
                        "employee": self.employee.pk, "service": self.service.pk}

    def post(self, resource, payload):
        return self.client.post(f"/api/shifts/ledger/{resource}/", json.dumps(payload), content_type="application/json")

    def test_screenshot_interval_calculates_hours_and_cost_with_blank_amount_and_hours(self):
        response = self.post("records", {**self.payload, "start_time": "10:30", "end_time": "14:30", "units": "", "amount": ""})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["units"]), 4)
        self.assertEqual(Decimal(response.json()["amount"]), 800)

    def test_night_shift_ignores_stale_hours_but_incomplete_interval_is_rejected(self):
        response = self.post("records", {**self.payload, "start_time": "22:30", "end_time": "02:30", "units": "999", "amount": None})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["units"]), 4)
        for payload in ({"start_time": "10:00"}, {"start_time": "10:00", "end_time": "10:00"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post("records", {**self.payload, **payload}).status_code, 400)

    @override_settings(SHIFT_SYNC_AFTER_WRITE=True)
    def test_preview_rolls_back_records_groups_audits_legacy_and_external_callbacks(self):
        counts = {model: model.objects.count() for model in (Record, DailyCalculation, AuditLog, ShiftEntry, SyncOutbox)}
        with patch("apps.shifts.sync.try_sync_once") as sender, self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.post("record-preview", {**self.payload, "start_time": "10:30", "end_time": "14:30", "amount": ""})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["record"]["amount"]), 800)
        for model, count in counts.items():
            self.assertEqual(model.objects.count(), count, model.__name__)
        self.assertEqual(callbacks, [])
        sender.assert_not_called()

    def test_shared_day_preview_matches_save_and_does_not_change_other_participant(self):
        Rate.objects.filter(service=self.service, organization=self.org).update(price=300, maximum=1500, minimum=600)
        first = save_record({**self.payload, "units": "4"})
        payload = {**self.payload, "units": "2", "amount": ""}
        response = self.post("record-preview", payload)
        self.assertEqual(response.status_code, 200, response.content)
        preview = response.json()["record"]
        self.assertEqual(Decimal(preview["amount"]), 500)
        self.assertEqual(Decimal(preview["daily_group"]["total"]), 1500)
        self.assertEqual(len(preview["daily_group"]["members"]), 2)
        first.refresh_from_db()
        self.assertEqual(first.amount, 1200)
        saved = self.post("records", payload).json()
        self.assertEqual(saved["amount"], preview["amount"])
        first.refresh_from_db()
        self.assertEqual(first.amount, 1000)

    def test_preview_comment_edit_preserves_snapshot(self):
        row = save_record({**self.payload, "units": "4"})
        Rate.objects.filter(service=self.service, organization=self.org).update(price=999, maximum=None)
        response = self.post("record-preview", {"id": row.pk, "description": "Только комментарий"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["record"]["amount"]), 800)
        row.refresh_from_db()
        self.assertEqual(row.description, "")

    def test_fixed_and_quantity_services_accept_blank_cost(self):
        cleaning = Service.objects.get(legacy_code="cleaning")
        response = self.post("records", {**self.payload, "service": cleaning.pk, "amount": "", "units": "", "start_time": "10:00"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["amount"]), 700)
        self.assertIsNone(response.json()["start_time"])
        companion = Service.objects.get(legacy_code="companion")
        response = self.post("records", {**self.payload, "service": companion.pk, "amount": "", "units": "2"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["amount"]), 1000)
        self.assertEqual(CompanionEntry.objects.get().count, 2)
        response = self.post("records", {**self.payload, "service": companion.pk, "units": "1.5"})
        self.assertEqual(response.status_code, 400)

    def test_ready_amount_and_expenses_require_positive_amount(self):
        service = Service.objects.create(name="Готовая стоимость", input_type="amount")
        Rate.objects.create(service=service, organization=self.org, calculation="amount", start=self.day)
        for value in ("", None, "0", "-50"):
            with self.subTest(value=value):
                self.assertEqual(self.post("records", {**self.payload, "service": service.pk, "amount": value}).status_code, 400)
                self.assertEqual(self.post("records", {**self.payload, "kind": "expense", "amount": value, "description": "Расход"}).status_code, 400)
        response = self.post("records", {**self.payload, "service": service.pk, "amount": "1500,50"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["amount"]), Decimal("1500.50"))

    def test_invalid_preview_body_returns_validation_error(self):
        for value in ([], None):
            self.assertEqual(self.post("record-preview", value).status_code, 400)

    def test_service_archive_preserves_work_and_allows_restoration(self):
        row = save_record({**self.payload, "units": "4"})
        response = self.client.delete(f"/api/shifts/ledger/services/{self.service.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.service.refresh_from_db()
        self.assertFalse(self.service.active)
        self.assertIsNotNone(self.service.deleted_at)
        row.refresh_from_db()
        self.assertEqual(row.amount, 800)
        self.assertEqual(self.post("records", {**self.payload, "units": "4"}).status_code, 400)
        response = self.client.put(f"/api/shifts/ledger/services/{self.service.pk}/", json.dumps({"active": True}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.service.refresh_from_db()
        self.assertIsNone(self.service.deleted_at)
        self.assertTrue(self.service.active)

    def test_tariff_archive_preserves_saved_invoice_amount_but_blocks_new_day(self):
        row = save_record({**self.payload, "units": "4"})
        rate = Rate.objects.get(service=self.service, organization=self.org, active=True)
        response = self.client.delete(f"/api/shifts/ledger/rates/{rate.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        rate.refresh_from_db()
        self.assertFalse(rate.active)
        self.assertIsNotNone(rate.deleted_at)
        batch = prepare(self.day, self.day, [self.org.pk])
        invoice = batch.invoices.get()
        self.assertEqual(invoice.total, row.amount)
        self.assertEqual(invoice.errors, ["Не назначен получатель Telegram."])
        self.assertEqual(self.post("records", {**self.payload, "date": "2026-04-13", "units": "4"}).status_code, 400)
