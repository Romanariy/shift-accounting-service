"""Actual employee payments, independent of earnings and owner invoice payments."""
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.shifts.audit import json_safe, log_change
from .engine import digest, lock
from .models import EmployeePayout, EmployeePayoutLine, EmployeePayoutPreview, Record

START = date(2026, 10, 1)
ZONE = ZoneInfo("Asia/Yekaterinburg")


def paid_amounts(ids):
    return dict(EmployeePayoutLine.objects.filter(record_id__in=ids, payout__cancelled_at=None)
                .values("record_id").annotate(total=Sum("amount")).values_list("record_id", "total"))


def status(amount, paid):
    if paid > amount:
        return "overpaid"
    if paid == amount:
        return "paid"
    return "partial" if paid else "unpaid"


def record_payment(row, paid=None):
    if row.kind == "expense" or row.date < START:
        return {"payout_tracking": False, "payout_paid": None, "payout_remaining": None, "payout_state": "unknown"}
    paid = paid_amounts([row.pk]).get(row.pk, Decimal(0)) if paid is None else paid
    return json_safe({"payout_tracking": True, "payout_paid": paid, "payout_remaining": row.amount - paid,
                      "payout_state": status(row.amount, paid)})


def authorize(user_id):
    config = lock()
    if not config.approver_id or config.approver.user_id != user_id:
        raise ValidationError("Отмечать выплаты может только главный администратор.")


def selection(payload):
    if payload.get("record"):
        row = Record.objects.get(pk=int(payload["record"]), deleted_at=None)
        selector = {"record": row.pk}
        rows = [row]
    else:
        selector = {"employee": int(payload["employee"]), "organization": int(payload["organization"]), "date": str(date.fromisoformat(payload["date"]))}
        rows = list(Record.objects.filter(employee_id=selector["employee"], organization_id=selector["organization"],
                    date=selector["date"], deleted_at=None, kind__in=("service", "oneoff")).order_by("id"))
    if not rows or any(r.kind == "expense" or not r.employee_id or not r.organization_id for r in rows):
        raise ValidationError("Выберите выполненные работы сотрудника в студии.")
    if any(r.date < START for r in rows):
        raise ValidationError("Учёт выплат начинается с 01.10.2026. За прежние периоды нет данных.")
    if any(r.date > timezone.now().astimezone(ZONE).date() for r in rows):
        raise ValidationError("Будущие работы оплачивать нельзя.")
    if any(r.review or r.error or r.calculation_review or r.client_bonus_review for r in rows):
        raise ValidationError("Сначала исправьте работы, требующие проверки: " + ", ".join(str(r.pk) for r in rows if r.review or r.error or r.calculation_review or r.client_bonus_review))
    paid = paid_amounts([r.pk for r in rows])
    lines = [json_safe({"record": r.pk, "date": r.date, "employee": r.employee_id, "employee_name": r.employee.display_name,
                       "organization": r.organization_id, "organization_name": r.organization.name,
                       "service_name": r.service.name if r.service_id else r.description,
                       "description": r.description, "start_time": r.start_time, "end_time": r.end_time,
                       "earned": r.amount, "paid": paid.get(r.pk, Decimal(0)),
                       "remaining": r.amount - paid.get(r.pk, Decimal(0)),
                       "to_pay": max(Decimal(0), r.amount - paid.get(r.pk, Decimal(0))),
                       "updated_at": r.updated_at, "bonus": r.bonus, "client_bonus": r.client_bonus}) for r in rows]
    for row, line in zip(rows, lines):
        line.update(json_safe({"units": row.units, "base_amount": row.base_amount, "bonus_reason": row.bonus_reason,
                               "overlap_hours": row.overlap_hours, "payable_hours": row.payable_hours,
                               "pricing_version": row.pricing_version, "calculation_note": row.calculation_note,
                               "client_bonus_details": row.client_bonus_details}))
    # Include payment events as well as amounts: cancelling and repeating a payment invalidates a draft.
    events = list(EmployeePayoutLine.objects.filter(record_id__in=[r.pk for r in rows]).order_by("id")
                  .values("id", "payout__revision", "payout__cancelled_at"))
    return selector, lines, digest({"lines": lines, "events": events})


@transaction.atomic
def preview(payload, actor="web", user_id=None):
    lock()
    if user_id is not None:
        authorize(user_id)
    selector, lines, fingerprint = selection(payload)
    amount = sum((Decimal(r["to_pay"]) for r in lines), Decimal(0))
    if amount <= 0:
        raise ValidationError("Нет положительного остатка к выплате.")
    item = EmployeePayoutPreview.objects.create(selector=selector, fingerprint=fingerprint, author=actor,
                                              source="telegram" if user_id is not None else "web")
    return {"token": str(item.token), "lines": lines, "amount": str(amount), "tracking_start": str(START)}


