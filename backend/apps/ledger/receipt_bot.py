import re
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.shifts.audit import log_change
from apps.shifts.models import Employee
from .engine import lock, money, digest
from .ingest import DATE, SIMPLE_AMOUNT, parse_date, remove_organization
from .models import OrganizationReceipt
from .receipts import data


def matches(text):
    return bool(re.match(r"^(получено|вычет)(?:\s|$)", text or "", re.I))


@transaction.atomic
def ingest_receipt(text, *, user_id, chat_id, message_id, today=None, edited=False, event_time=None):
    config = lock()
    admin = config.approver_id and config.approver.user_id == user_id
    if not admin and Employee.objects.filter(is_active=True, telegram_user_id=user_id).count() != 1:
        raise ValidationError("Получения и вычеты доступны главному администратору и сотрудникам с привязанным Telegram ID.")
    old = OrganizationReceipt.objects.filter(telegram_chat_id=chat_id, telegram_message_id=message_id).first()
    normalized = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
    fingerprint = digest(normalized)
    if old:
        if old.deleted_at:
            raise ValidationError("Эта операция удалена на сайте. Создайте новое сообщение.")
        if old.author != f"telegram:{user_id}":
            raise ValidationError("Изменять можно только собственное сообщение.")
        if not edited or old.telegram_fingerprint == fingerprint:
            return data(old)
        if event_time and old.telegram_event_at and event_time <= old.telegram_event_at:
            return data(old)
        if old.updated_at != old.telegram_updated_at:
            raise ValidationError("Операция изменена на сайте. Обновите её на сайте или создайте новую запись.")
    match = re.match(r"^(получено|вычет)\s+(.+)$", normalized, re.I)
    if not match:
        raise ValidationError("Формат: получено [дата] От кого Сумма Студия; вычет [дата] Причина Сумма Студия.")
    body, org = remove_organization(match[2])
    if not org:
        raise ValidationError("Укажите студию в конце сообщения.")
    dated = re.match(rf"^({DATE})\s+(.+)$", body)
    day = parse_date(dated[1], today) if dated else today or timezone.now().astimezone(ZoneInfo("Asia/Yekaterinburg")).date()
    body = dated[2] if dated else body
    content = re.fullmatch(rf"(.+?)\s+({SIMPLE_AMOUNT})", body)
    if not content:
        raise ValidationError("Укажите плательщика или причину, затем сумму и студию.")
    row = old or OrganizationReceipt(telegram_chat_id=chat_id, telegram_message_id=message_id)
    before = data(row) if old else None
    row.kind = "receipt" if match[1].casefold() == "получено" else "deduction"
    row.organization, row.date, row.amount = org, day, money(content[2])
    row.payer = content[1].strip() if row.kind == "receipt" else ""
    row.reason = content[1].strip() if row.kind == "deduction" else ""
    row.source, row.author = "telegram", f"telegram:{user_id}"
    row.telegram_fingerprint = fingerprint
    row.telegram_event_at = event_time
    row.full_clean()
    row.save()
    row.telegram_updated_at = row.updated_at
    row.save(update_fields=("telegram_updated_at",))
    log_change("receipt", row.pk, "update" if old else "create", actor=row.author, before=before, after=data(row))
    return data(row)
