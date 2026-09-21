import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.utils import timezone

from apps.shifts.audit import json_safe, log_change
from apps.shifts.models import Employee, Organization
from apps.shifts.parser import calculate_hours
from .models import Accrual, Invoice, Rate, Record, Settings


def lock():
    Settings.objects.get_or_create(pk=1)
    return Settings.objects.select_for_update().get(pk=1)


def digest(value):
    return hashlib.sha256(json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def money(value):
    try:
        result = Decimal(str(value).replace(" ", "").replace(",", "."))
        if not result.is_finite() or abs(result) >= Decimal("1000000000000"):
            raise ValueError()
        return result.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Некорректная сумма.")


def rate_for(service_id, organization_id, day):
    rates = list(Rate.objects.filter(service_id=service_id, organization_id=organization_id, active=True, start__lte=day).filter(Q(end__isnull=True) | Q(end__gte=day)))
    if len(rates) != 1:
        raise ValidationError("Нет единственного действующего тарифа для услуги, организации и даты.")
    return rates[0]


def calculate(record):
    if record.kind != "service":
        if record.amount <= 0:
            raise ValidationError("Сумма должна быть положительной.")
        return money(record.amount)
    rate = rate_for(record.service_id, record.organization_id, record.date)
    if rate.calculation == "amount":
        if record.amount <= 0:
            raise ValidationError("Укажите положительную стоимость услуги.")
        amount = record.amount
    elif rate.calculation in ("hourly", "quantity"):
        amount = rate.price * record.units
    else:
        amount = rate.price
    if rate.minimum is not None and amount > 0:
        amount = max(amount, rate.minimum)
    if rate.maximum is not None:
        amount = min(amount, rate.maximum)
    return money(amount)


def record_dict(r, include_group=True):
    result = json_safe(model_to_dict(r))
    result.update(id=r.pk, updated_at=r.updated_at.isoformat(), deleted_at=r.deleted_at.isoformat() if r.deleted_at else None,
                  organization_name=r.organization.name if r.organization_id else "Без организации",
                  service_name=r.service.name if r.service_id else ("Расход" if r.kind == "expense" else "Разовая услуга"),
                  sheet=(r.service.sheet or "Основной") if r.service_id else ("Расходы" if r.kind == "expense" else "Основной"),
                  input_type=r.service.input_type if r.service_id else None)
    if include_group:
        from .grouping import group_info
        result["daily_group"] = group_info(r.daily_calculation) if r.daily_calculation_id else None
    return result


def is_frozen(record):
    # Portable on both SQLite and PostgreSQL; snapshots deliberately outlive source edits.
    return any(any(line.get("id") == record.pk for line in lines) for lines in Invoice.objects.filter(batch__state="approved").values_list("lines", flat=True))


def audit_record(record, before, actor):
    log_change("record", record.pk, "update" if before else "create", actor=actor, before=before, after=record_dict(record, include_group=False))


@transaction.atomic
def save_record(payload, record=None, actor="web", allow_frozen=False):
    lock()
    caller_record = record
    record = Record.objects.get(pk=record.pk) if record and record.pk else Record()
    original_service_id = record.service_id
    if record.legacy_id and (payload.get("kind", record.kind) != record.kind or str(payload.get("service", record.service_id)) != str(record.service_id)):
        raise ValidationError("Для смены типа архивной записи удалите её и создайте новую работу.")
    if record.pk and is_frozen(record) and not allow_frozen:
        raise ValidationError("Запись включена в отправленный счёт. Используйте корректировку на сайте.")
    for field in ("kind", "description", "employee_name"):
        if field in payload:
            setattr(record, field, payload[field])
    record.included = True
    if "date" in payload:
        record.date = date.fromisoformat(payload["date"])
    for field, Model in (("organization", Organization), ("employee", Employee)):
        if field in payload:
            value = payload[field]
            obj = Model.objects.filter(pk=value).first() if value else None
            if value and not obj:
                raise ValidationError(f"Не найдено: {field}.")
            setattr(record, field, obj)
    from .models import Service
    if "service" in payload:
        record.service = Service.objects.filter(pk=payload["service"]).first() if payload["service"] else None
    if not record.organization_id or not record.organization.is_active:
        raise ValidationError("Укажите активную организацию.")
    if record.kind == "service":
        if not record.service_id or (not record.service.active and (not record.pk or record.service_id != original_service_id)):
            raise ValidationError("Укажите активную услугу.")
        if not record.pk and record.service.frequency != "entry":
            raise ValidationError("Эта услуга начисляется автоматически. Добавьте настройку автоначисления.")
    else:
        record.service = None
        if not record.description.strip():
            raise ValidationError("Укажите описание.")
    from datetime import time
    input_type = record.service.input_type if record.kind == "service" else None
    if input_type == "time":
        for field in ("start_time", "end_time"):
            if field in payload:
                setattr(record, field, time.fromisoformat(payload[field]) if payload[field] else None)
        if bool(record.start_time) != bool(record.end_time):
            raise ValidationError("Укажите и начало, и конец интервала времени.")
    else:
        record.start_time = record.end_time = None
    # A complete interval is authoritative even if an older form sends stale or blank hours.
    if input_type == "time" and record.start_time and record.end_time:
        record.units = calculate_hours(record.start_time, record.end_time)
    elif input_type in ("time", "quantity") and payload.get("units") not in (None, ""):
        record.units = money(payload["units"])
    elif input_type not in ("time", "quantity"):
        record.units = Decimal(1)
    if record.units <= 0:
        raise ValidationError("Количество или длительность должны быть положительными.")
    if record.service_id and record.service.legacy_code == "companion" and record.units != int(record.units):
        raise ValidationError("Количество сопровождений должно быть целым.")
    if "amount" in payload and payload["amount"] not in (None, ""):
        record.amount = money(payload["amount"])
    elif "amount" in payload and (record.kind != "service" or input_type == "amount"):
        raise ValidationError("Укажите положительную сумму.")
    if record.employee_id:
        if not record.employee.is_active:
            raise ValidationError("Сотрудник отключён.")
        record.employee_name = record.employee.display_name
    record.review = record.kind != "expense" and not record.employee_id and not record.auto_key
    record.error = ""
    from .grouping import commit_records
    commit_records([record], actor=actor, allow_frozen=allow_frozen)
    if caller_record is not None:
        caller_record.__dict__.update(record.__dict__)
    return record


def preview_record(payload):
    """Use the actual shared-day engine; discard rows, audits, outbox and on-commit callbacks."""
    with transaction.atomic():
        record = Record.objects.get(pk=payload["id"]) if payload.get("id") else None
        result = save_record(payload, record, allow_frozen=payload.get("correction") is True)
        data = {**record_dict(result), "allocation_changes": result.allocation_changes}
        transaction.set_rollback(True)
    return {"record": data}


def sync_legacy(record, enqueue=True):
    """Mirror only the existing shift/companion contract; no new remote wire types."""
    from apps.shifts.models import ShiftEntry, CompanionEntry
    from apps.shifts.serializers import shift_to_dict, companion_to_dict
    from apps.shifts.sync import queue_sync_change
    code = record.service.legacy_code if record.service_id else ""
    if not record.legacy_id and code not in {"small_admin", "big_admin", "cleaning", "cyclorama_painting", "companion"}:
        return
    kind = record.legacy_kind or ("companion" if code == "companion" else "shift")
    Model = CompanionEntry if kind == "companion" else ShiftEntry
    entry = Model.objects.filter(pk=record.legacy_id).first() if record.legacy_id else Model()
    if not entry:
        return
    creating = not entry.pk
    entry._ledger_bridge = True
    for field in ("organization_id", "employee_id", "date", "deleted_at"):
        setattr(entry, field, getattr(record, field))
    entry.employee_name_snapshot = record.employee_name
    entry.comment = record.description
    entry.calculated_amount = record.amount
    entry.status = "needs_review" if record.review else "confirmed"
    entry.source = "telegram" if record.source == "telegram" else "manual"
    entry.telegram_chat_id = record.chat_id
    entry.telegram_message_id = record.message_id
    entry.telegram_author_user_id = record.author_id
    entry.telegram_author_username = record.author_name
    entry.raw_text = record.raw_text
    if kind == "shift":
        entry.work_type = code or entry.work_type
        entry.hours = record.units if record.service and record.service.input_type == "time" else Decimal(0)
        entry.start_time, entry.end_time = record.start_time, record.end_time
    else:
        entry.count = int(record.units)
    entry.save()
    Record.objects.filter(pk=record.pk).update(legacy_kind=kind, legacy_id=entry.pk)
    record.legacy_kind, record.legacy_id = kind, entry.pk
    if not enqueue:
        return
    data = companion_to_dict(entry) if kind == "companion" else shift_to_dict(entry)
    action = "delete" if record.deleted_at else "create" if creating else "update"
    queue_sync_change(kind, entry.pk, action, data)
    from django.conf import settings
    if getattr(settings, "SHIFT_SYNC_AFTER_WRITE", True):
        from apps.shifts.sync import try_sync_once
        transaction.on_commit(lambda: try_sync_once(limit=10))


@transaction.atomic
def delete_record(record, actor="web", allow_frozen=False):
    lock()
    from .grouping import commit_records
    record = Record.objects.get(pk=record.pk)
    commit_records([], deleted=[record], actor=actor, allow_frozen=allow_frozen)
    return record.allocation_changes


@transaction.atomic
def accrue(until=None):
    lock()
    until = until or timezone.localdate()
    for schedule in Accrual.objects.filter(active=True, service__active=True, organization__is_active=True).select_related("service", "employee"):
        day = schedule.start
        last = min(until, schedule.end) if schedule.end else until
        while day <= last:
            if schedule.service.frequency not in ("daily", "monthly"):
                break
            key_day = day.replace(day=1) if schedule.service.frequency == "monthly" else day
            key = f"{schedule.service_id}:{schedule.organization_id}:{key_day}"
            if not Record.objects.filter(auto_key=key).exists():
                employee = schedule.employee
                record = Record(kind="service", service=schedule.service, organization_id=schedule.organization_id,
                                date=day, auto_key=key, source="auto", units=1, employee=employee,
                                employee_name=employee.display_name if employee else "")
                try:
                    record.amount = calculate(record)
                except ValidationError as error:
                    record.error = " ".join(error.messages)
                    record.review = True
                if employee and not employee.is_active:
                    record.review = True
                    record.error = (record.error + " Сотрудник автоначисления отключён.").strip()
                record.save()
                audit_record(record, None, "scheduler")
            if schedule.service.frequency == "daily":
                day += timedelta(days=1)
            else:
                day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)


