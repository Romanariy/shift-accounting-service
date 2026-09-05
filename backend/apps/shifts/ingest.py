from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from .audit import log_change
from .constants import EntrySource, EntryStatus, WorkType
from .models import CompanionEntry, Employee, Organization, ShiftEntry
from .parser import ParseError, parse_shift_message
from .payments import calculate_companion_amount, calculate_shift_amount
from .serializers import companion_to_dict, shift_to_dict
from .sync import queue_sync_change, try_sync_once


@dataclass(frozen=True)
class IngestResult:
    shift: ShiftEntry | None
    companion: CompanionEntry | None
    message: str
    needs_review: bool


def normalize_lookup(value):
    return (value or "").strip().lower().lstrip("@")


def employee_aliases(employee):
    aliases = [employee.short_name, employee.full_name, employee.telegram_username]
    aliases.extend(employee.aliases or [])
    return [alias for alias in aliases if alias]


def get_all_aliases():
    aliases = []
    for employee in Employee.objects.filter(is_active=True):
        aliases.extend(employee_aliases(employee))
        if employee.telegram_username:
            aliases.append(f"@{employee.telegram_username}")
    return aliases


def find_employee_by_hint(hint):
    normalized_hint = normalize_lookup(hint)
    if not normalized_hint:
        return None

    for employee in Employee.objects.filter(is_active=True):
        for alias in employee_aliases(employee):
            if normalize_lookup(alias) == normalized_hint:
                return employee

    return None


def find_employee_by_telegram(username="", user_id=None):
    if user_id:
        employee = Employee.objects.filter(is_active=True, telegram_user_id=user_id).first()
        if employee:
            return employee

    username = normalize_lookup(username)
    if username:
        return Employee.objects.filter(
            is_active=True,
            telegram_username__iexact=username,
        ).first()

    return None


def resolve_work_type(parsed_work_type, employee):
    if parsed_work_type != WorkType.DEFAULT_SHIFT:
        return parsed_work_type

    if employee:
        return employee.default_work_type

    return WorkType.SMALL_ADMIN


def maybe_try_sync():
    if getattr(settings, "SHIFT_SYNC_AFTER_WRITE", True):
        try_sync_once(limit=10)


@transaction.atomic
def ingest_telegram_message(
    text,
    author_username="",
    author_user_id=None,
    chat_id=None,
    thread_id=None,
    message_id=None,
    default_year=None,
):
    parsed = parse_shift_message(text, aliases=get_all_aliases(), default_year=default_year,
                                 organizations=Organization.objects.filter(is_active=True))
    explicit_employee = find_employee_by_hint(parsed.employee_hint)
    author_employee = find_employee_by_telegram(author_username, author_user_id)
    employee = explicit_employee or author_employee
    status = EntryStatus.CONFIRMED if employee else EntryStatus.NEEDS_REVIEW
    work_type = resolve_work_type(parsed.work_type, employee)
    actor = f"telegram:@{author_username}" if author_username else "telegram"

    common = dict(
        date=parsed.date,
        organization_id=parsed.organization_id,
        employee=employee,
        employee_name_snapshot=employee.display_name if employee else parsed.employee_hint,
        comment=parsed.comment,
        source=EntrySource.TELEGRAM,
        status=status,
        telegram_chat_id=chat_id,
        telegram_thread_id=thread_id,
        telegram_message_id=message_id,
        telegram_author_username=author_username or "",
        telegram_author_user_id=author_user_id,
        raw_text=text,
    )
    shift = None
    if parsed.has_shift:
        try:
            amount = calculate_shift_amount(work_type, parsed.hours, parsed.date, parsed.organization_id)
        except ValueError as error:
            raise ParseError(str(error)) from error
        shift = ShiftEntry.objects.create(
            **common, work_type=work_type, start_time=parsed.start_time,
            end_time=parsed.end_time, hours=parsed.hours, calculated_amount=amount,
        )
        shift_payload = shift_to_dict(shift)
        log_change("shift", shift.id, "create", actor=actor, after=shift_payload)
        queue_sync_change("shift", shift.id, "create", shift_payload)

    companion = None
    if parsed.companion_count > 0:
        companion = CompanionEntry.objects.create(
            **common,
            count=parsed.companion_count,
            calculated_amount=calculate_companion_amount(parsed.companion_count, parsed.date),
        )
        companion_payload = companion_to_dict(companion)
        log_change("companion", companion.id, "create", actor=actor, after=companion_payload)
        queue_sync_change("companion", companion.id, "create", companion_payload)

    transaction.on_commit(maybe_try_sync)

    review_note = " Запись требует проверки: сотрудник не найден." if status == EntryStatus.NEEDS_REVIEW else ""
    companion_note = f", сопровождений: {parsed.companion_count}" if parsed.companion_count else ""
    entry = shift or companion
    total = (shift.calculated_amount if shift else 0) + (companion.calculated_amount if companion else 0)
    description = shift.get_work_type_display() if shift else "Сопровождение"
    return IngestResult(
        shift=shift,
        companion=companion,
        message=(
            f"Записал: {entry.date:%d.%m.%Y}, {entry.employee_name_snapshot or 'без сотрудника'}, "
            f"{entry.organization.name}, {description}, сумма {total} руб.{companion_note}.{review_note}"
        ),
        needs_review=status == EntryStatus.NEEDS_REVIEW,
    )


def try_parse_for_preview(text, default_year=None):
    try:
        return {"ok": True, "parsed": parse_shift_message(text, aliases=get_all_aliases(), default_year=default_year,
                                                         organizations=Organization.objects.filter(is_active=True))}
    except ParseError as error:
        return {"ok": False, "error": str(error)}
