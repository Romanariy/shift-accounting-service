import json
from datetime import date, datetime, time
from decimal import Decimal

from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .audit import log_change
from .constants import EntrySource, EntryStatus, SyncStatus
from .ingest import ingest_telegram_message
from .models import AuditLog, CompanionEntry, Employee, PayRule, ShiftEntry, SyncOutbox
from .parser import ParseError, calculate_hours
from .payments import calculate_companion_amount, calculate_shift_amount
from .reports import build_month_summary, generate_month_report
from .serializers import (
    audit_to_dict,
    companion_to_dict,
    employee_to_dict,
    pay_rule_to_dict,
    shift_to_dict,
)
from .sync import queue_sync_change, try_sync_once


def json_body(request):
    if not request.body:
        return {}

    return json.loads(request.body.decode("utf-8"))


def api_error(message, status=400):
    return JsonResponse({"error": message}, status=status, json_dumps_params={"ensure_ascii": False})


def parse_date(value, field="date"):
    if not value:
        raise ValueError(f"Поле {field} обязательно.")
    return date.fromisoformat(value)


def parse_optional_date(value):
    return date.fromisoformat(value) if value else None


def parse_time(value):
    if not value:
        return None
    return time.fromisoformat(value)


def decimal_or_zero(value):
    if value in (None, ""):
        return Decimal("0.00")
    return Decimal(str(value))


def int_or_zero(value):
    if value in (None, ""):
        return 0
    return int(value)


def get_month_params(request):
    today = timezone.localdate()
    return int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month))


def month_bounds(year, month):
    from calendar import monthrange

    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def apply_entry_filters(queryset, request):
    year = request.GET.get("year")
    month = request.GET.get("month")
    date_from = request.GET.get("dateFrom")
    date_to = request.GET.get("dateTo")
    employee_id = request.GET.get("employeeId")
    status = request.GET.get("status")

    if year and month:
        start, end = month_bounds(int(year), int(month))
        queryset = queryset.filter(date__range=(start, end))
    elif date_from and date_to:
        queryset = queryset.filter(date__range=(parse_date(date_from), parse_date(date_to)))

    if employee_id:
        queryset = queryset.filter(employee_id=employee_id)

    if status:
        queryset = queryset.filter(status=status)

    return queryset.filter(deleted_at__isnull=True)


def update_employee_from_payload(employee, payload):
    employee.short_name = payload.get("shortName", employee.short_name).strip()
    employee.full_name = payload.get("fullName", employee.full_name).strip()
    employee.telegram_username = payload.get("telegramUsername", employee.telegram_username).strip().lstrip("@")
    telegram_user_id = payload.get("telegramUserId", employee.telegram_user_id)
    employee.telegram_user_id = int(telegram_user_id) if telegram_user_id not in (None, "") else None
    employee.aliases = payload.get("aliases", employee.aliases or [])
    employee.default_work_type = payload.get("defaultWorkType", employee.default_work_type)
    employee.is_active = bool(payload.get("isActive", employee.is_active))
    employee.sort_order = int(payload.get("sortOrder", employee.sort_order))
    employee.save()
    return employee


def update_pay_rule_from_payload(rule, payload):
    rule.code = payload.get("code", rule.code)
    rule.title = payload.get("title", rule.title).strip()
    rule.calculation_type = payload.get("calculationType", rule.calculation_type)
    rule.hourly_rate = payload.get("hourlyRate") or None
    rule.fixed_amount = payload.get("fixedAmount") or None
    rule.min_amount = payload.get("minAmount") or None
    rule.max_amount = payload.get("maxAmount") or None
    rule.active_from = parse_date(payload.get("activeFrom"), "activeFrom")
    rule.active_to = parse_optional_date(payload.get("activeTo"))
    rule.is_active = bool(payload.get("isActive", rule.is_active))
    rule.save()
    return rule


def resolve_employee(payload):
    employee_id = payload.get("employeeId")
    if not employee_id:
        return None
    return Employee.objects.filter(id=employee_id).first()


def fill_common_entry(entry, payload, employee):
    entry.date = parse_date(payload.get("date"))
    entry.employee = employee
    entry.employee_name_snapshot = employee.display_name if employee else payload.get("employeeName", "")
    entry.comment = payload.get("comment", "")
    entry.status = payload.get("status", EntryStatus.CONFIRMED)
    entry.source = payload.get("source", EntrySource.MANUAL)
    entry.sync_status = SyncStatus.PENDING
    return entry


