"""Run against PostgreSQL: manage.py test apps.ledger.test_shared_concurrency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from apps.shifts.models import Employee, Organization
from .engine import save_record
from .ingest import ingest
from .models import DailyCalculation, Rate, Record, Service, Settings


@skipUnless(connection.vendor == "postgresql", "Row-lock concurrency requires PostgreSQL")
@override_settings(SHIFT_SYNC_AFTER_WRITE=False, SHIFT_SYNC_ENDPOINT="")
class SharedConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Параллельная студия", aliases=["параллельная"])
        self.employee = Employee.objects.create(short_name="Параллельный", telegram_user_id=987654321)
        self.service = Service.objects.create(name="Параллельная смена", aliases=["паралл"], input_type="time", default_organization=self.org)
        Rate.objects.create(service=self.service, organization=self.org, calculation="hourly", price=300, maximum=1500, start=date(2026, 1, 1))
        Settings.objects.get_or_create(pk=1)

    def parallel(self, action):
        barrier = Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action(index)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, [0, 1]))

    def test_simultaneous_parts_share_one_cap(self):
        def action(index):
            return save_record({"kind":"service", "service":self.service.pk, "organization":self.org.pk,
                                "employee":self.employee.pk, "date":"2026-04-12", "units":"4" if index == 0 else "2"}).pk
        ids = self.parallel(action)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(DailyCalculation.objects.count(), 1)
        self.assertEqual(sorted(Record.objects.values_list("amount", flat=True)), [Decimal(500), Decimal(1000)])

    def test_simultaneous_telegram_retry_creates_one_record(self):
        def action(index):
            return ingest("12.04 паралл 10:00–14:00", chat_id=-123456789, message_id=1,
                          user_id=self.employee.telegram_user_id, today=date(2026, 4, 12))[0].pk
        ids = self.parallel(action)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Record.objects.count(), 1)
        self.assertEqual(DailyCalculation.objects.count(), 1)
