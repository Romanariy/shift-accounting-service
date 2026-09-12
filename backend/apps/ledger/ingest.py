import re
from datetime import date, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.shifts.models import Employee, Organization
from apps.shifts.parser import TIME_RANGE_RE, calculate_hours
from .engine import lock, money
from .models import EmployeePreference, Record, Service, Settings


DATE = r"\d{1,2}\.\d{1,2}(?:\.\d{2}(?:\d{2})?)?"
AMOUNT = r"\d+(?:[.,]\d{1,2})?"
ONEOFF = re.compile(rf"^(?:(?P<who>[^,]+),\s*)?(?P<date>{DATE}),\s*(?P<amount>{AMOUNT})\s*,\s*(?P<description>.+)$")


def parse_date(raw, today):
    try:
        parts = [int(p) for p in raw.split(".")]
        year = parts[2] if len(parts) == 3 else today.year
        return date(year + 2000 if year < 100 else year, parts[1], parts[0])
    except ValueError:
        raise ValidationError("Некорректная дата.")


def employee_for(hint, user_id, username):
    employees = list(Employee.objects.filter(is_active=True))
    if not hint and user_id:
        matches = [e for e in employees if e.telegram_user_id == user_id]
        if matches:
            return matches[0] if len(matches) == 1 else None
    token = (hint or username or "").casefold().lstrip("@").strip()
    matches = [e for e in employees if token and token in {s.casefold().lstrip("@").strip() for s in [e.short_name, e.full_name, e.telegram_username, *e.aliases] if s}]
    return matches[0] if len(matches) == 1 else None


def organization_for(raw):
    token = raw.strip().rstrip(".").casefold()
    matches = [o for o in Organization.objects.filter(is_active=True) if token in {o.name.casefold(), *o.aliases}]
    if len(matches) != 1:
        raise ValidationError("Организация не найдена или неоднозначна.")
    return matches[0]


def remove_organization(text):
    candidates = sorted([(a, o) for o in Organization.objects.filter(is_active=True) for a in [o.name, *o.aliases]], key=lambda p: len(p[0]), reverse=True)
    for alias, org in candidates:
        match = re.search(rf"(?<!\S){re.escape(alias)}\.?$", text, re.I)
        if match:
            return text[:match.start()].strip(), org
    return text, None


