from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import DailyCalculation, Record
from . import overlaps


def recalculate_independent(start, end, fingerprint=None):
    from .engine import lock, digest, is_frozen, record_dict, audit_record, sync_legacy, rate_for
    lock()
    if start > end or (end - start).days > 366:
        raise ValidationError("Выберите период не длиннее года.")
    rows = list(Record.objects.filter(kind="service", deleted_at=None, date__range=(start, end)).select_related("service", "organization").order_by("id"))
    frozen_days = {overlaps.day_key(r) for r in rows if overlaps.day_key(r) and is_frozen(r)}
    before = {r.pk: record_dict(r, include_group=False) for r in rows}
    candidates, skipped, errors = [], [], []
    for row in rows:
        reason = ""
        if not row.organization_id:
            reason = "Архив общего расчёта телефонов" if (row.auto_key or "").startswith("legacy-phone:") else "Не назначена организация"
        elif is_frozen(row) or overlaps.day_key(row) in frozen_days:
            reason = "Есть запись в подтверждённом счёте (связанный день сохраняется целиком)"
        if reason:
            skipped.append({"date": str(row.date), "organization": row.organization.name if row.organization_id else "Без организации", "service": row.service.name, "reason": reason})
        else:
            candidates.append(row)
    try:
        overlaps.plan(candidates)
    except ValidationError as error:
        errors.extend(error.messages)
    # Backfilling calculation metadata is not a change to the user's work.
    # Compare money numerically, so 600 and 600.00 are identical.
    changed = [r for r in candidates if Decimal(before[r.pk]["amount"]) != r.amount
               or bool(before[r.pk].get("calculation_review")) != r.calculation_review
               or bool(before[r.pk].get("error"))]
    groups = defaultdict(list)
    for row in changed:
        groups[(row.service_id, row.organization_id, row.date)].append(row)
    previews = []
    for (service, organization, day), group in groups.items():
        try:
            rate = rate_for(service, organization, day)
        except ValidationError:
            continue
        previews.append({"date": str(day), "organization": group[0].organization.name, "service": group[0].service.name,
                         "hourly": False, "hours": str(sum(r.units for r in group)), "total": str(sum(r.amount for r in group)),
                         "price": str(rate.price), "minimum": str(rate.minimum) if rate.minimum is not None else None,
                         "maximum": str(rate.maximum) if rate.maximum is not None else None,
                         "members": [{"id": r.pk, "employee_name": r.employee_name, "hours": str(r.units),
                                      "before": before[r.pk]["amount"], "after": str(r.amount), "base_amount": str(r.base_amount),
                                      "overlap_factor": str(r.overlap_factor), "bonus": str(r.bonus),
                                      "actual_hours": str(r.actual_hours), "overlap_hours": str(r.overlap_hours),
                                      "payable_hours": str(r.payable_hours), "pricing_version": r.pricing_version,
                                      "calculation_note": r.calculation_note,
                                      "change_reason": ("Требуется проверка расчёта" if r.calculation_review else "Проверка расчёта завершена")
                                      if bool(before[r.pk].get("calculation_review")) != r.calculation_review
                                      else "Исправлена ошибка расчёта" if before[r.pk].get("error") else ""} for r in group]})
    changes = [{"id": r.pk, "before": before[r.pk]["amount"], "after": str(r.amount), "updated_at": before[r.pk]["updated_at"]}
               for r in changed]
    token = digest({"source": overlaps.source_token(), "start": start, "end": end, "groups": previews})
    if fingerprint is not None:
        if token != fingerprint:
            raise ValidationError("Данные изменились. Повторите предварительный просмотр.")
        if errors:
            raise ValidationError("Сначала исправьте отсутствующие тарифы: " + " ".join(errors))
        for row in changed:
            row.daily_calculation = None
            row.calculation_version = 1
            row.error = ""
            row.review = not row.employee_id and not row.auto_key
            row.save()
            audit_record(row, before[row.pk], "web:recalculate")
            sync_legacy(row)
        for service, organization, day in groups:
            DailyCalculation.objects.filter(service_id=service, organization_id=organization, date=day).update(active=False)
    return {"changes": changes, "errors": errors, "groups": previews, "skipped": skipped, "fingerprint": token}
