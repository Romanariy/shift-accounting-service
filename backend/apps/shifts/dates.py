"""Russian calendar date labels independent of server locale."""
from datetime import date, datetime

WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def date_label(value, short=False):
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value.strftime('%d.%m' if short else '%d.%m.%Y')} ({WEEKDAYS[value.weekday()]})"


def date_time_label(value):
    from django.utils import timezone
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return f"{date_label(value)} {value:%H:%M:%S}"


def excel_date_format(value):
    return f'DD.MM.YYYY" ({WEEKDAYS[value.weekday()]})"'
