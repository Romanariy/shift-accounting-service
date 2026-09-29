"""Cross-studio pricing. All writes use the ledger singleton transaction lock."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import OverlapDecision, Rate, Record, Service

ADMIN_CODES = {"small_admin", "big_admin"}
FIELDS = ("amount", "base_amount", "overlap_factor", "calculation_note", "calculation_review")


def eligible(row):
    return row.kind == "service" and row.service_id and row.service.legacy_code in ADMIN_CODES


def day_key(row):
    return (row.employee_id, row.date) if eligible(row) and row.employee_id else None


def members(keys):
    from django.db.models import Q
    query = Q(pk__in=[])
    for employee, day in keys:
        query |= Q(employee_id=employee, date=day)
    return Record.objects.filter(query, deleted_at=None, kind="service", service__legacy_code__in=ADMIN_CODES).select_related("service", "organization").order_by("id")


def source_token():
    from .engine import digest
    # Rates and peer changes must invalidate a preview even when amounts match.
    return digest({"rows": list(Record.objects.order_by("id").values()),
                   "services": list(Service.objects.order_by("id").values()),
                   "rates": list(Rate.objects.order_by("id").values()),
                   "decisions": list(OverlapDecision.objects.order_by("id").values())})


def interval(row):
    if not row.start_time or not row.end_time:
        return None
    start = datetime.combine(row.date, row.start_time)
    end = datetime.combine(row.date, row.end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def component_fingerprint(rows):
    from .engine import digest
    return digest([{"id": r.pk, "organization": r.organization_id, "employee": r.employee_id,
                    "date": r.date, "service": r.service_id, "start": r.start_time,
                    "end": r.end_time, "hours": r.units, "base": r.base_amount} for r in sorted(rows, key=lambda r: r.pk)])


def plan(rows):
    """Mutate only in-memory rows; return ambiguous connected components."""
    from .engine import calculate, money
    days = defaultdict(list)
    for row in rows:
        row.base_amount = calculate(row)
        row.overlap_factor = Decimal("1.00")
        row.calculation_note = ""
        row.calculation_review = False
        row.amount = money(row.base_amount + row.bonus)
        if eligible(row):
            if not interval(row):
                row.calculation_note = "Для проверки пересечения укажите начало и конец смены."
            if day_key(row):
                days[day_key(row)].append(row)
    pending = []
    for (employee, day), day_rows in days.items():
        studios = defaultdict(list)
        for row in day_rows:
            studios[row.organization_id].append(row)
        graph = {org: set() for org in studios}
        for a in day_rows:
            for b in day_rows:
                if a.organization_id == b.organization_id:
                    continue
                ia, ib = interval(a), interval(b)
                if ia and ib and max(ia[0], ib[0]) < min(ia[1], ib[1]):
                    graph[a.organization_id].add(b.organization_id)
        unseen = set(graph)
        while unseen:
            connected, queue = set(), [min(unseen)]
            while queue:
                org = queue.pop()
                if org in connected:
                    continue
                connected.add(org)
                queue.extend(graph[org] - connected)
            unseen -= connected
            if len(connected) < 2:
                continue
            group = [r for org in sorted(connected) for r in studios[org]]
            fingerprint = component_fingerprint(group)
            half = set()
            note = ""
            if any(not interval(r) for r in group):
                note = "Укажите интервалы всех смен пересекающихся студий для расчёта."
            elif len(connected) == 2:
                half.add(min(connected, key=lambda org: (sum(r.units for r in studios[org]),
                             sum(r.base_amount for r in studios[org]), org)))
            else:
                decision = OverlapDecision.objects.filter(employee_id=employee, date=day, fingerprint=fingerprint).first()
                if decision:
                    half = set(decision.half_organizations)
                else:
                    note = "Пересекаются три или более студии: выберите полный или половинный расчёт."
                    pending.append({"employee": employee, "date": str(day), "fingerprint": fingerprint,
                                    "members": sorted(r.pk for r in group), "organizations": [
                                        {"id": org, "name": studios[org][0].organization.name,
                                         "hours": str(sum(r.units for r in studios[org])),
                                         "base": str(sum(r.base_amount for r in studios[org]))} for org in sorted(connected)]})
            for row in group:
                row.calculation_review = bool(note)
                row.calculation_note = note or ("База ÷ 2: пересечение студий." if row.organization_id in half else "Полная база: пересечение студий.")
            for org in half:
                # Divide the studio total once and allocate residual kopecks deterministically.
                group_rows = sorted(studios[org], key=lambda r: r.pk)
                amounts = {r.pk: (r.base_amount / 2).quantize(Decimal(".01"), rounding=ROUND_DOWN) for r in group_rows}
                target = money(sum(r.base_amount for r in group_rows) / 2)
                remainder = int((target - sum(amounts.values())) * 100)
                ordered = sorted(group_rows, key=lambda r: (-(r.base_amount / 2 - amounts[r.pk]), r.pk))
                for row in ordered[:remainder]:
                    amounts[row.pk] += Decimal(".01")
                for row in group_rows:
                    row.overlap_factor = Decimal(".50")
                    row.amount = money(amounts[row.pk] + row.bonus)
    return pending


def review_day(employee, day):
    rows = list(members({(employee, day)}))
    return plan(rows)


@transaction.atomic
def decide(payload, actor="web"):
    from datetime import date
    from .engine import audit_record, is_frozen, record_dict, sync_legacy, lock
    from apps.shifts.audit import log_change
    lock()
    employee, day = int(payload["employee"]), date.fromisoformat(payload["date"])
    rows = list(members({(employee, day)}))
    before = {r.pk: record_dict(r, include_group=False) for r in rows}
    pending = plan(rows)
    component = next((p for p in pending if p["fingerprint"] == payload.get("fingerprint")), None)
    if not component:
        raise ValidationError("Данные изменились или выбор уже сохранён. Обновите расчёт.")
    half = payload.get("half_organizations")
    allowed = {o["id"] for o in component["organizations"]}
    if not isinstance(half, list) or any(type(o) is not int or o not in allowed for o in half) or len(set(half)) != len(half):
        raise ValidationError("Выберите студии из текущего расчёта.")
    if any(is_frozen(r) for r in rows) and payload.get("correction") is not True:
        raise ValidationError("Есть подтверждённая работа. Включите корректировку.")
    decision = OverlapDecision.objects.create(employee_id=employee, date=day, fingerprint=component["fingerprint"],
                                             half_organizations=half, members=component["members"], author=actor)
    log_change("overlap_decision", decision.pk, "create", actor=actor, after=payload)
    plan(rows)
    for row in rows:
        row.save()
        audit_record(row, before[row.pk], actor + ":overlap")
        sync_legacy(row)
    return {"saved": True}
