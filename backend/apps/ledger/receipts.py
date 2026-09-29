from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.forms.models import model_to_dict
from django.utils import timezone

from apps.shifts.audit import json_safe, log_change
from apps.shifts.models import Organization
from .models import OrganizationReceipt


def data(row):
    return json_safe({**model_to_dict(row), "id": row.pk, "updated_at": row.updated_at})


def listing(params):
    if not params.get("organization"):
        raise ValidationError("Выберите организацию-получателя.")
    if params.get("start") and params.get("end") and date.fromisoformat(params["start"]) > date.fromisoformat(params["end"]):
        raise ValidationError("Начало периода не может быть позже окончания.")
    rows = OrganizationReceipt.objects.filter(organization_id=int(params["organization"]), deleted_at=None)
    for field, op in (("start", "gte"), ("end", "lte")):
        if params.get(field):
            rows = rows.filter(**{f"date__{op}": date.fromisoformat(params[field])})
    offset = max(0, int(params.get("offset", 0)))
    limit = min(250, max(1, int(params.get("limit", 50))))
    received = rows.filter(kind="receipt").aggregate(total=Sum("amount"))["total"] or 0
    deducted = rows.filter(kind="deduction").aggregate(total=Sum("amount"))["total"] or 0
    return {"receipts": [data(r) for r in rows[offset:offset + limit]], "count": rows.count(),
            "total": received - deducted, "received_total": received, "deduction_total": deducted, "offset": offset, "limit": limit}


def save(payload, pk=None, delete=False):
    from .engine import money
    row = OrganizationReceipt.objects.get(pk=pk, deleted_at=None) if pk else OrganizationReceipt()
    before = data(row) if pk else None
    if pk and payload.get("updated_at") != row.updated_at.isoformat():
        raise ValidationError("Поступление изменилось. Обновите список перед сохранением.")
    if delete:
        row.deleted_at = timezone.now()
    else:
        row.organization = Organization.objects.get(pk=int(payload["organization"]))
        if not row.organization.is_active:
            raise ValidationError("Выберите активную организацию.")
        row.date = date.fromisoformat(payload["date"])
        row.kind = payload.get("kind", row.kind)
        row.payer = payload.get("payer", "")
        row.reason = payload.get("reason", "")
        row.amount = money(payload["amount"])
        row.full_clean()
    row.save()
    log_change("receipt", row.pk, "delete" if delete else "update" if pk else "create", actor="web", before=before, after=data(row))
    return data(row)
