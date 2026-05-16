from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from .constants import CalculationType, PayCode, WorkType
from .models import PayRule, ShiftEntry


ZERO = Decimal("0.00")


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_active_rule(code, target_date):
    return (
        PayRule.objects.filter(code=code, is_active=True, active_from__lte=target_date)
        .filter(Q(active_to__isnull=True) | Q(active_to__gte=target_date))
        .order_by("-active_from", "-updated_at")
        .first()
    )


def calculate_by_rule(rule, hours=ZERO, units=1):
    if rule is None:
        return ZERO

    if rule.calculation_type == CalculationType.HOURLY:
        amount = money(hours) * money(rule.hourly_rate)

        if rule.min_amount is not None and amount > ZERO:
            amount = max(amount, money(rule.min_amount))

        if rule.max_amount is not None:
            amount = min(amount, money(rule.max_amount))

        return money(amount)

    if rule.calculation_type == CalculationType.PER_UNIT:
        return money(rule.fixed_amount) * Decimal(units or 0)

    return money(rule.fixed_amount)


def calculate_shift_amount(work_type, hours, target_date):
    return calculate_by_rule(get_active_rule(work_type, target_date), hours=hours)


def calculate_companion_amount(count, target_date):
    return calculate_by_rule(get_active_rule(PayCode.COMPANION, target_date), units=count)


def has_big_admin_shift(target_date):
    return ShiftEntry.objects.filter(
        date=target_date,
        work_type=WorkType.BIG_ADMIN,
        deleted_at__isnull=True,
    ).exists()


def calculate_phone_amount(target_date):
    code = PayCode.PHONE_WITH_BIG_ADMIN if has_big_admin_shift(target_date) else PayCode.PHONE_WITHOUT_BIG_ADMIN
    return calculate_by_rule(get_active_rule(code, target_date))


def iter_month_dates(year, month):
    for day in range(1, monthrange(year, month)[1] + 1):
        yield date(year, month, day)