@transaction.atomic
def recalculate(start, end, fingerprint=None):
    from collections import defaultdict
    from .grouping import allocation, key, find_group
    from .models import DailyCalculation
    lock()
    if start > end or (end - start).days > 366:
        raise ValidationError("Выберите период не длиннее года.")
    rows = list(Record.objects.filter(kind="service", deleted_at=None, date__range=(start, end)).select_related("service", "organization").order_by("id"))
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    changes, errors, plans, previews, skipped = [], [], [], [], []
    for group_key, members in grouped.items():
        if group_key[1] is None:
            # Imported global phone history has no organization-specific tariff.
            # Keep it intact without blocking recalculation of current accruals.
            archived_phones = all((row.auto_key or "").startswith("legacy-phone:") for row in members)
            skipped.append({"date": str(group_key[2]), "service": members[0].service.name,
                            "organization": "Без организации",
                            "reason": "Архив общего расчёта телефонов" if archived_phones else "Не назначена организация"})
            continue
        if any(is_frozen(row) for row in members):
            skipped.append({"date": str(group_key[2]), "service": members[0].service.name, "organization": members[0].organization.name, "reason": "Есть запись в подтверждённом счёте"})
            continue
        try:
            rate = rate_for(*group_key)
            hourly = django_settings.SHARED_SHIFT_ALLOCATION_ENABLED and rate.calculation == "hourly"
            if hourly:
                hours, total, amounts = allocation(members, rate.price, rate.minimum, rate.maximum)
            else:
                amounts = {row.pk: calculate(row) for row in members}
                hours, total = sum((r.units for r in members), Decimal(0)), sum(amounts.values(), Decimal(0))
            preview = {"service": members[0].service.name, "organization": members[0].organization.name, "date": str(group_key[2]),
                       "hourly": hourly, "hours": str(hours), "total": str(total), "price": str(rate.price),
                       "minimum": str(rate.minimum) if rate.minimum is not None else None, "maximum": str(rate.maximum) if rate.maximum is not None else None,
                       "members": [{"id": r.pk, "employee_name": r.employee_name, "hours": str(r.units), "before": str(r.amount), "after": str(amounts[r.pk])} for r in members]}
            previews.append(preview)
            group = find_group(group_key)
            snapshot_changed = hourly and (not group or any(getattr(group, f) != getattr(rate, f) for f in ("price", "minimum", "maximum")))
            for row in members:
                if row.amount != amounts[row.pk] or row.error or row.calculation_version == 0 or snapshot_changed or (not hourly and row.daily_calculation_id):
                    changes.append({"id": row.pk, "before": str(row.amount), "after": str(amounts[row.pk]), "updated_at": row.updated_at.isoformat()})
            plans.append((group_key, members, rate, amounts, hours, total))
        except ValidationError as error:
            errors.append(f"#{members[0].pk}: {' '.join(error.messages)}")
    # Include sources and snapshots even when their rounded amounts have not changed.
    current = digest({"rows": [record_dict(r) for r in rows], "groups": previews, "errors": errors, "skipped": skipped})
    if fingerprint is not None:
        if current != fingerprint:
            raise ValidationError("Данные изменились. Повторите предварительный просмотр.")
        if errors:
            raise ValidationError("Сначала исправьте отсутствующие тарифы.")
        changed_ids = {c["id"] for c in changes}
        for group_key, members, rate, amounts, hours, total in plans:
            if not any(r.pk in changed_ids for r in members):
                continue
            before = {r.pk: record_dict(r, include_group=False) for r in members}
            if hourly:
                group, _ = DailyCalculation.objects.update_or_create(service_id=group_key[0], organization_id=group_key[1], date=group_key[2],
                    defaults={"active": True, "price": rate.price, "minimum": rate.minimum, "maximum": rate.maximum, "hours": hours, "total": total})
            else:
                DailyCalculation.objects.filter(service_id=group_key[0], organization_id=group_key[1], date=group_key[2]).update(active=False)
                group = None
            for row in members:
                row.amount, row.error, row.daily_calculation, row.calculation_version = amounts[row.pk], "", group, 1
                row.review = not row.employee_id and not row.auto_key
                row.save()
                audit_record(row, before[row.pk], "web:recalculate:shared-shift" if group else "web:recalculate")
                sync_legacy(row)
    return {"changes": changes, "errors": errors, "groups": previews, "skipped": skipped, "fingerprint": current}
