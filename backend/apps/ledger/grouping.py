"""One transactional write path for web, Telegram and legacy shift records."""
from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.shifts.audit import json_safe
from .models import DailyCalculation, Record


def key(row):
    return (row.service_id, row.organization_id, row.date) if row.kind == "service" else None


def matching(group_key):
    service, organization, day = group_key
    return Record.objects.filter(kind="service", service_id=service, organization_id=organization, date=day, deleted_at=None)


def find_group(group_key):
    if not group_key:
        return None
    return DailyCalculation.objects.filter(service_id=group_key[0], organization_id=group_key[1], date=group_key[2], active=True).first()


def allocation(rows, price, minimum, maximum):
    """Largest remainder allocation in integer kopecks, with a stable ID tie-break."""
    from .engine import money
    hours = sum((r.units for r in rows), Decimal(0))
    if any(r.units <= 0 for r in rows):
        raise ValidationError("Часы каждой части смены должны быть положительными.")
    total = hours * price
    if hours:
        if minimum is not None:
            total = max(total, minimum)
        if maximum is not None:
            total = min(total, maximum)
    total = money(total)
    if not hours:
        return hours, total, {}
    cents = int(total * 100)
    exact = {r.pk: Decimal(cents) * r.units / hours for r in rows}
    shares = {pk: int(value.to_integral_value(rounding=ROUND_DOWN)) for pk, value in exact.items()}
    ordered = sorted(shares, key=lambda pk: (-(exact[pk] - shares[pk]), pk))
    for pk in ordered[:cents - sum(shares.values())]:
        shares[pk] += 1
    return hours, total, {pk: Decimal(value) / 100 for pk, value in shares.items()}


def group_info(group):
    if not group:
        return None
    return json_safe({"id": group.pk, "hours": group.hours, "total": group.total, "price": group.price,
                     "minimum": group.minimum, "maximum": group.maximum,
                     "members": list(group.records.filter(deleted_at=None).order_by("id").values("id", "employee_name", "units", "amount"))})


def financial_change(old, new):
    fields = ("kind", "service_id", "organization_id", "date", "units", "start_time", "end_time", "deleted_at")
    return old is None or any(getattr(old, field) != getattr(new, field) for field in fields) or (new.kind != "service" and old.amount != new.amount)


def guard_frozen(keys, rows, allow_frozen):
    from .engine import is_frozen
    if allow_frozen:
        return
    candidates = {r.pk: r for r in rows if r.pk}
    for group_key in keys:
        candidates.update({r.pk: r for r in matching(group_key)})
    if any(is_frozen(r) for r in candidates.values()):
        raise ValidationError("Запись или часть этой смены уже включена в подтверждённый счёт. Оформите корректировку всей группы на сайте.")


