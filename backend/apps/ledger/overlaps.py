"""Cross-studio pricing. All writes use the ledger singleton transaction lock."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import OverlapDecision, Rate, Record, Service

ADMIN_CODES = {"small_admin", "big_admin"}
ALGORITHM = 2
FIELDS = ("amount", "base_amount", "overlap_factor", "calculation_note", "calculation_review",
          "actual_hours", "overlap_hours", "payable_hours", "pricing_version")


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
    from .client_bonuses import source_token as bonus_token
    # Rates and peer changes must invalidate a preview even when amounts match.
    return digest({"rows": list(Record.objects.order_by("id").values()),
                   "services": list(Service.objects.order_by("id").values()),
                   "rates": list(Rate.objects.order_by("id").values()),
                   "decisions": list(OverlapDecision.objects.order_by("id").values()), "client_bonuses": bonus_token()})


def interval(row):
    if not row.start_time or not row.end_time:
        return None
    start = datetime.combine(row.date, row.start_time)
    end = datetime.combine(row.date, row.end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def component_fingerprint(rows, rates=None):
    from .engine import digest, rate_for
    rates = rates or {r.pk: rate_for(r.service_id, r.organization_id, r.date) for r in rows}
    return digest({"algorithm": ALGORITHM, "rows": [{"id": r.pk, "organization": r.organization_id, "employee": r.employee_id,
                    "date": r.date, "service": r.service_id, "start": r.start_time,
                    "end": r.end_time, "hours": r.units, "base": r.base_amount,
                    "rate": {k: getattr(rates[r.pk], k)
                             for k in ("id", "calculation", "price", "minimum", "maximum", "start", "end")}}
                   for r in sorted(rows, key=lambda r: r.pk)]})


def hours(span):
    return Decimal(str((span[1] - span[0]).total_seconds())) / Decimal(3600)


def intersecting_hours(row, peers):
    """Union clipped spans: two other halls covering the same minute count once."""
    own = interval(row)
    if not own:
        return Decimal(0)
    spans = []
    for peer in peers:
        other = interval(peer)
        if other and peer.organization_id != row.organization_id:
            start, end = max(own[0], other[0]), min(own[1], other[1])
            if start < end:
                spans.append((start, end))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum((hours(s) for s in merged), Decimal(0))


def plan(rows):
    """Mutate only in-memory rows; return ambiguous connected components."""
    from .engine import calculate, money, rate_for
    days, rates = defaultdict(list), {}
    for row in rows:
        span = interval(row) if eligible(row) else None
        if eligible(row):
            rates[row.pk] = rate_for(row.service_id, row.organization_id, row.date)
        actual = hours(span) if span else row.units
        row.base_amount = calculate(row, units=actual if span and rates[row.pk].calculation == "hourly" else None)
        row.overlap_factor = Decimal("1.00")
        row.calculation_note = ""
        row.calculation_review = False
        row.pricing_version = ALGORITHM
        row.actual_hours = actual
        row.payable_hours = row.actual_hours
        row.overlap_hours = Decimal(0)
        row.amount = money(row.base_amount + row.bonus + row.client_bonus)
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
            fingerprint = component_fingerprint(group, rates)
            half = set()
            note = ""
            decision = None
            if any(not interval(r) for r in group):
                note = "Укажите интервалы всех смен пересекающихся студий для расчёта."
            else:
                decision = OverlapDecision.objects.filter(employee_id=employee, date=day, fingerprint=fingerprint).first()
                if decision:
                    half = set(decision.half_organizations)
                elif OverlapDecision.objects.filter(employee_id=employee, date=day, members__isnull=False).exists():
                    # Only decisions sharing a member concern this component.
                    stale = any(set(d.members) & {r.pk for r in group} for d in
                                OverlapDecision.objects.filter(employee_id=employee, date=day))
                    if stale:
                        note = "Исходные смены или тарифы изменились: подтвердите ручной выбор заново."
                    elif len(connected) == 2:
                        half.add(min(connected, key=lambda org: (sum(r.actual_hours for r in studios[org]), sum(r.base_amount for r in studios[org]), org)))
                    else:
                        note = "Пересекаются три или более студии: выберите делимые студии."
                elif len(connected) == 2:
                    half.add(min(connected, key=lambda org: (sum(r.actual_hours for r in studios[org]),
                                 sum(r.base_amount for r in studios[org]), org)))
                else:
                    note = "Пересекаются три или более студии: выберите делимые студии."
            pending.append({"employee": employee, "date": str(day), "fingerprint": fingerprint,
                                    "half_organizations": sorted(half), "requires_review": bool(note),
                                    "incomplete": any(not interval(r) for r in group),
                                    "decision_id": decision.pk if not any(not interval(r) for r in group) and decision else None,
                                    "decision_token": digest_decision(fingerprint, decision),
                                    "members": sorted(r.pk for r in group), "organizations": [
                                        {"id": org, "name": studios[org][0].organization.name,
                                         "hours": str(sum(r.units for r in studios[org])),
                                         "base": str(sum(r.base_amount for r in studios[org]))} for org in sorted(connected)]})
            for row in group:
                row.calculation_review = bool(note)
                row.overlap_hours = intersecting_hours(row, group)
                row.calculation_note = note or "Пересекающееся время оплачивается полностью."
                rate = rates[row.pk]
                if rate.calculation == "fixed":
                    row.calculation_note = note or "Фиксированный тариф: оплата полностью."
                elif row.organization_id in half and rate.calculation == "hourly":
                    row.payable_hours = row.actual_hours - row.overlap_hours / 2
                    row.overlap_factor = Decimal(".50")  # Applies to intersecting hours only in v2.
                    row.amount = money(calculate(row, units=row.payable_hours) + row.bonus + row.client_bonus)
                    fmt = lambda v: format(v.quantize(Decimal('.000001')), 'f').rstrip('0').rstrip('.')
                    row.calculation_note = (f"{fmt(row.actual_hours)} ч − {fmt(row.overlap_hours)} ч × ½ = "
                                            f"{fmt(row.payable_hours)} оплачиваемых ч; минимум и максимум после расчёта часов.")
                for field in ("actual_hours", "overlap_hours", "payable_hours"):
                    setattr(row, field, getattr(row, field).quantize(Decimal(".000001")))
    return pending


def digest_decision(fingerprint, decision):
    from .engine import digest
    return digest([fingerprint, decision.pk if decision else None,
                   decision.half_organizations if decision else [], decision.author if decision else ""])


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
    if component["incomplete"]:
        raise ValidationError("Сначала укажите интервалы всех смен пересекающихся студий.")
    if component["decision_id"] != payload.get("decision_id"):
        raise ValidationError("Ручной выбор уже изменился. Обновите расчёт.")
    if payload.get("decision_token") and component["decision_token"] != payload["decision_token"]:
        raise ValidationError("Данные изменились. Обновите расчёт.")
    half = payload.get("half_organizations")
    allowed = {o["id"] for o in component["organizations"]}
    if not isinstance(half, list) or any(type(o) is not int or o not in allowed for o in half) or len(set(half)) != len(half):
        raise ValidationError("Выберите студии из текущего расчёта.")
    if any(is_frozen(r) for r in rows) and payload.get("correction") is not True:
        raise ValidationError("Есть подтверждённая работа. Включите корректировку.")
    previous = OverlapDecision.objects.filter(employee_id=employee, date=day, fingerprint=component["fingerprint"]).values().first()
    decision, _ = OverlapDecision.objects.update_or_create(employee_id=employee, date=day, fingerprint=component["fingerprint"],
                          defaults={"half_organizations": half, "members": component["members"], "author": actor})
    log_change("overlap_decision", decision.pk, "update" if previous else "create", actor=actor, before=previous, after=payload)
    plan(rows)
    for row in rows:
        row.save()
        audit_record(row, before[row.pk], actor + ":overlap")
        sync_legacy(row)
    return {"saved": True}