def update_shift_from_payload(entry, payload):
    employee = resolve_employee(payload)
    fill_common_entry(entry, payload, employee)
    entry.work_type = payload.get("workType") or (employee.default_work_type if employee else "")
    entry.start_time = parse_time(payload.get("startTime"))
    entry.end_time = parse_time(payload.get("endTime"))

    if payload.get("hours") not in (None, ""):
        entry.hours = decimal_or_zero(payload.get("hours"))
    elif entry.start_time and entry.end_time:
        entry.hours = calculate_hours(entry.start_time, entry.end_time)
    else:
        entry.hours = Decimal("0.00")

    entry.calculated_amount = calculate_shift_amount(entry.work_type, entry.hours, entry.date)
    entry.save()
    return entry


def update_companion_from_payload(entry, payload):
    employee = resolve_employee(payload)
    fill_common_entry(entry, payload, employee)
    entry.count = max(1, int_or_zero(payload.get("count", 1)))
    entry.calculated_amount = calculate_companion_amount(entry.count, entry.date)
    entry.save()
    return entry


def queue_after_write(entity_type, entry, action, payload):
    queue_sync_change(entity_type, entry.id, action, payload)
    try_sync_once(limit=10)


@csrf_exempt
def employees_api(request, employee_id=None):
    if request.method == "GET":
        if employee_id:
            employee = Employee.objects.filter(id=employee_id).first()
            if not employee:
                raise Http404
            return JsonResponse(employee_to_dict(employee), json_dumps_params={"ensure_ascii": False})

        employees = Employee.objects.all().order_by("sort_order", "short_name")
        return JsonResponse(
            {"employees": [employee_to_dict(employee) for employee in employees]},
            json_dumps_params={"ensure_ascii": False},
        )

    if request.method == "POST":
        payload = json_body(request)
        employee = update_employee_from_payload(Employee(), payload)
        after = employee_to_dict(employee)
        log_change("employee", employee.id, "create", actor="web", after=after)
        return JsonResponse(after, status=201, json_dumps_params={"ensure_ascii": False})

    if request.method == "PUT" and employee_id:
        payload = json_body(request)
        employee = Employee.objects.get(id=employee_id)
        before = employee_to_dict(employee)
        update_employee_from_payload(employee, payload)
        after = employee_to_dict(employee)
        log_change("employee", employee.id, "update", actor="web", before=before, after=after)
        return JsonResponse(after, json_dumps_params={"ensure_ascii": False})

    if request.method == "DELETE" and employee_id:
        employee = Employee.objects.get(id=employee_id)
        before = employee_to_dict(employee)
        employee.is_active = False
        employee.save(update_fields=("is_active", "updated_at"))
        after = employee_to_dict(employee)
        log_change("employee", employee.id, "delete", actor="web", before=before, after=after)
        return JsonResponse(after, json_dumps_params={"ensure_ascii": False})

    return api_error("Метод не поддерживается.", status=405)


@csrf_exempt
def pay_rules_api(request, rule_id=None):
    if request.method == "GET":
        if rule_id:
            rule = PayRule.objects.filter(id=rule_id).first()
            if not rule:
                raise Http404
            return JsonResponse(pay_rule_to_dict(rule), json_dumps_params={"ensure_ascii": False})

        rules = PayRule.objects.all().order_by("code", "-active_from")
        return JsonResponse(
            {"payRules": [pay_rule_to_dict(rule) for rule in rules]},
            json_dumps_params={"ensure_ascii": False},
        )

    if request.method == "POST":
        payload = json_body(request)
        rule = update_pay_rule_from_payload(PayRule(), payload)
        after = pay_rule_to_dict(rule)
        log_change("pay_rule", rule.id, "create", actor="web", after=after)
        return JsonResponse(after, status=201, json_dumps_params={"ensure_ascii": False})

    if request.method == "PUT" and rule_id:
        payload = json_body(request)
        rule = PayRule.objects.get(id=rule_id)
        before = pay_rule_to_dict(rule)
        update_pay_rule_from_payload(rule, payload)
        after = pay_rule_to_dict(rule)
        log_change("pay_rule", rule.id, "update", actor="web", before=before, after=after)
        return JsonResponse(after, json_dumps_params={"ensure_ascii": False})

    if request.method == "DELETE" and rule_id:
        rule = PayRule.objects.get(id=rule_id)
        before = pay_rule_to_dict(rule)
        rule.is_active = False
        rule.save(update_fields=("is_active", "updated_at"))
        after = pay_rule_to_dict(rule)
        log_change("pay_rule", rule.id, "delete", actor="web", before=before, after=after)
        return JsonResponse(after, json_dumps_params={"ensure_ascii": False})

    return api_error("Метод не поддерживается.", status=405)