@transaction.atomic
def commit_records(rows, *, deleted=(), actor="web", allow_frozen=False):
    from .engine import lock, calculate, rate_for, record_dict, audit_record, sync_legacy
    lock()
    rows = list(rows)
    for row in deleted:
        row.deleted_at = timezone.now()
        rows.append(row)
    old = {r.pk: Record.objects.get(pk=r.pk) for r in rows if r.pk}
    changed_keys = {k for r in rows for k in (key(old.get(r.pk)) if r.pk else None, key(r)) if k and financial_change(old.get(r.pk), r)}
    all_keys = {key(r) for r in rows if key(r)} | {key(r) for r in old.values() if key(r)}
    # Fixed/quantity services remain independent; only hourly groups share the freeze boundary.
    hourly_keys = set()
    groups = {}
    rates = {}
    for k in all_keys:
        group = find_group(k)
        if group:
            hourly_keys.add(k)
            groups[k] = group
        elif k in changed_keys:
            # A removed record needs no current tariff unless its remaining group is managed.
            incoming = [r for r in rows if key(r) == k and not r.deleted_at]
            if incoming:
                rate = rate_for(*k)
                rates[k] = rate
                if rate.calculation == "hourly":
                    hourly_keys.add(k)
    guard_frozen(hourly_keys, [*rows, *old.values()], allow_frozen)
    if any(old.get(r.pk) and old[r.pk].organization_id and old[r.pk].calculation_version == 0 and key(r) in hourly_keys and financial_change(old[r.pk], r) and not r.deleted_at for r in rows):
        raise ValidationError("Сначала переведите историческую смену через предпросмотр пересчёта на сайте.")
    for k in hourly_keys & changed_keys:
        if matching(k).filter(Q(calculation_version=0) | Q(daily_calculation__isnull=True)).exists():
            raise ValidationError("В этом дне есть исторические смены. Сначала подтвердите их перевод через предпросмотр пересчёта на сайте.")
        if k not in groups:
            rate = rates[k]
            groups[k], _ = DailyCalculation.objects.update_or_create(service_id=k[0], organization_id=k[1], date=k[2], defaults={"active": True, "price": rate.price, "minimum": rate.minimum, "maximum": rate.maximum})

    # Capture every participant before writing, including ones whose shares will change indirectly.
    affected = dict(old)
    for k in hourly_keys & changed_keys:
        affected.update({r.pk: r for r in matching(k)})
    before = {pk: record_dict(r, include_group=False) for pk, r in affected.items()}
    for row in rows:
        previous = old.get(row.pk)
        changed = financial_change(previous, row)
        if previous and previous.legacy_id and (previous.kind != row.kind or previous.service_id != row.service_id):
            previous.deleted_at = timezone.now()
            sync_legacy(previous)
            row.legacy_id, row.legacy_kind = None, ""
        group = groups.get(key(row))
        if group:
            row.daily_calculation = group
        elif changed:
            row.daily_calculation = None
        if not row.deleted_at:
            if not changed and previous is not None:
                # A full editor payload may carry a previously displayed amount. Ignore it for calculated services.
                if row.kind == "service":
                    rate = rates.get(key(row))
                    if not group and row.amount != previous.amount:
                        rate = rate or rate_for(*key(row))
                    if rate and rate.calculation == "amount" and row.amount != previous.amount:
                        row.amount = calculate(row)
                    else:
                        row.amount = previous.amount
                row.error = previous.error
            elif group:
                row.amount = previous.amount if previous else Decimal(0)
                row.error = ""
            else:
                row.amount = calculate(row)
                row.error = ""
            if changed:
                row.calculation_version = 1
            row.full_clean()
        row.save()

    for k in hourly_keys & changed_keys:
        group = groups[k]
        participants = list(matching(k).order_by("id"))
        group.hours, group.total, shares = allocation(participants, group.price, group.minimum, group.maximum)
        group.save()
        for participant in participants:
            participant.amount = shares[participant.pk]
            participant.daily_calculation = group
            participant.calculation_version = 1
            participant.error = ""
            participant.save()
            affected[participant.pk] = participant

    changed_ids = {r.pk for r in rows} | set(affected)
    updates = []
    for final in Record.objects.filter(pk__in=changed_ids).order_by("id"):
        previous = before.get(final.pk)
        after = record_dict(final, include_group=False)
        # Ignore timestamps when deciding whether an untouched peer needs an audit/sync event.
        compare = lambda item: {k: v for k, v in item.items() if k != "updated_at"}
        if previous is None or compare(previous) != compare(after):
            reason = actor + (":shared-shift" if key(final) in hourly_keys & changed_keys else "")
            audit_record(final, previous, reason)
            sync_legacy(final, enqueue=not (actor == "legacy" and final.pk in {r.pk for r in rows}))
        if previous and previous["amount"] != str(final.amount) and Decimal(previous["amount"]) != final.amount:
            updates.append({"id": final.pk, "employee_name": final.employee_name, "before": previous["amount"], "after": str(final.amount)})
    for row in rows:
        row.refresh_from_db()
        row.allocation_changes = updates
    return rows
