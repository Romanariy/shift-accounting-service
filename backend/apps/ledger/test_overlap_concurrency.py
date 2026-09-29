from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from apps.shifts.models import Employee, Organization
from .engine import save_record
from .models import Rate, Record, Service, Settings


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
@override_settings(SHARED_SHIFT_ALLOCATION_ENABLED=False, SHIFT_SYNC_AFTER_WRITE=False)
class OverlapConcurrencyTests(TransactionTestCase):
    def test_simultaneous_studio_entries_calculate_one_consistent_day(self):
        Settings.objects.get_or_create(pk=1)
        employee=Employee.objects.create(short_name="Параллельный сотрудник")
        service=Service.objects.create(name="Параллельный админ",legacy_code="small_admin")
        orgs=[Organization.objects.create(name=f"Параллельная {i}") for i in range(2)]
        for org in orgs:Rate.objects.create(service=service,organization=org,calculation="hourly",price=200,start=date(2026,1,1))
        barrier=Barrier(2)
        def run(i):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return save_record({"kind":"service","service":service.pk,"organization":orgs[i].pk,"employee":employee.pk,
                                    "date":"2026-04-12","start_time":"10:00","end_time":"18:00" if i==0 else "14:00"}).pk
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(run,[0,1]))
        self.assertEqual(len(set(ids)),2)
        self.assertEqual(Record.objects.get(organization=orgs[0]).amount,1600)
        self.assertEqual(Record.objects.get(organization=orgs[1]).amount,400)
