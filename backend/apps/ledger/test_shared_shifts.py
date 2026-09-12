import json
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from openpyxl import load_workbook

from apps.shifts.models import AuditLog, Employee, Organization, PayRule, ShiftEntry, SyncOutbox
from .billing import approve, prepare
from .engine import delete_record, recalculate, record_dict, save_record
from .grouping import allocation
from .ingest import parse
from .models import DailyCalculation, Invoice, Rate, Record, Service
from . import tests as ledger_tests


@override_settings(SHIFT_SYNC_AFTER_WRITE=False, SHIFT_SYNC_ENDPOINT="")
class SharedShiftTests(TestCase):
    setUp = ledger_tests.LedgerTests.setUp
    ingest = ledger_tests.LedgerTests.ingest

    def rate(self, price=300, minimum=600, maximum=1500):
        rate = Rate.objects.get(service=self.service, organization=self.focus)
        rate.price, rate.minimum, rate.maximum = price, minimum, maximum
        rate.save()
        return rate

    def shift(self, hours, **kwargs):
        return save_record({"kind": "service", "service": self.service.pk, "organization": self.focus.pk,
                            "employee": self.employee.pk, "date": str(self.day), "units": str(hours), **kwargs})

    def amounts(self):
        return list(Record.objects.filter(service=self.service, organization=self.focus, date=self.day, deleted_at=None).order_by("id").values_list("amount", flat=True))

    def test_shared_maximum_and_peer_audit_api_and_legacy_amounts(self):
        self.rate()
        first = self.shift(4)
        other = Employee.objects.exclude(pk=self.employee.pk).first()
        second = self.shift(2, employee=other.pk)
        self.assertEqual(self.amounts(), [Decimal(1000), Decimal(500)])
        self.assertEqual(second.allocation_changes[0]["id"], first.pk)
        self.assertTrue(AuditLog.objects.filter(entity_type="record", entity_id=first.pk, actor__contains="shared-shift").exists())
        self.assertEqual(ShiftEntry.objects.get(pk=first.legacy_id).calculated_amount, 1000)
        self.assertEqual(SyncOutbox.objects.filter(entity_type="shift", entity_id=first.legacy_id).order_by("-id").first().payload["calculatedAmount"], "1000.00")
        data = self.client.get(f"/api/shifts/ledger/records/{first.pk}/").json()
        self.assertEqual(data["daily_group"]["total"], "1500.00")
        self.assertEqual(len(data["daily_group"]["members"]), 2)

    def test_shared_minimum_same_employee_and_no_limits(self):
        rate = self.rate(price=200)
        a, b = self.shift(1), self.shift(1)
        self.assertEqual(self.amounts(), [Decimal(300), Decimal(300)])
        save_record({"units": "2"}, a)
        self.assertEqual(self.amounts(), [Decimal(400), Decimal(200)])
        rate.minimum = rate.maximum = None
        rate.save()
        preview = recalculate(self.day, self.day)
        recalculate(self.day, self.day, preview["fingerprint"])
        self.assertEqual(self.amounts(), [Decimal(400), Decimal(200)])

    def test_rounding_largest_remainder_conserves_kopecks(self):
        self.rate(price=0, minimum=1000, maximum=1000)
        rows = [self.shift(1) for _ in range(3)]
        self.assertEqual(self.amounts(), [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")])
        self.assertEqual(sum(self.amounts()), 1000)
        rows[0].units, rows[1].units, rows[2].units = Decimal(1), Decimal(2), Decimal(3)
        _, total, shares = allocation(rows, Decimal(1), None, Decimal("0.01"))
        self.assertEqual(total, Decimal("0.01"))
        self.assertEqual(shares[rows[2].pk], Decimal("0.01"))

    def test_delete_move_and_tariff_snapshot(self):
        rate = self.rate()
        a, b = self.shift(4), self.shift(2)
        rate.price, rate.minimum, rate.maximum = 999, None, 9999
        rate.save()
        save_record({"units": "3"}, b)
        self.assertEqual(sum(self.amounts()), 1500)
        save_record({"date": str(self.day + timedelta(days=1))}, b)
        self.assertEqual(self.amounts(), [Decimal(1200)])
        self.assertEqual(b.amount, 2997)
        c = self.shift(2)
        changes = delete_record(c)
        self.assertEqual(self.amounts(), [Decimal(1200)])
        self.assertIn(a.pk, [item["id"] for item in changes])
        delete_record(a)
        group = DailyCalculation.objects.get(date=self.day, organization=self.focus, service=self.service)
        self.assertEqual(group.total, 0)

    def test_fixed_services_and_other_groups_remain_independent(self):
        fixed = Service.objects.get(legacy_code="big_admin")
        a, b = self.shift(4, service=fixed.pk), self.shift(2, service=fixed.pk)
        self.assertIsNone(a.daily_calculation_id)
        self.assertEqual(a.amount, b.amount)
        self.assertEqual(a.amount, 1400)
        first = self.shift(4)
        self.shift(4, organization=self.other.pk)
        first.refresh_from_db()
        self.assertEqual(first.amount, 800)

    def test_comment_only_keeps_amount_with_missing_or_changed_rate(self):
        rate = self.rate()
        a, b = self.shift(4), self.shift(2)
        group = DailyCalculation.objects.get(pk=a.daily_calculation_id)
        rate.active = False
        rate.save()
        save_record({**record_dict(a), "description": "Только комментарий", "amount": "99999"}, a)
        self.assertEqual(self.amounts(), [Decimal(1000), Decimal(500)])
        self.assertEqual(a.description, "Только комментарий")
        group.refresh_from_db()
        self.assertEqual(group.price, 300)

    def test_telegram_comments_explicit_default_escape_and_atomic_error(self):
        rows = self.ingest(r"12.04 10:00–14:00 Фокус уборка в Квин \+ помощь + 2 сопр задержались на 20 минут")
        self.assertEqual(rows[0].description, "уборка в Квин + помощь")
        self.assertEqual(rows[1].description, "задержались на 20 минут")
        self.assertEqual(rows[1].organization_id, self.other.pk)
        self.assertEqual(rows[0].organization_id, self.focus.pk)
        self.rate(price=900)
        old_amount = rows[0].amount
        changed = self.ingest("12.04 10:00–14:00 Фокус другой комментарий + 2 сопр новый комментарий", edited=True)
        self.assertEqual(changed[0].amount, old_amount)
        count = Record.objects.count()
        with self.assertRaisesMessage(ValidationError, "Часть 2"):
            self.ingest("10:00–14:00 Фокус комментарий + неизвестная услуга", message_id=2)
        self.assertEqual(Record.objects.count(), count)

    def test_comment_organizations_longest_alias_archive_and_no_default(self):
        branch = Organization.objects.create(name="Фокус Зал", aliases=["фз"])
        rows = parse("10:00–14:00 Фокус Зал уборка Фокус", today=self.day)
        self.assertEqual(rows[0].organization_id, branch.pk)
        self.assertEqual(rows[0].description, "уборка Фокус")
        branch.is_active = False
        branch.save()
        self.service.default_organization = self.focus
        self.service.save()
        with self.assertRaisesMessage(ValidationError, "отключена"):
            parse("10:00–14:00 фз комментарий", today=self.day)
        row = parse("10:00–14:00 неизвестное название\nи пояснение", today=self.day)[0]
        self.assertEqual(row.organization_id, self.focus.pk)
        self.assertEqual(row.description, "неизвестное название и пояснение")
        self.service.default_organization = None
        self.service.save()
        with self.assertRaises(ValidationError):
            parse("10:00–14:00 пояснение", today=self.day)

    def test_history_requires_preview_and_conversion_without_amount_change(self):
        self.rate(price=200)
        a = Record.objects.create(kind="service", service=self.service, organization=self.focus, employee=self.employee, date=self.day, units=4, amount=777)
        b = Record.objects.create(kind="service", service=self.service, organization=self.focus, employee=self.employee, date=self.day, units=2, amount=999)
        with self.assertRaisesMessage(ValidationError, "исторические"):
            self.shift(1)
        self.assertEqual(self.amounts(), [Decimal(777), Decimal(999)])
        save_record({"description": "Архивный комментарий"}, a)
        self.assertEqual(a.amount, 777)
        preview = recalculate(self.day, self.day)
        self.assertEqual(preview["groups"][0]["total"], "1200.00")
        recalculate(self.day, self.day, preview["fingerprint"])
        self.assertEqual(self.amounts(), [Decimal(800), Decimal(400)])
        self.shift(1)
        self.assertEqual(sum(self.amounts()), 1400)

    def test_recalculation_fingerprint_tracks_comments_and_tariffs(self):
        rate = self.rate()
        a = self.shift(4)
        preview = recalculate(self.day, self.day)
        save_record({"description": "Новое"}, a)
        with self.assertRaisesMessage(ValidationError, "Данные изменились"):
            recalculate(self.day, self.day, preview["fingerprint"])
        preview = recalculate(self.day, self.day)
        rate.price = 301
        rate.save()
        with self.assertRaises(ValidationError):
            recalculate(self.day, self.day, preview["fingerprint"])

    def test_explicit_recalculation_changes_method_and_can_reactivate_daily_group(self):
        rate = self.rate()
        self.shift(4)
        self.shift(2)
        rate.calculation, rate.price = "fixed", 700
        rate.save()
        preview = recalculate(self.day, self.day)
        recalculate(self.day, self.day, preview["fingerprint"])
        self.assertEqual(self.amounts(), [Decimal(700), Decimal(700)])
        self.assertFalse(DailyCalculation.objects.get().active)
        rate.calculation, rate.price = "hourly", 300
        rate.save()
        with self.assertRaises(ValidationError):
            self.shift(1)
        preview = recalculate(self.day, self.day)
        recalculate(self.day, self.day, preview["fingerprint"])
        self.assertEqual(self.amounts(), [Decimal(1000), Decimal(500)])
        self.assertEqual(DailyCalculation.objects.count(), 1)
        self.assertTrue(DailyCalculation.objects.get().active)

    def test_mark_amount_and_oneoff_comments_keep_service_words_as_text(self):
        cleaning = Service.objects.get(legacy_code="cleaning")
        cleaning.default_organization = self.focus
        cleaning.save()
        amount = Service.objects.create(name="Допработа", aliases=["доп"], input_type="amount", default_organization=self.focus)
        for message in ("уборка Фокус сопр, телефоны + 2 сопр Фокус уборка", "уборка всё готово"):
            rows = parse(message, today=self.day)
            self.assertEqual(rows[0].service, cleaning)
            self.assertTrue(rows[0].description)
        for message in ("доп 1500,50 Фокус заменил ручку", "1500,50 доп заменил ручку"):
            row = parse(message, today=self.day)[0]
            self.assertEqual(row.service, amount)
            self.assertEqual(row.amount, Decimal("1500.50"))
            self.assertEqual(row.description, "заменил ручку")
        row = parse("12.04, 1500,50, дверь, замок + ручка, Фокус", today=self.day)[0]
        self.assertEqual(row.description, "дверь, замок + ручка")
        expense = parse("12.04, краска + кисть, 1500,50, Фокус", expense=True, today=self.day)[0]
        self.assertEqual(expense.description, "краска + кисть")

    def test_frozen_group_blocks_new_parts_telegram_and_old_api(self):
        self.rate()
        a = self.shift(4)
        batch = prepare(self.day, self.day, [self.focus.pk])
        approve(batch.pk, batch.version)
        artifact = bytes(batch.invoices.get().artifact)
        with self.assertRaises(ValidationError):
            self.shift(2)
        with self.assertRaises(ValidationError):
            self.ingest("10:00–12:00 Фокус")
        with self.assertRaises(ValidationError):
            ShiftEntry.objects.create(date=self.day, organization=self.focus, employee=self.employee, work_type="small_admin", hours=2)
        self.assertEqual(Record.objects.count(), 1)
        self.assertEqual(ShiftEntry.objects.count(), 1)
        b = save_record({"kind":"service", "service":self.service.pk, "organization":self.focus.pk, "employee":self.employee.pk, "date":str(self.day), "units":"2"}, allow_frozen=True)
        self.assertEqual(self.amounts(), [Decimal(1000), Decimal(500)])
        self.assertEqual(bytes(batch.invoices.get().artifact), artifact)
        with self.assertRaises(ValidationError):
            save_record({"description": "Комментарий к новой части"}, b)
        save_record({"description": "Разрешённая корректировка"}, b, allow_frozen=True)
        replacement = prepare(self.day, self.day, [self.focus.pk], replaces=batch.pk)
        self.assertEqual(replacement.invoices.get().replaces_id, batch.invoices.get().pk)

    def test_legacy_api_uses_shared_calculation_and_comment_does_not_require_old_rate(self):
        self.rate()
        def payload(hours):
            return {"kind":"shift", "date":str(self.day), "organizationId":self.focus.pk, "employeeId":self.employee.pk, "workType":"small_admin", "hours":str(hours)}
        a = self.client.post("/api/shifts/entries/", json.dumps(payload(4)), content_type="application/json")
        b = self.client.post("/api/shifts/entries/", json.dumps(payload(2)), content_type="application/json")
        self.assertEqual(a.status_code, 201, a.content)
        self.assertEqual(b.status_code, 201, b.content)
        self.assertEqual(self.amounts(), [Decimal(1000), Decimal(500)])
        PayRule.objects.filter(organization=self.focus, code="small_admin").delete()
        updated = self.client.put(f"/api/shifts/entries/shift/{a.json()['id']}/", json.dumps({**payload(4), "comment":"Из старого интерфейса"}), content_type="application/json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(self.amounts(), [Decimal(1000), Decimal(500)])

    def test_night_overlap_composite_same_group_and_duplicates(self):
        self.rate()
        rows = self.ingest("12.04 22:00–02:00 Фокус первая + 22:00–00:00 Фокус вторая")
        self.assertEqual([r.amount for r in rows], [Decimal(1000), Decimal(500)])
        self.assertEqual([r.units for r in rows], [Decimal(4), Decimal(2)])
        self.ingest("12.04 22:00–02:00 Фокус первая + 22:00–00:00 Фокус вторая")
        self.assertEqual(Record.objects.count(), 2)
        self.ingest("12.04 22:00–02:00 Фокус осталась одна", edited=True)
        self.assertEqual(self.amounts(), [Decimal(1200)])

    def test_excel_and_employee_totals_have_shares_once_and_comment_invalidates_draft(self):
        self.rate()
        a, b = self.shift(4, description="Подменил коллегу"), self.shift(2)
        batch = prepare(self.day, self.day, [self.focus.pk])
        invoice = batch.invoices.get()
        self.assertEqual(invoice.total, 1500)
        self.assertEqual(len(invoice.lines), 2)
        wb = load_workbook(BytesIO(bytes(invoice.artifact)))
        self.assertEqual(wb["Основной"]["B3"].value, 1500)
        self.assertIn("Подменил коллегу", str(list(wb["Основной"].values)))
        summary = self.client.get("/api/shifts/ledger/records/", {"month":"2026-04", "kind":"work"}).json()
        self.assertEqual(sum(Decimal(item["total"]) for item in summary["employee_totals"]), 1500)
        save_record({"description":"Уточнение"}, a)
        result = approve(batch.pk, batch.version)
        self.assertFalse(result["approved"])
