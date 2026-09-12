from datetime import date

from .constants import INITIAL_PAY_RULES, WORK_TYPE_CHOICES
from .models import Organization, PayRule


def copy_initial_rates(organization):
    """New organizations get independent copies of Focus's complete rate schedule."""
    focus = Organization.objects.filter(is_default=True).exclude(pk=organization.pk).first()
    fields = ("code", "title", "calculation_type", "hourly_rate", "fixed_amount",
              "min_amount", "max_amount", "active_from", "active_to", "is_active")
    for code, _ in WORK_TYPE_CHOICES:
        rules = list(PayRule.objects.filter(organization=focus, code=code).order_by("active_from", "updated_at", "pk").values(*fields))
        if not rules:
            rules = list(PayRule.objects.filter(organization=None, code=code).order_by("active_from", "updated_at", "pk").values(*fields))
        if not rules:
            seed = next(rule for rule in INITIAL_PAY_RULES if rule["code"] == code)
            rules = [{**seed, "active_from": date(2026, 1, 1)}]
        for values in rules:
            PayRule.objects.create(organization=organization, **values)
    from django.db import connection
    if focus and "ledger_rate" in connection.introspection.table_names():
        from apps.ledger.models import Rate
        for rate in Rate.objects.filter(organization=focus):
            values = {field: getattr(rate, field) for field in ("service_id", "calculation", "price", "minimum", "maximum", "start", "end", "active")}
            Rate.objects.create(organization=organization, **values)
