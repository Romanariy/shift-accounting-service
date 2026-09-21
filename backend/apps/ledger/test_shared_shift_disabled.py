from datetime import date
from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.test import TestCase, override_settings

from apps.shifts.models import AuditLog, Employee, Organization
from .engine import recalculate, save_record
from .models import DailyCalculation, Rate, Record, Service


@override_settings(SHARED_SHIFT_ALLOCATION_ENABLED=False, SHIFT_SYNC_AFTER_WRITE=False)
class DisabledSharedShiftTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Без деления")
        self.first_employee = Employee.objects.create(short_name="Первый")
        self.second_employee = Employee.objects.create(short_name="Второй")
        self.service = Service.objects.create(name="Независимая смена", input_type="time")
        Rate.objects.create(service=self.service, organization=self.organization, calculation="hourly",
                            price=300, minimum=600, maximum=1500, start=date(2026, 1, 1))
        self.day = date(2026, 4, 12)

    def payload(self, employee, hours):
        return {"kind": "service", "service": self.service.pk, "organization": self.organization.pk,
                "employee": employee.pk, "date": str(self.day), "units": str(hours)}

    def test_new_records_are_calculated_independently(self):
        first = save_record(self.payload(self.first_employee, 4))
        second = save_record(self.payload(self.second_employee, 2))

        first.refresh_from_db()
        self.assertEqual((first.amount, second.amount), (Decimal("1200.00"), Decimal("600.00")))
        self.assertIsNone(first.daily_calculation_id)
        self.assertIsNone(second.daily_calculation_id)
        self.assertEqual(second.allocation_changes, [])
        self.assertIsNone(self.client.get(f"/api/shifts/ledger/records/{first.pk}/").json()["daily_group"])
        self.assertFalse(DailyCalculation.objects.exists())

    def test_editing_one_record_does_not_change_the_other(self):
        first = save_record(self.payload(self.first_employee, 4))
        second = save_record(self.payload(self.second_employee, 2))
        save_record({"units": "3"}, first)

        second.refresh_from_db()
        self.assertEqual(first.amount, Decimal("900.00"))
        self.assertEqual(second.amount, Decimal("600.00"))
        self.assertEqual(first.allocation_changes, [])

    @override_settings(SHARED_SHIFT_ALLOCATION_ENABLED=True)
    def create_shared_records(self):
        first = save_record(self.payload(self.first_employee, 4))
        second = save_record(self.payload(self.second_employee, 2))
        return first.pk, second.pk

    def test_explicit_recalculation_can_detach_historical_group(self):
        first_id, second_id = self.create_shared_records()
        self.assertTrue(DailyCalculation.objects.filter(active=True).exists())

        preview = recalculate(self.day, self.day)
        self.assertFalse(preview["groups"][0]["hourly"])
        recalculate(self.day, self.day, preview["fingerprint"])

        first, second = Record.objects.filter(pk__in=(first_id, second_id)).order_by("pk")
        self.assertEqual((first.amount, second.amount), (Decimal("1200.00"), Decimal("600.00")))
        self.assertIsNone(first.daily_calculation_id)
        self.assertIsNone(second.daily_calculation_id)
        self.assertFalse(DailyCalculation.objects.get().active)

    def test_data_migration_recalculates_existing_shared_records(self):
        first_id, second_id = self.create_shared_records()
        DailyCalculation.objects.update(active=False)

        migration = import_module("apps.ledger.migrations.0008_recalculate_shared_shifts_independently")
        migration.recalculate(apps, None)

        first, second = Record.objects.filter(pk__in=(first_id, second_id)).order_by("pk")
        self.assertEqual((first.amount, second.amount), (Decimal("1200.00"), Decimal("600.00")))
        self.assertIsNone(first.daily_calculation_id)
        self.assertIsNone(second.daily_calculation_id)
        self.assertFalse(DailyCalculation.objects.get().active)
        self.assertEqual(
            AuditLog.objects.filter(actor="migration:independent-shifts", entity_type="record").count(),
            2,
        )

    def test_bootstrap_reports_temporary_switch(self):
        response = self.client.get("/api/shifts/ledger/bootstrap/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["features"]["shared_shift_allocation"])