def payout_data(item):
    return json_safe({"id": item.pk, "employee": item.employee_id, "employee_name": item.employee.display_name,
                      "organization": item.organization_id, "organization_name": item.organization.name,
                      "work_date": item.work_date, "amount": item.amount, "paid_at": item.paid_at,
                      "author": item.author, "source": item.source, "revision": item.revision,
                      "cancelled_at": item.cancelled_at, "cancel_reason": item.cancel_reason,
                      "cancelled_by": item.cancelled_by,
                      "lines": [{"record": l.record_id, "amount": str(l.amount), "snapshot": l.snapshot} for l in item.lines.all()]})


@transaction.atomic
def confirm(payload, actor="web", user_id=None):
    lock()
    if user_id is not None:
        authorize(user_id)
    token = UUID(str(payload["token"]))
    draft = EmployeePayoutPreview.objects.get(token=token)
    if draft.author != actor or draft.source != ("telegram" if user_id is not None else "web"):
        raise ValidationError("Этот предпросмотр принадлежит другому пользователю.")
    existing = EmployeePayout.objects.filter(request_key=token).first()
    if existing:
        if existing.cancelled_at:
            raise ValidationError("Эта выплата отменена. Создайте новый предпросмотр.")
        return {**payout_data(existing), "already_paid": True}
    if draft.created_at < timezone.now() - timedelta(hours=1):
        raise ValidationError("Предпросмотр устарел. Обновите данные.")
    _, lines, fingerprint = selection(draft.selector)
    if fingerprint != draft.fingerprint:
        raise ValidationError("Работы или выплаты изменились. Обновите предпросмотр.")
    payable = [r for r in lines if Decimal(r["to_pay"]) > 0]
    if not payable:
        raise ValidationError("Все выбранные работы уже оплачены.")
    first = payable[0]
    item = EmployeePayout.objects.create(request_key=token, employee_id=first["employee"], organization_id=first["organization"],
        work_date=first["date"], amount=sum((Decimal(r["to_pay"]) for r in payable), Decimal(0)), author=actor, source=draft.source)
    for line in payable:
        EmployeePayoutLine.objects.create(payout=item, record_id=line["record"], amount=line["to_pay"], snapshot=line)
    log_change("employee_payout", item.pk, "create", actor=actor, after=payout_data(item))
    from .payout_bot import refresh_messages
    refresh_messages()
    return payout_data(item)


@transaction.atomic
def cancel(pk, payload):
    lock()
    item = EmployeePayout.objects.get(pk=pk)
    if int(payload["revision"]) != item.revision:
        raise ValidationError("Выплата изменилась. Обновите список.")
    reason = str(payload.get("reason", "")).strip()
    if not reason or len(reason) > 500:
        raise ValidationError("Укажите причину отмены (до 500 символов).")
    if item.cancelled_at:
        raise ValidationError("Выплата уже отменена.")
    before = payout_data(item)
    item.cancelled_at, item.cancelled_by, item.cancel_reason = timezone.now(), "web", reason
    item.revision += 1
    item.save()
    log_change("employee_payout", item.pk, "cancel", actor="web", before=before, after=payout_data(item))
    from .payout_bot import refresh_messages
    refresh_messages()
    return payout_data(item)


def guard_changes(previous, current):
    if previous and EmployeePayoutLine.objects.filter(record=previous, payout__cancelled_at=None).exists():
        if current.deleted_at or any(getattr(previous, f) != getattr(current, f) for f in ("employee_id", "organization_id", "date", "kind")):
            raise ValidationError("Сначала отмените выплату этой работы. Нельзя менять сотрудника, студию, дату, вид записи или удалять оплаченную работу.")


def listing(params):
    rows = EmployeePayout.objects.select_related("employee", "organization").prefetch_related("lines").order_by("-paid_at", "-id")
    for field in ("employee", "organization"):
        if params.get(field):
            rows = rows.filter(**{field + "_id": int(params[field])})
    if params.get("month"):
        start = date.fromisoformat(params["month"] + "-01")
        rows = rows.filter(work_date__year=start.year, work_date__month=start.month)
    offset, limit = max(0, int(params.get("offset", 0))), min(100, max(1, int(params.get("limit", 50))))
    return {"payouts": [payout_data(r) for r in rows[offset:offset + limit]], "count": rows.count(), "offset": offset, "limit": limit, "tracking_start": str(START)}


def details(params):
    start = date.fromisoformat(params["month"] + "-01")
    rows = list(Record.objects.filter(employee_id=int(params["employee"]), date__year=start.year, date__month=start.month,
                deleted_at=None, kind__in=("service", "oneoff")).select_related("employee", "organization", "service").order_by("-date", "organization_id", "id"))
    paid = paid_amounts([r.pk for r in rows])
    from .engine import record_dict
    items = [{**record_dict(r, include_group=False), **record_payment(r, paid.get(r.pk, Decimal(0)))} for r in rows]
    if params.get("state"):
        if params["state"] not in ("unpaid", "partial", "paid", "overpaid", "unknown"):
            raise ValidationError("Неизвестное состояние выплаты.")
        items = [r for r in items if r["payout_state"] == params["state"]]
    offset, limit = max(0, int(params.get("offset", 0))), min(100, max(1, int(params.get("limit", 50))))
    return {"records": items[offset:offset + limit], "count": len(items), "offset": offset, "limit": limit}
