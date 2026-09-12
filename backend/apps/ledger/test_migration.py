import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase


class HistoryMigrationTests(TestCase):
    def test_import_preserves_amounts_sources_and_legacy_links(self):
        manage = Path(__file__).resolve().parents[2] / "manage.py"
        with TemporaryDirectory(prefix="ledger-history-") as folder:
            env = {**os.environ, "DJANGO_DB_ENGINE":"django.db.backends.sqlite3", "DJANGO_DB_NAME":str(Path(folder)/"history.sqlite3"), "SHIFT_SYNC_ENDPOINT":""}

            def run(*args):
                result = subprocess.run([sys.executable, str(manage), *args], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            run("migrate", "shifts", "0002", "--noinput")
            run("shell", "-c", """
from apps.shifts.models import Employee, Organization, ShiftEntry, CompanionEntry
from django.utils import timezone
e=Employee.objects.first()
o=Organization.objects.first()
s=ShiftEntry.objects.create(date=timezone.localdate(),employee=e,organization=o,work_type='small_admin',hours=4,calculated_amount='777.13',telegram_chat_id=-100,telegram_message_id=42)
c=CompanionEntry.objects.create(date=timezone.localdate(),employee=e,organization=o,count=2,calculated_amount='456.78',telegram_chat_id=-100,telegram_message_id=42)
""")
            run("migrate", "--noinput")
            run("shell", "-c", """
from decimal import Decimal
from apps.ledger.models import DailyCalculation, Record, Service, Settings
rows=list(Record.objects.filter(legacy_id__isnull=False).order_by('part'))
assert len(rows)==2
assert rows[0].amount==Decimal('777.13')
assert rows[1].amount==Decimal('456.78')
assert rows[0].chat_id==-100 and rows[1].message_id==42
assert rows[0].part==0 and rows[1].part==1
assert all(r.service.sheet=='' for r in rows)
assert Service.objects.count()==6
assert not Settings.objects.get(pk=1).enabled
assert not DailyCalculation.objects.exists()
assert all(r.calculation_version == 0 and r.daily_calculation_id is None for r in rows)
""")
