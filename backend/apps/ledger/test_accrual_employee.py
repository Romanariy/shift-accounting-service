import json
from datetime import date, datetime, timezone

from django.test import TestCase

from apps.shifts.models import Employee, Organization
from .earnings import report_data
from .engine import accrue, save_record
from .models import Accrual, Rate, Record, Service


class AccrualEmployeeTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.first()
        self.other = Employee.objects.exclude(pk=self.employee.pk).first()
        self.org = Organization.objects.first()
        self.service = Service.objects.create(name="Автоуслуга", input_type="mark", frequency="daily")
        Rate.objects.create(service=self.service, organization=self.org, start=date(2026,4,1), price=100)
        self.accrual = Accrual.objects.create(service=self.service, organization=self.org, employee=self.employee, start=date(2026,4,1))

    def test_daily_all_organizations_and_earnings(self):
        other_org = Organization.objects.exclude(pk=self.org.pk).first()
        Rate.objects.create(service=self.service, organization=other_org, start=date(2026,4,1), price=100)
        Accrual.objects.create(service=self.service, organization=other_org, employee=self.other, start=date(2026,4,1))
        accrue(date(2026,4,3))
        accrue(date(2026,4,3))
        rows = Record.objects.filter(service=self.service)
        self.assertEqual(rows.count(), 6)
        self.assertEqual(set(rows.values_list("employee_id", flat=True)), {self.employee.pk,self.other.pk})
        self.assertEqual(set(rows.values_list("employee_name", flat=True)), {self.employee.display_name,self.other.display_name})
        self.assertEqual(set(rows.filter(organization=self.org).values_list("employee_id", flat=True)), {self.employee.pk})
        self.assertEqual(set(rows.filter(organization=other_org).values_list("employee_id", flat=True)), {self.other.pk})
        data = report_data("2026-04", datetime(2026,5,1,tzinfo=timezone.utc))
        self.assertEqual(data["total"], "600.00")
        self.assertEqual({r["employee_id"]:r["amount"] for r in data["employees"]}, {self.employee.pk:"300.00",self.other.pk:"300.00"})
        self.assertEqual(data["unassigned"], [])
        self.assertEqual(data["review"], [])

    def test_monthly_assignment(self):
        self.service.frequency = "monthly"
        self.service.save()
        accrue(date(2026,6,10))
        rows = Record.objects.filter(service=self.service)
        self.assertEqual(rows.count(), 3)
        self.assertTrue(all(r.employee_id==self.employee.pk for r in rows))

    def test_change_clear_and_rename_preserve_existing_records(self):
        accrue(date(2026,4,1))
        first = Record.objects.get(service=self.service)
        original_name = first.employee_name
        self.employee.short_name = "Новое имя"
        self.employee.save()
        self.accrual.employee = self.other
        self.accrual.save()
        accrue(date(2026,4,2))
        first.refresh_from_db()
        self.assertEqual((first.employee_id,first.employee_name), (self.employee.pk,original_name))
        self.assertEqual(Record.objects.get(service=self.service,date=date(2026,4,2)).employee_id,self.other.pk)
        self.accrual.employee = None
        self.accrual.save()
        accrue(date(2026,4,3))
        third = Record.objects.get(service=self.service,date=date(2026,4,3))
        self.assertIsNone(third.employee_id)
        self.assertEqual(third.employee_name, "")
        self.assertFalse(third.review)

    def test_inactive_employee_and_missing_rate_require_review(self):
        self.employee.is_active = False
        self.employee.save()
        Rate.objects.filter(service=self.service).delete()
        accrue(date(2026,4,1))
        row = Record.objects.get(service=self.service)
        self.assertEqual(row.employee_id,self.employee.pk)
        self.assertTrue(row.review)
        self.assertIn("отключён",row.error)
        self.assertIn("тарифа",row.error)
        data = report_data("2026-04", datetime(2026,5,1,tzinfo=timezone.utc))
        self.assertEqual(len(data["review"]),1)
        self.assertEqual(data["employees"],[])

    def test_api_assignment_clearing_validation_and_bootstrap(self):
        url = f"/api/shifts/ledger/accruals/{self.accrual.pk}/"
        def update(value):
            return self.client.put(url,json.dumps({"employee":value}),content_type="application/json")
        self.assertEqual(update(self.other.pk).status_code,200)
        accruals = self.client.get("/api/shifts/ledger/bootstrap/").json()["accruals"]
        self.assertEqual(next(s for s in accruals if s["id"]==self.accrual.pk)["employee"],self.other.pk)
        self.assertEqual(update(None).status_code,200)
        self.assertEqual(update(9999999).status_code,400)
        self.other.is_active = False
        self.other.save()
        self.assertEqual(update(self.other.pk).status_code,400)
        self.accrual.refresh_from_db()
        self.assertIsNone(self.accrual.employee_id)

    def test_manual_entry_keeps_explicit_employee(self):
        self.service.frequency = "entry"
        self.service.save()
        record = save_record({"kind":"service","service":self.service.pk,"organization":self.org.pk,
            "employee":self.other.pk,"date":"2026-04-01","amount":0})
        self.assertEqual(record.employee_id,self.other.pk)
