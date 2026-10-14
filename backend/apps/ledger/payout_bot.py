"""Admin payout callbacks and a persistent, revisioned Telegram edit queue."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.shifts.dates import date_label
from apps.shifts.models import Employee
from .engine import digest, lock
from .models import EmployeePayoutMessage, Record
from . import payouts


def groups(item):
    rows = list(Record.objects.filter(pk__in=item.record_ids, deleted_at=None, kind__in=("service", "oneoff"))
                .select_related("employee", "organization", "service").order_by("id"))
    result = defaultdict(list)
    for r in rows:
        if r.employee_id and r.organization_id and r.date >= payouts.START:
            result[(r.employee_id, r.organization_id, r.date)].append(r)
    expanded = []
    for key, members in result.items():
        day_rows = list(Record.objects.filter(employee_id=key[0], organization_id=key[1], date=key[2], deleted_at=None,
                       kind__in=("service", "oneoff")).order_by("id"))
        day_paid = payouts.paid_amounts([r.pk for r in day_rows])
        earned = sum((r.amount for r in day_rows), Decimal(0))
        received = sum(day_paid.values(), Decimal(0))
        can_pay = any(r.amount > day_paid.get(r.pk, 0) for r in day_rows) and not any(r.review or r.error or r.calculation_review or r.client_bonus_review for r in day_rows)
        expanded.append({"record": members[0].pk, "employee": members[0].employee.display_name,
                         "organization": members[0].organization.name, "date": str(key[2]),
                         "earned": str(earned), "paid": str(received), "remaining": str(earned - received), "can_pay": can_pay})
    texts = [f'{date_label(r.date, short=True)} · {r.service.name if r.service_id else r.description} · '
             f'{r.organization.name if r.organization_id else "Без организации"} · {r.employee_name} · {r.amount:,.2f} ₽'
             + (f' · {r.description}' if r.service_id and r.description else "")
             + (" · нужно проверить" if r.review or r.error or r.calculation_review or r.client_bonus_review else "") for r in rows]
    texts.extend(r.calculation_note for r in rows if r.calculation_note)
    return expanded, texts


def presentation(item):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    data, texts = groups(item)
    summary = [f'{r["employee"]} · {r["organization"]} · {date_label(r["date"])}: выплачено {r["paid"]} ₽, '
               + (f'переплата {abs(Decimal(r["remaining"]))} ₽' if Decimal(r["remaining"]) < 0 else f'осталось {r["remaining"]} ₽') for r in data]
    body = ("Записано:\n" + "\n".join(texts) + ("\n\nВыплаты за день:\n" + "\n".join(summary) if summary else ""))[:4000]
    buttons = [[InlineKeyboardButton(text=("Оплатил" if len(data) == 1 else f'Оплатил · {r["employee"]} · {r["organization"]} · {date_label(r["date"], short=True)}')[:64],
                callback_data=f'payout:day:{item.pk}:{item.revision}:{r["record"]}')] for r in data if r["can_pay"]]
    return body, InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


@transaction.atomic
def register_message(chat_id, message_id, record_ids, source_message_id=None):
    lock()
    item, _ = EmployeePayoutMessage.objects.get_or_create(chat_id=chat_id, message_id=message_id, defaults={"record_ids": record_ids})
    item.record_ids, item.source_message_id = record_ids, source_message_id
    item.fingerprint = digest(groups(item))
    item.save()
    return item


@transaction.atomic
def existing_message(chat_id, source_message_id, edited=False):
    lock()
    item = EmployeePayoutMessage.objects.filter(chat_id=chat_id, source_message_id=source_message_id).exclude(state="gone").first()
    if not item:
        return False
    if edited:
        ids = list(Record.objects.filter(chat_id=chat_id, message_id=source_message_id, deleted_at=None).values_list("id", flat=True))
        item.record_ids = ids
        item.save(update_fields=("record_ids",))
        refresh_messages()
    return True


@transaction.atomic
def refresh_messages():
    lock()
    for item in EmployeePayoutMessage.objects.exclude(state="gone"):
        current = digest(groups(item))
        if current != item.fingerprint:
            item.fingerprint, item.revision = current, item.revision + 1
            if item.state != "sending":
                item.state = "pending"
            item.retry_at = None
            item.save()


@transaction.atomic
def claim():
    lock()
    now = timezone.now()
    EmployeePayoutMessage.objects.filter(state="sending", started_at__lt=now - timedelta(minutes=5)).update(state="pending", lease=None)
    item = EmployeePayoutMessage.objects.filter(state="pending").filter(Q(retry_at=None) | Q(retry_at__lte=now)).order_by("id").first()
    if not item:
        return None
    item.state, item.started_at, item.lease = "sending", now, uuid4()
    item.attempts += 1
    item.save()
    text, keyboard = presentation(item)
    return item.pk, item.lease, item.revision, item.chat_id, item.message_id, text, keyboard


@transaction.atomic
def finish(pk, lease, revision, error="", gone=False):
    lock()
    item = EmployeePayoutMessage.objects.get(pk=pk)
    if item.lease != lease:
        # A slow old HTTP edit can arrive AFTER the replacement worker's edit.
        # Re-assert current content, without stealing an active worker's lease.
        if not error and not gone and revision < item.revision and item.state != "gone":
            item.revision += 1
            if item.state != "sending":
                item.state = "pending"
            item.retry_at = None
            item.save(update_fields=("revision", "state", "retry_at"))
        return
    item.lease, item.error = None, error[:1000]
    if gone:
        item.state = "gone"
    elif error:
        item.state, item.retry_at = "pending", timezone.now() + timedelta(seconds=min(3600, 15 * 2 ** min(item.attempts, 8)))
    else:
        item.delivered_revision = revision
        item.state = "sent" if item.revision == revision else "pending"
        item.retry_at = None
    item.save()


async def update_once(bot):
    job = await sync_to_async(claim)()
    if not job:
        return False
    pk, lease, revision, chat, message, text, markup = job
    try:
        await bot.edit_message_text(chat_id=chat, message_id=message, text=text, reply_markup=markup)
    except Exception as error:
        value = str(error)
        unchanged = "message is not modified" in value.lower()
        gone = any(s in value.lower() for s in ("message to edit not found", "message can't be edited", "chat not found", "bot was kicked", "bot was blocked"))
        await sync_to_async(finish)(pk, lease, revision, "" if unchanged else value, gone)
    else:
        await sync_to_async(finish)(pk, lease, revision)
    return True


@transaction.atomic
def day_preview(message_id, revision, record_id, user_id, chat_id, telegram_message_id):
    payouts.authorize(user_id)
    item = EmployeePayoutMessage.objects.get(pk=message_id, chat_id=chat_id, message_id=telegram_message_id)
    if item.revision != revision or record_id not in item.record_ids:
        raise ValidationError("Кнопка устарела. Обновите сообщение или используйте /payouts.")
    row = Record.objects.get(pk=record_id, deleted_at=None)
    return payouts.preview({"employee": row.employee_id, "organization": row.organization_id, "date": str(row.date)},
                           actor=f"telegram:{user_id}", user_id=user_id)


@transaction.atomic
def navigation(user_id, action="employees", employee=None, month=None, offset=0):
    from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K
    payouts.authorize(user_id)
    offset = max(0, int(offset))
    if action == "employees":
        rows = list(Employee.objects.filter(record__date__gte=payouts.START, record__deleted_at=None).distinct().order_by("short_name", "id"))
        buttons = [[B(text=e.display_name, callback_data=f'payout:months:{e.pk}:0')] for e in rows[offset:offset + 10]]
        prefix, text = "payout:employees:", "Выберите сотрудника:"
    elif action == "months":
        today = timezone.now().astimezone(payouts.ZONE).date()
        rows, cursor = [], today.replace(day=1)
        while cursor >= payouts.START:
            rows.append(cursor.strftime("%Y-%m"))
            cursor = (cursor - timedelta(days=1)).replace(day=1)
        buttons = [[B(text=m, callback_data=f'payout:days:{employee}:{m}:0')] for m in rows[offset:offset + 10]]
        prefix, text = f"payout:months:{employee}:", "Выберите месяц выполнения работ:"
    else:
        start = date.fromisoformat(month + "-01")
        members = list(Record.objects.filter(employee_id=employee, date__year=start.year, date__month=start.month,
                      date__gte=payouts.START, date__lte=timezone.now().astimezone(payouts.ZONE).date(), deleted_at=None,
                      kind__in=("service", "oneoff"), organization__isnull=False).select_related("organization").order_by("-date", "organization_id", "id"))
        paid = payouts.paid_amounts([r.pk for r in members])
        collected = {}
        for r in members:
            if r.amount > paid.get(r.pk, 0):
                collected.setdefault((r.organization_id, r.date), r)
        rows = list(collected.values())
        buttons = [[B(text=f'{date_label(r.date)} · {r.organization.name}', callback_data=f'payout:navday:{r.pk}')] for r in rows[offset:offset + 10]]
        prefix, text = f"payout:days:{employee}:{month}:", "Неоплаченные дни:" if rows else "Неоплаченных дней нет."
    paging = []
    if offset:
        paging.append(B(text="← Назад", callback_data=prefix + str(max(0, offset - 10))))
    if offset + 10 < len(rows):
        paging.append(B(text="Далее →", callback_data=prefix + str(offset + 10)))
    if paging:
        buttons.append(paging)
    if action != "employees":
        buttons.append([B(text="Сотрудники", callback_data="payout:employees:0")])
    return text, K(inline_keyboard=buttons)


@transaction.atomic
def nav_preview(record_id, user_id):
    payouts.authorize(user_id)
    row = Record.objects.get(pk=record_id, deleted_at=None)
    return payouts.preview({"employee": row.employee_id, "organization": row.organization_id, "date": str(row.date)},
                          actor=f"telegram:{user_id}", user_id=user_id)


def preview_text(draft):
    first = draft["lines"][0]
    lines = [f'Выплата · {first["employee_name"]} · {first["organization_name"]} · {date_label(first["date"])}']
    lines.extend(f'{r["service_name"]}: начислено {r["earned"]} ₽, выплачено {r["paid"]} ₽, к выплате {r["to_pay"]} ₽' for r in draft["lines"])
    lines.append(f'Итого передано сотруднику: {draft["amount"]} ₽. Подтвердить?')
    return "\n".join(lines)[:4000]


async def callback(query, bot):
    from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K
    try:
        args = query.data.split(":")
        action = args[1]
        if action in ("employees", "months", "days"):
            if not query.message or query.message.chat.type != "private":
                raise ValidationError("Откройте /payouts в личном чате.")
            kwargs = {"offset": int(args[-1])}
            if action in ("months", "days"):
                kwargs["employee"] = int(args[2])
            if action == "days":
                kwargs["month"] = args[3]
            text, markup = await sync_to_async(navigation)(query.from_user.id, action, **kwargs)
            await query.answer()
            await query.message.edit_text(text, reply_markup=markup)
            return
        if action == "confirm":
            result = await sync_to_async(payouts.confirm)({"token": args[2]}, actor=f"telegram:{query.from_user.id}", user_id=query.from_user.id)
            await query.answer("Выплата уже записана" if result.get("already_paid") else "Выплата записана")
            await bot.send_message(query.from_user.id, f'Выплата №{result["id"]}: {result["amount"]} ₽. Записано.')
            return
        if action == "day":
            if not query.message:
                raise ValidationError("Сообщение не найдено.")
            draft = await sync_to_async(day_preview)(int(args[2]), int(args[3]), int(args[4]), query.from_user.id,
                                                     query.message.chat.id, query.message.message_id)
        elif action == "navday":
            draft = await sync_to_async(nav_preview)(int(args[2]), query.from_user.id)
        else:
            raise ValidationError("Неизвестное действие.")
        await query.answer()
        await bot.send_message(query.from_user.id, preview_text(draft),
                              reply_markup=K(inline_keyboard=[[B(text="Подтвердить выплату", callback_data=f'payout:confirm:{draft["token"]}')]]))
    except (ValidationError, ObjectDoesNotExist, ValueError, IndexError, KeyError) as error:
        await query.answer(" ".join(error.messages)[:190] if isinstance(error, ValidationError) else "Данные или кнопка устарели. Используйте /payouts.", show_alert=True)
