"""Journal filters apply before pagination and aggregate calculations."""
from calendar import monthrange
from datetime import date
import re

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import CharField, Q
from django.db.models.functions import Coalesce


ORDERINGS = {
    "date": "date", "amount": "amount", "employee": "employee_name",
    "organization": "organization__name", "service": "display_service", "units": "units",
}


def filter_records(rows, params):
    month = params.get("month", "")
    if month:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValidationError("Выберите месяц в формате ГГГГ-ММ.")
        year, number = map(int, month.split("-"))
        start = date(year, number, 1)
        rows = rows.filter(date__range=(start, date(year, number, monthrange(year, number)[1])))
    else:
        if params.get("start"):
            rows = rows.filter(date__gte=date.fromisoformat(params["start"]))
        if params.get("end"):
            rows = rows.filter(date__lte=date.fromisoformat(params["end"]))
    for key in ("organization", "employee", "service"):
        if params.get(key):
            rows = rows.filter(**{f"{key}__isnull": True}) if params[key] == "unassigned" else rows.filter(**{f"{key}_id": int(params[key])})
    kind = params.get("kind")
    if kind == "work":
        rows = rows.exclude(kind="expense")
    elif kind:
        if kind not in ("service", "oneoff", "expense"):
            raise ValidationError("Неизвестный вид записи.")
        rows = rows.filter(kind=kind)
    status = params.get("status")
    needs_review = Q(review=True) | ~Q(error="")
    if status == "review":
        rows = rows.filter(needs_review)
    elif status == "ready":
        rows = rows.exclude(needs_review)
    elif status:
        raise ValidationError("Неизвестный статус записи.")
    if params.get("source"):
        if params["source"] not in ("web", "telegram", "auto", "demo", "import", "manual"):
            raise ValidationError("Неизвестный источник записи.")
        rows = rows.filter(source=params["source"])
    query = params.get("q", "").strip()
    if query:
        fields = ("description", "employee_name", "service__name", "organization__name", "author_name")
        if connection.vendor == "sqlite":
            # SQLite's built-in LIKE does not case-fold Cyrillic.
            token = query.casefold()
            ids = [r["id"] for r in rows.values("id", *fields) if any(token in str(r[f] or "").casefold() for f in fields)]
            rows = rows.filter(pk__in=ids)
        else:
            condition = Q()
            for field in fields:
                condition |= Q(**{f"{field}__icontains": query})
            rows = rows.filter(condition)
    ordering = params.get("ordering", "-date")
    field = ordering.lstrip("-")
    if field not in ORDERINGS or ordering not in (field, "-" + field):
        raise ValidationError("Неизвестный способ сортировки.")
    rows = rows.annotate(display_service=Coalesce("service__name", "description", output_field=CharField()))
    prefix = "-" if ordering.startswith("-") else ""
    return rows.order_by(prefix + ORDERINGS[field], prefix + "id")