def parse(text, *, expense=False, user_id=None, username="", author_name="", today=None):
    today = today or timezone.localdate()
    text = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
    if not text:
        raise ValidationError("Пустое сообщение.")
    author = employee_for("", user_id, username)
    if expense:
        try:
            body, org_text = text.rsplit(",", 1)
        except ValueError:
            raise ValidationError("Формат расхода: Краска, 1500, Фокус.")
        dated = re.match(rf"^({DATE}),\s*(.+)$", body)
        day = parse_date(dated[1], today) if dated else today
        body = dated[2] if dated else body
        match = re.fullmatch(rf"(.+?),\s*({AMOUNT})", body.strip())
        if not match:
            raise ValidationError("Укажите назначение, положительную сумму и организацию.")
        return [Record(kind="expense", date=day, amount=money(match[2]), description=match[1].strip(), organization=organization_for(org_text))]
    # Detect the row grammar before splitting '+': free text is never tokenized as services.
    if re.match(rf"^(?:[^,]+,\s*)?{DATE},", text):
        try:
            body, org_text = text.rsplit(",", 1)
        except ValueError:
            raise ValidationError("Укажите организацию в конце.")
        match = ONEOFF.fullmatch(body.strip())
        if not match:
            raise ValidationError("Формат: [Человек,] 12.04, 1500, Что сделал, Организация.")
        who = (match["who"] or "").strip()
        employee = employee_for(who, user_id, username)
        return [Record(kind="oneoff", date=parse_date(match["date"], today), amount=money(match["amount"]),
                       description=match["description"].strip(), organization=organization_for(org_text), employee=employee,
                       employee_name=employee.display_name if employee else who or author_name or username or str(user_id or "Неизвестный"), review=employee is None)]
    dated = re.match(rf"^({DATE})\s+", text)
    day = parse_date(dated[1], today) if dated else today
    text = text[dated.end():] if dated else text
    # Preserve the existing optional employee prefix for typed service messages.
    employee = author
    for e in Employee.objects.filter(is_active=True):
        found = False
        for alias in sorted([e.short_name, e.full_name, e.telegram_username, *e.aliases], key=len, reverse=True):
            if not alias:
                continue
            m = re.match(rf"^@?{re.escape(alias)}\s+", text, re.I)
            if m:
                employee = employee_for(alias, None, "")
                text = text[m.end():]
                found = True
                break
        if found:
            break
    if not dated:
        dated = re.match(rf"^({DATE})\s+", text)
        if dated:
            day, text = parse_date(dated[1], today), text[dated.end():]
    parts = re.split(r"(?<!\\)\+", text)
    if parts and not parts[0].strip():
        parts = parts[1:]
    records = []
    services = list(Service.objects.filter(active=True))

    def service_prefix(raw, modes=None):
        candidates = []
        for service in services:
            if modes and service.input_type not in modes:
                continue
            for alias in {service.name, *service.aliases}:
                match = re.match(rf"{re.escape(alias)}(?!\w)\.?", raw, re.I)
                if match:
                    candidates.append((match.end(), service))
        if not candidates:
            return None, raw
        length, service = max(candidates, key=lambda item: item[0])
        return service, raw[length:].strip()

    def organization_prefix(raw):
        candidates = []
        for org in Organization.objects.all():
            for alias in {org.name, *org.aliases}:
                match = re.match(rf"{re.escape(alias)}(?=$|[\s,.;:])", raw, re.I)
                if match:
                    candidates.append((match.end(), org))
        if not candidates:
            return None, raw
        length, org = max(candidates, key=lambda item: item[0])
        if not org.is_active:
            raise ValidationError("Указанная организация отключена.")
        return org, raw[length:].lstrip(" .,;:")

    for index, part in enumerate(parts, 1):
        try:
            rest = part.strip().replace(r"\+", "+")
            service, rest = service_prefix(rest)
            time_match = TIME_RANGE_RE.match(rest)
            amount_match = re.match(rf"({AMOUNT})(?=$|\s)", rest)
            value = None
            if service:
                mode = service.input_type
            elif time_match:
                mode = "time"
            elif amount_match:
                mode = "quantity"
            else:
                raise ValidationError("Не удалось определить услугу. Укажите её название или алиас.")
            if mode == "time":
                if not time_match:
                    raise ValidationError("Укажите один интервал времени.")
                rest = rest[time_match.end():].strip()
            elif mode in ("quantity", "amount"):
                if not amount_match:
                    raise ValidationError("Укажите положительное количество или сумму.")
                value = money(amount_match[1])
                if value <= 0:
                    raise ValidationError("Значение должно быть положительным.")
                rest = rest[amount_match.end():].strip()
            if service is None:
                service, rest = service_prefix(rest, {"time"} if mode == "time" else {"quantity", "amount"})
                if service is None and mode == "time":
                    preference = EmployeePreference.objects.filter(employee=employee).select_related("service").first() if employee else None
                    code = employee.default_work_type if employee else "small_admin"
                    service = preference.service if preference else next((s for s in services if s.legacy_code == code), None)
                if not service or not service.active or (mode == "time" and service.input_type != "time"):
                    raise ValidationError("Не удалось определить услугу. Укажите её название или алиас.")
            org, comment = organization_prefix(rest)
            org = org or service.default_organization
            if not org or not org.is_active:
                raise ValidationError("Укажите организацию или настройте организацию услуги по умолчанию.")
            if service.frequency != "entry":
                raise ValidationError("Эта услуга начисляется автоматически. Настройте организацию и период в разделе автоначислений.")
            r = Record(kind="service", service=service, organization=org, date=day, employee=employee, description=comment,
                       employee_name=employee.display_name if employee else author_name or username or str(user_id or "Неизвестный"), review=employee is None)
            if service.input_type == "time":
                r.start_time = time(int(time_match["start_hour"]), int(time_match["start_minute"]))
                r.end_time = time(int(time_match["end_hour"]), int(time_match["end_minute"]))
                r.units = calculate_hours(r.start_time, r.end_time)
                if r.units <= 0:
                    raise ValidationError("Длительность должна быть положительной.")
            elif service.input_type == "quantity":
                if service.legacy_code == "companion" and value != int(value):
                    raise ValidationError("Количество сопровождений должно быть целым.")
                r.units = value
            elif service.input_type == "amount":
                r.amount = value
            records.append(r)
        except (ValidationError, ValueError) as error:
            message = " ".join(error.messages) if isinstance(error, ValidationError) else "Некорректное время."
            raise ValidationError(f"Часть {index}: {message}")
    return records


@transaction.atomic
def ingest(text, *, chat_id, message_id, thread_id=None, user_id=None, username="", author_name="", expense=False, today=None, edited=False):
    from .grouping import commit_records
    lock()
    existing = list(Record.objects.filter(chat_id=chat_id, message_id=message_id).select_related("service", "organization").order_by("part"))
    if existing and not edited:
        return [r for r in existing if r.deleted_at is None]
    parsed = parse(text, expense=expense, user_id=user_id, username=username, author_name=author_name, today=today)
    for i, r in enumerate(parsed):
        if i < len(existing):
            old = existing[i]
            r.pk, r.created_at = old.pk, old.created_at
            r._state.adding = False
            r._state.db = old._state.db
            r.legacy_id, r.legacy_kind = old.legacy_id, old.legacy_kind
            r.calculation_version, r.daily_calculation_id = old.calculation_version, old.daily_calculation_id
            if r.kind == "service" and r.service.input_type != "amount":
                r.amount = old.amount
        r.source, r.author_id, r.author_name = "telegram", user_id, author_name or username
        r.chat_id, r.message_id, r.part, r.raw_text = chat_id, message_id, i, text
    commit_records(parsed, deleted=existing[len(parsed):], actor=f"telegram:{user_id}")
    return parsed


def source_kind(chat_id, thread_id):
    config = Settings.objects.get(pk=1)
    for kind in ("expense", "service"):
        target = getattr(config, f"{kind}_chat")
        topic = getattr(config, f"{kind}_thread")
        if target is not None and target == chat_id and (topic is None or topic == thread_id):
            return kind
    return None
