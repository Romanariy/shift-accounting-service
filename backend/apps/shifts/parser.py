import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .constants import WorkType


DATE_RE = re.compile(r"(?P<day>\d{1,2})[.](?P<month>\d{1,2})(?:[.](?P<year>\d{2,4}))?")
TIME_RANGE_RE = re.compile(
    r"(?P<start_hour>\d{1,2})[:.](?P<start_minute>\d{2})\s*[-–—]\s*"
    r"(?P<end_hour>\d{1,2})[:.](?P<end_minute>\d{2})"
)
COMPANION_RE = re.compile(
    r"\+\s*(?P<count>\d+)\s*(?:сопр|сопровождени[еяй]?|сопровождения?)\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedMessage:
    date: date
    employee_hint: str
    work_type: str
    start_time: time | None
    end_time: time | None
    hours: Decimal
    companion_count: int
    comment: str


class ParseError(ValueError):
    pass


def normalize_text(text):
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def normalize_alias(value):
    return normalize_text(value).lower().lstrip("@")


def calculate_hours(start, end):
    start_dt = datetime.combine(date.today(), start)
    end_dt = datetime.combine(date.today(), end)

    if end_dt < start_dt:
        end_dt += timedelta(days=1)

    minutes = Decimal((end_dt - start_dt).total_seconds()) / Decimal(60)
    return (minutes / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def make_date(match, default_year):
    raw_year = match.group("year")
    year = default_year

    if raw_year:
        year = int(raw_year)
        if year < 100:
            year += 2000

    return date(year, int(match.group("month")), int(match.group("day")))


def strip_wrapping_punctuation(text):
    return text.strip(" :-–—,;")


def consume_employee_hint(text, aliases):
    for alias in sorted(aliases, key=len, reverse=True):
        alias_normalized = normalize_alias(alias)
        if not alias_normalized:
            continue

        pattern = re.compile(rf"^@?{re.escape(alias_normalized)}(?=$|[\s:,\-–—])", re.IGNORECASE)
        match = pattern.match(text)

        if match and match.end() == len(text):
            return alias, ""

        if match:
            return alias, strip_wrapping_punctuation(text[match.end() :])

    return "", text


def remove_known_work_word(text, patterns):
    result = text
    for pattern in patterns:
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
    return normalize_text(result)


def parse_shift_message(text, aliases=(), default_year=None):
    default_year = int(default_year or date.today().year)
    source = normalize_text(text)

    if not source:
        raise ParseError("Пустое сообщение.")

    employee_hint, remaining = consume_employee_hint(source, aliases)
    date_match = DATE_RE.match(remaining)

    if not date_match:
        raise ParseError("Сообщение должно начинаться с даты в формате 01.04.")

    parsed_date = make_date(date_match, default_year)
    remaining = strip_wrapping_punctuation(remaining[date_match.end() :])

    if not employee_hint:
        employee_hint, remaining = consume_employee_hint(remaining, aliases)

    lower_remaining = remaining.lower()
    only_natasha_match = re.search(r"\((?:только\s+для\s+)?наташ[аи]\)", lower_remaining)
    if only_natasha_match:
        employee_hint = employee_hint or "Наташа"
        remaining = normalize_text(re.sub(r"\((?:только\s+для\s+)?наташ[аи]\)", " ", remaining, flags=re.IGNORECASE))

    companion_count = 0
    companion_match = COMPANION_RE.search(remaining)
    if companion_match:
        companion_count = int(companion_match.group("count"))
        remaining = normalize_text(COMPANION_RE.sub(" ", remaining))

    start_time = None
    end_time = None
    hours = Decimal("0.00")
    time_match = TIME_RANGE_RE.search(remaining)
    if time_match:
        start_time = time(
            int(time_match.group("start_hour")),
            int(time_match.group("start_minute")),
        )
        end_time = time(
            int(time_match.group("end_hour")),
            int(time_match.group("end_minute")),
        )
        hours = calculate_hours(start_time, end_time)
        remaining = normalize_text(TIME_RANGE_RE.sub(" ", remaining, count=1))

    normalized_remaining = remaining.lower()
    work_type = WorkType.DEFAULT_SHIFT

    if "фотобар" in normalized_remaining:
        work_type = WorkType.PHOTOBAR
        remaining = remove_known_work_word(remaining, (r"фотобар",))
    elif "покраск" in normalized_remaining and "циклорам" in normalized_remaining:
        work_type = WorkType.CYCLORAMA_PAINTING
        remaining = remove_known_work_word(remaining, (r"покраск\w*", r"циклорам\w*"))
    elif "уборк" in normalized_remaining:
        work_type = WorkType.CLEANING
        remaining = remove_known_work_word(remaining, (r"уборк\w*",))

    return ParsedMessage(
        date=parsed_date,
        employee_hint=employee_hint,
        work_type=work_type,
        start_time=start_time,
        end_time=end_time,
        hours=hours,
        companion_count=companion_count,
        comment=strip_wrapping_punctuation(remaining),
    )
