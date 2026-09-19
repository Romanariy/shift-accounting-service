"""Invoice-level listing for preparation, delivery and shared payment tracking."""
from datetime import date
from django.core.exceptions import ValidationError
from django.db.models import Count
from .models import Invoice, Delivery, TelegramContact


def invoice_list(params):
    rows = Invoice.objects.select_related("batch").defer("artifact", "lines", "adjustments")
    open_count = rows.filter(batch__state="approved", payment_state="open").count()
    preparation = params.get("state", "all") or "all"
    payment = params.get("payment_state", "all") or "all"
    if preparation not in ("all", "draft", "approved") or payment not in ("all", "none", "open", "paid", "superseded"):
        raise ValidationError("Неизвестный статус счёта или оплаты.")
    if preparation != "all": rows = rows.filter(batch__state=preparation)
    if payment != "all": rows = rows.filter(payment_state=payment)
    if params.get("organization"): rows = rows.filter(organization_id=int(params["organization"]))
    start = date.fromisoformat(params["start"]) if params.get("start") else None
    end = date.fromisoformat(params["end"]) if params.get("end") else None
    if start and end and start > end:
        raise ValidationError("Начало периода должно быть не позже окончания.")
    if start: rows = rows.filter(batch__end__gte=start)
    if end: rows = rows.filter(batch__start__lte=end)
    offset = max(0, int(params.get("offset", 0)))
    limit = min(250, max(1, int(params.get("limit", 50))))
    count = rows.count()
    page = list(rows.order_by("-id")[offset:offset + limit])
    deliveries = {}
    for row in Delivery.objects.filter(invoice_id__in=[i.pk for i in page], purpose="owner").values("invoice_id", "state").annotate(count=Count("id")):
        deliveries.setdefault(row["invoice_id"], {})[row["state"]] = row["count"]
    names = dict(TelegramContact.objects.values_list("user_id", "name"))
    return {"invoices": [{"id": i.pk, "batch": i.batch_id, "organization": i.organization_id,
        "organization_name": i.organization_name, "start": i.batch.start, "end": i.batch.end,
        "version": i.version, "batch_version": i.batch.version, "batch_state": i.batch.state,
        "total": i.total, "replaces": i.replaces_id, "payment_state": i.payment_state,
        "paid_at": i.paid_at, "paid_by": i.paid_by, "paid_by_name": names.get(i.paid_by, ""),
        "delivery_counts": deliveries.get(i.pk, {}), "recipient_count": len(i.recipients),
        "errors": i.errors} for i in page],
        "count": count, "open_count": open_count, "offset": offset, "limit": limit}
