"""Read-only-to-production audit: all probes run in an isolated in-memory database.

Run from repository root: .venv/Scripts/python.exe audit/2026-09-12/probe_backend.py
Each probe rolls back its own writes. No Telegram or external sync is invoked.
"""
import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update(DJANGO_SETTINGS_MODULE="config.settings", DJANGO_DB_ENGINE="django.db.backends.sqlite3",
                  DJANGO_DB_NAME=":memory:", DJANGO_ALLOWED_HOSTS="testserver,localhost,127.0.0.1",
                  TELEGRAM_BOT_TOKEN="", SHIFT_SYNC_ENDPOINT="", SHIFT_SYNC_AFTER_WRITE="0")
import django
django.setup()
from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.contrib import admin
from django.test import RequestFactory
from apps.ledger.models import Record, Service, Rate, OrganizationBilling, TelegramContact, Invoice, DailyCalculation
from apps.ledger.engine import save_record, record_dict, recalculate
from apps.ledger.billing import prepare, approve, delete_batch, schedule
from apps.shifts.models import Organization, Employee, CompanionEntry, AuditLog

logging.disable(logging.CRITICAL)
call_command("migrate", verbosity=0)
client = Client(raise_request_exception=False)
org = Organization.objects.get(name="Фокус")
employee = Employee.objects.first()
day = date(2026, 4, 12)
hourly = Service.objects.get(legacy_code="small_admin")
companion = Service.objects.get(legacy_code="companion")

def emit(name, **result):
    print(json.dumps({"probe": name, **result}, ensure_ascii=False, default=str))

@contextmanager
def isolated(name):
    try:
        with transaction.atomic():
            yield
            transaction.set_rollback(True)
    except Exception as error:
        emit(name, exception=type(error).__name__, message=str(error))

def api(path, payload, method="post"):
    return getattr(client, method)("/api/shifts/ledger/" + path, json.dumps(payload), content_type="application/json")

def oneoff(amount="100"):
    return save_record(dict(kind="oneoff", date=str(day), organization=org.pk, employee=employee.pk,
                            amount=amount, description="Audit work"))

def recipients():
    contact = TelegramContact.objects.create(user_id=99001, name="Audit recipient")
    OrganizationBilling.objects.get(organization=org).recipients.add(contact)

with isolated("service_form_blank_amount"):
    payload = dict(kind="service", date=str(day), organization=org.pk, employee=employee.pk,
                   service=hourly.pk, units=1, start_time="10:00", end_time="14:00", amount="")
    response = api("records/", payload)
    emit("service_form_blank_amount", status=response.status_code, body=response.json())
    del payload["amount"]
    response = api("records/", payload)
    emit("service_form_amount_omitted_control", status=response.status_code, amount=response.json().get("amount"))

with isolated("fractional_companion"):
    response = api("records/", dict(kind="service", date=str(day), organization=org.pk,
                   employee=employee.pk, service=companion.pk, units="1.5", amount="0"))
    result = response.json()
    legacy = CompanionEntry.objects.get(pk=result["legacy_id"]) if response.status_code == 200 else None
    emit("fractional_companion", status=response.status_code, units=result.get("units"),
         ledger_amount=result.get("amount"), legacy_count=legacy.count if legacy else None)

with isolated("negative_ready_amount"):
    service = Service.objects.create(name="Audit amount", input_type="amount")
    Rate.objects.create(service=service, organization=org, start=day, calculation="amount", price=0)
    response = api("records/", dict(kind="service", date=str(day), organization=org.pk,
                   employee=employee.pk, service=service.pk, units="1", amount="-500"))
    emit("negative_ready_amount", status=response.status_code, amount=response.json().get("amount"), review=response.json().get("review"))

with isolated("lost_update"):
    record = oneoff()
    stale = record_dict(record)
    save_record({"amount":"200"}, record)
    stale["description"] = "Only editing a comment from another open tab"
    response = api(f"records/{record.pk}/", stale, "put")
    emit("lost_update", status=response.status_code, expected_preserved_amount="200.00", actual=response.json().get("amount"))

with isolated("change_service_input_with_hourly_rates"):
    response = api(f"services/{hourly.pk}/", {"input_type":"quantity"}, "put")
    emit("change_service_input_with_hourly_rates", status=response.status_code, input_type=response.json().get("input_type"),
         existing_calculations=list(Rate.objects.filter(service=hourly).values_list("calculation", flat=True)))

with isolated("malformed_json_shapes"):
    for payload in ([], None, {"description":None}):
        response = api("records/", payload)
        error_type = response.exc_info[0].__name__ if response.exc_info else None
        emit("malformed_json_shapes", payload=payload, status=response.status_code, error_type=error_type)

with isolated("invalid_rate_foreign_key"):
    response = api("rates/", {"service":999999, "organization":org.pk, "start":str(day), "price":"100", "calculation":"hourly"})
    emit("invalid_rate_foreign_key", status=response.status_code, body=response.json())

with isolated("delete_middle_invoice_version"):
    recipients()
    oneoff()
    a = prepare(day, day, [org.pk]); approve(a.pk, a.version)
    b = prepare(day, day, [org.pk], replaces=a.pk); approve(b.pk, b.version)
    c = prepare(day, day, [org.pk], replaces=b.pk); approve(c.pk, c.version)
    delete_batch(b.pk)
    d = prepare(day, day, [org.pk], replaces=c.pk)
    try:
        result = approve(d.pk, d.version)
        emit("delete_middle_invoice_version", result=result)
    except Exception as error:
        emit("delete_middle_invoice_version", exception=type(error).__name__, message=str(error),
             remaining_chain=list(Invoice.objects.values("id", "replaces_id")))

with isolated("admin_bypasses_engine"):
    record = save_record(dict(kind="service", service=hourly.pk, organization=org.pk, employee=employee.pk,
                             date=str(day), units="4"))
    original = record.amount
    before_audit = AuditLog.objects.filter(entity_type="record", entity_id=record.pk).count()
    record.units = 8
    admin.site._registry[Record].save_model(RequestFactory().post("/admin/"), record, form=None, change=True)
    group = DailyCalculation.objects.get(pk=record.daily_calculation_id)
    emit("admin_bypasses_engine", original_amount=original, units=record.units, amount=record.amount,
         group_hours=group.hours, record_audit_events_added=AuditLog.objects.filter(entity_type="record", entity_id=record.pk).count()-before_audit)

with isolated("journal_query_count"):
    for n in range(50):
        save_record(dict(kind="service", service=hourly.pk, organization=org.pk, employee=employee.pk,
                         date=str(day), units="1"))
    connection.queries_log.clear()
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/shifts/ledger/records/?month=2026-04&limit=50")
    emit("journal_query_count", status=response.status_code, rows=len(response.json()["records"]),
         sql_queries=len(queries), response_bytes=len(response.content))

with isolated("employee_name_split"):
    oneoff()
    employee.short_name = "Audit renamed employee"
    employee.save()
    oneoff()
    response = client.get("/api/shifts/ledger/records/?month=2026-04")
    emit("employee_name_split", totals=response.json()["employee_totals"])

with isolated("legacy_excel_formula"):
    from apps.shifts.reports import generate_month_report
    record = save_record(dict(kind="service", service=hourly.pk, organization=org.pk, employee=employee.pk,
                             date=str(day), units="4", description="=1+1"))
    workbook = generate_month_report(2026, 4)
    cells = [{"sheet": ws.title, "cell": cell.coordinate, "type": cell.data_type, "value": cell.value}
             for ws in workbook for row in ws for cell in row if cell.value == "=1+1"]
    emit("legacy_excel_formula", cells=cells)