@csrf_exempt
def entries_api(request):
    if request.method == "GET":
        shifts = apply_entry_filters(
            ShiftEntry.objects.select_related("employee").order_by("-date", "-created_at"),
            request,
        )
        companions = apply_entry_filters(
            CompanionEntry.objects.select_related("employee").order_by("-date", "-created_at"),
            request,
        )
        work_type = request.GET.get("workType")

        if work_type:
            shifts = shifts.filter(work_type=work_type)

        year, month = get_month_params(request)
        return JsonResponse(
            {
                "shifts": [shift_to_dict(entry) for entry in shifts[:500]],
                "companions": [companion_to_dict(entry) for entry in companions[:500]],
                "summary": build_month_summary(year, month),
            },
            json_dumps_params={"ensure_ascii": False},
        )

    if request.method == "POST":
        payload = json_body(request)
        kind = payload.get("kind", "shift")

        if kind == "companion":
            entry = update_companion_from_payload(CompanionEntry(), payload)
            after = companion_to_dict(entry)
            log_change("companion", entry.id, "create", actor="web", after=after)
            queue_after_write("companion", entry, "create", after)
            return JsonResponse(after, status=201, json_dumps_params={"ensure_ascii": False})

        entry = update_shift_from_payload(ShiftEntry(), payload)
        after = shift_to_dict(entry)
        log_change("shift", entry.id, "create", actor="web", after=after)
        queue_after_write("shift", entry, "create", after)
        return JsonResponse(after, status=201, json_dumps_params={"ensure_ascii": False})

    return api_error("Метод не поддерживается.", status=405)


@csrf_exempt
def entry_detail_api(request, kind, entry_id):
    model = CompanionEntry if kind == "companion" else ShiftEntry
    serializer = companion_to_dict if kind == "companion" else shift_to_dict
    updater = update_companion_from_payload if kind == "companion" else update_shift_from_payload
    entity_type = "companion" if kind == "companion" else "shift"
    entry = model.objects.filter(id=entry_id).first()

    if not entry:
        raise Http404

    if request.method == "GET":
        return JsonResponse(serializer(entry), json_dumps_params={"ensure_ascii": False})

    if request.method == "PUT":
        payload = json_body(request)
        before = serializer(entry)
        updater(entry, payload)
        after = serializer(entry)
        log_change(entity_type, entry.id, "update", actor="web", before=before, after=after)
        queue_after_write(entity_type, entry, "update", after)
        return JsonResponse(after, json_dumps_params={"ensure_ascii": False})

    if request.method == "DELETE":
        before = serializer(entry)
        entry.deleted_at = timezone.now()
        entry.sync_status = SyncStatus.PENDING
        entry.save(update_fields=("deleted_at", "sync_status", "updated_at"))
        after = serializer(entry)
        log_change(entity_type, entry.id, "delete", actor="web", before=before, after=after)
        queue_after_write(entity_type, entry, "delete", after)
        return JsonResponse(after, json_dumps_params={"ensure_ascii": False})

    return api_error("Метод не поддерживается.", status=405)


@csrf_exempt
def telegram_ingest_api(request):
    if request.method != "POST":
        return api_error("Метод не поддерживается.", status=405)

    payload = json_body(request)
    try:
        result = ingest_telegram_message(
            payload.get("text", ""),
            author_username=payload.get("authorUsername", ""),
            author_user_id=payload.get("authorUserId"),
            chat_id=payload.get("chatId"),
            thread_id=payload.get("threadId"),
            message_id=payload.get("messageId"),
            default_year=payload.get("year"),
        )
    except ParseError as error:
        return api_error(str(error))
    return JsonResponse(
        {
            "message": result.message,
            "needsReview": result.needs_review,
            "shift": shift_to_dict(result.shift) if result.shift else None,
            "companion": companion_to_dict(result.companion) if result.companion else None,
        },
        status=201,
        json_dumps_params={"ensure_ascii": False},
    )


def audit_log_api(request):
    logs = AuditLog.objects.all()
    entity_type = request.GET.get("entityType")
    entity_id = request.GET.get("entityId")

    if entity_type:
        logs = logs.filter(entity_type=entity_type)

    if entity_id:
        logs = logs.filter(entity_id=entity_id)

    return JsonResponse(
        {"auditLog": [audit_to_dict(entry) for entry in logs[:100]]},
        json_dumps_params={"ensure_ascii": False},
    )


def month_summary_api(request):
    year, month = get_month_params(request)
    return JsonResponse(build_month_summary(year, month), json_dumps_params={"ensure_ascii": False})


def report_xlsx_api(request):
    year, month = get_month_params(request)
    workbook = generate_month_report(year, month)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="shift-report-{year}-{month:02d}.xlsx"'
    workbook.save(response)
    return response


def sync_status_api(_request):
    pending = SyncOutbox.objects.filter(status=SyncStatus.PENDING).count()
    return JsonResponse({"pending": pending}, json_dumps_params={"ensure_ascii": False})
