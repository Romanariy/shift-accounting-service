import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase


class AccrualEmployeeMigrationTests(TestCase):
    def test_service_assignment_moves_to_schedules_without_changing_records(self):
        manage = Path(__file__).resolve().parents[2] / "manage.py"
        with TemporaryDirectory(prefix="accrual-employee-") as folder:
            env = {**os.environ,"DJANGO_DB_ENGINE":"django.db.backends.sqlite3","DJANGO_DB_NAME":str(Path(folder)/"migration.sqlite3"),"SHIFT_SYNC_ENDPOINT":""}
            def run(*args):
                result = subprocess.run([sys.executable,str(manage),*args],env=env,capture_output=True,text=True,encoding="utf-8",errors="replace")
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            run("migrate","ledger","0005","--noinput")
            run("shell","-c","""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from datetime import date
apps = MigrationExecutor(connection).loader.project_state([('ledger','0005_service_default_employee')]).apps
Employee, Organization, Service, Accrual, Record = [apps.get_model(app,model) for app,model in [('shifts','Employee'),('shifts','Organization'),('ledger','Service'),('ledger','Accrual'),('ledger','Record')]]
employee=Employee.objects.first()
service=Service.objects.create(name='Migration assignment',frequency='daily',default_employee=employee)
for org in Organization.objects.all()[:2]:
    Accrual.objects.create(service=service,organization=org,start=date(2026,4,1))
Record.objects.create(kind='service',service=service,date=date(2026,4,1),amount='123.45',employee_name='Historical')
""")
            run("migrate","--noinput")
            run("shell","-c","""
from decimal import Decimal
from apps.shifts.models import Employee
from apps.ledger.models import Accrual, Record, Service
service=Service.objects.get(name='Migration assignment')
assert set(Accrual.objects.filter(service=service).values_list('employee_id',flat=True))=={Employee.objects.first().pk}
assert Accrual.objects.filter(service=service).count()==2
row=Record.objects.get(service=service)
assert row.employee_id is None and row.employee_name=='Historical' and row.amount==Decimal('123.45')
assert not hasattr(service,'default_employee_id')
""")
