from apps.shifts.dates import date_label
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ledger.billing import approve, refresh
from apps.ledger.engine import lock
from apps.ledger.ingest import ingest, source_kind
from apps.ledger.models import Batch, Invoice, TelegramContact
from apps.ledger.payments import mark_paid
from apps.shifts.models import TelegramSource


def source_is_allowed(message):
    sources = TelegramSource.objects.filter(chat_id=message.chat.id, is_active=True)
    return not sources.exists() or sources.filter(thread_id=getattr(message, "message_thread_id", None)).exists() or sources.filter(thread_id__isnull=True).exists()


@transaction.atomic
def register_contact(user):
    lock()
    TelegramContact.objects.update_or_create(user_id=user.id, defaults={"name": user.full_name, "username": user.username or ""})


@transaction.atomic
def refresh_authorized(batch_id, version, user_id):
    config = lock()
    batch = Batch.objects.get(pk=batch_id)
    if not config.approver_id or config.approver.user_id != user_id or batch.approver_id_snapshot != user_id:
        raise ValidationError("Обновление доступно только назначенному утверждающему.")
    if batch.version != version:
        raise ValidationError("Откройте последнюю версию пакета.")
    refresh(batch)


def ingest_response(text, **kwargs):
    # Keep lazy ORM reads on the same synchronous side as persistence.
    rows = ingest(text, **kwargs)
    lines = ["Записи обновлены:" if kwargs.get("edited") else "Записано:"]
    if not rows:
        return "Новых записей нет: записи этого сообщения ранее удалены."
    for r in rows:
        title = r.service.name if r.service_id else r.description
        if r.service_id and r.description:
            title += " — " + r.description
        organization = r.organization.name if r.organization_id else "Без организации"
        lines.append(f"{date_label(r.date, short=True)} · {title} · {organization} · {r.employee_name or r.author_name} · {r.amount:,.2f} ₽" + (" · нужно проверить" if r.review or r.error or r.calculation_review else ""))
        if r.calculation_note:
            lines.append(r.calculation_note)
    current_ids = {r.pk for r in rows}
    for change in getattr(rows[0], "allocation_changes", []):
        if change["id"] not in current_ids:
            lines.append(f'Перераспределено: {change["employee_name"] or "Без исполнителя"} · запись №{change["id"]}: {change["before"]} → {change["after"]} ₽')
    return "\n".join(lines)


async def process_message(message, edited=False):
    if not message.text or message.chat.type == "private":
        return
    logger = logging.getLogger(__name__)
    kind = None
    success = False
    try:
        kind = await sync_to_async(source_kind)(message.chat.id, getattr(message, "message_thread_id", None))
        if not kind:
            return
        author = message.from_user
        response = await sync_to_async(ingest_response)(message.text, chat_id=message.chat.id, message_id=message.message_id,
            thread_id=getattr(message, "message_thread_id", None), user_id=author.id if author else None,
            username=(author.username or "") if author else "", author_name=author.full_name if author else "",
            expense=kind == "expense", today=timezone.localtime(message.date).date(), edited=edited)
        success = True
    except ValidationError as error:
        response = "Не удалось сохранить: " + " ".join(error.messages)
    except Exception:
        logger.exception("Record processing failed: chat=%s message=%s", message.chat.id, message.message_id)
        response = "Не удалось подтвердить результат записи. Проверьте журнал на сайте."
    try:
        if kind == "service" and success:
            from apps.ledger.payout_bot import existing_message
            if await sync_to_async(existing_message)(message.chat.id, message.message_id, edited):
                return
        sent = await message.reply(response[:4000])
        if kind == "service" and isinstance(getattr(sent, "message_id", None), int) and isinstance(getattr(getattr(sent, "chat", None), "id", None), int):
            from apps.ledger.payout_bot import register_message
            from apps.ledger.models import Record
            ids = await sync_to_async(list)(Record.objects.filter(chat_id=message.chat.id, message_id=message.message_id, deleted_at=None).values_list("id", flat=True))
            if ids:
                await sync_to_async(register_message)(sent.chat.id, sent.message_id, ids, message.message_id)
    except Exception:
        logger.exception("Record confirmation delivery failed: chat=%s message=%s", message.chat.id, message.message_id)


async def process_receipt(message, edited=False):
    from apps.ledger.receipt_bot import matches, ingest_receipt
    from apps.ledger.models import OrganizationReceipt
    from zoneinfo import ZoneInfo
    known = edited and await sync_to_async(OrganizationReceipt.objects.filter(telegram_chat_id=message.chat.id, telegram_message_id=message.message_id).exists)()
    if not matches(message.text) and not known:
        return False
    if message.chat.type != "private" and await sync_to_async(source_kind)(message.chat.id, getattr(message, "message_thread_id", None)) != "service":
        return False
    try:
        result = await sync_to_async(ingest_receipt)(message.text or "", user_id=message.from_user.id,
            chat_id=message.chat.id, message_id=message.message_id, edited=edited,
            today=message.date.astimezone(ZoneInfo("Asia/Yekaterinburg")).date(),
            event_time=getattr(message, "edit_date", None) or message.date)
        text = ("Получено" if result["kind"] == "receipt" else "Вычет") + f': {result["amount"]} ₽ · {date_label(result["date"])} · операция №{result["id"]}. Сохранено.'
    except ValidationError as error:
        text = "Не удалось сохранить: " + " ".join(error.messages)
    except Exception:
        logging.getLogger(__name__).exception("Receipt processing failed")
        text = "Не удалось подтвердить запись. Проверьте поступления студии на сайте."
    await message.reply(text)
    return True


async def process_ledger_callback(query, bot):
    await query.answer()
    try:
        _, action, entity_id, version, *revision = query.data.split(":")
        if len(revision) > 1 or (revision and action != "paid"):
            raise ValueError("Некорректная кнопка")
        if action == "approve":
            result = await sync_to_async(approve)(int(entity_id), int(version), actor=f"telegram:{query.from_user.id}", telegram_user=query.from_user.id)
            text = "Пакет подтверждён. Счета поставлены в очередь отправки." if result["approved"] else result["message"]
        elif action == "refresh":
            await sync_to_async(refresh_authorized)(int(entity_id), int(version), query.from_user.id)
            text = "Отчёты обновлены и поставлены в очередь доставки вам."
        elif action == "paid":
            if not query.message:
                raise ValidationError("Откройте личное сообщение со счётом для подтверждения оплаты.")
            result = await sync_to_async(mark_paid)(int(entity_id), query.from_user.id, version=int(version),
                message_id=query.message.message_id, chat_id=query.message.chat.id,
                payment_revision=int(revision[0]) if revision else None)
            text = "Оплата этого счёта уже подтверждена." if result["already_paid"] else "Оплата подтверждена. Счёт закрыт для всех получателей организации."
        else:
            return
    except (ValidationError, ValueError, Batch.DoesNotExist, Invoice.DoesNotExist) as error:
        text = " ".join(error.messages) if isinstance(error, ValidationError) else "Счёт или пакет не найден, либо кнопка устарела."
    await bot.send_message(query.from_user.id, text)


class Command(BaseCommand):
    help = "Запускает Telegram-бота учёта услуг, расходов и подтверждения счетов."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Укажите TELEGRAM_BOT_TOKEN в окружении.")
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import CommandStart, Command as TelegramCommand
        from aiogram.types import CallbackQuery, Message
        from apps.offers.bot import process_callback as process_offer_callback, process_private
        bot, dispatcher = Bot(token=settings.TELEGRAM_BOT_TOKEN), Dispatcher()

        @dispatcher.message(TelegramCommand("payouts"), F.chat.type == "private")
        async def payout_list(message: Message):
            from apps.ledger.payout_bot import navigation
            try:
                text, markup = await sync_to_async(navigation)(message.from_user.id)
                await message.answer(text, reply_markup=markup)
            except ValidationError as error:
                await message.answer(" ".join(error.messages))

        @dispatcher.callback_query(F.data.startswith("payout:"))
        async def payout_callback(query: CallbackQuery):
            from apps.ledger.payout_bot import callback
            await callback(query, bot)

        @dispatcher.message(CommandStart(), F.chat.type == "private")
        async def start(message: Message):
            await sync_to_async(register_contact)(message.from_user)
            await message.answer("Вы подключены. Администратор может назначить вас получателем счетов или утверждающим.\nВаш Telegram ID: " + str(message.from_user.id))

        @dispatcher.callback_query(F.data.startswith("ledger:"))
        async def callback(query: CallbackQuery):
            await process_ledger_callback(query, bot)

        @dispatcher.callback_query(F.data.startswith("offer:"))
        async def offer_callback(query: CallbackQuery):
            await process_offer_callback(query)

        @dispatcher.message()
        async def on_message(message: Message):
            if await process_receipt(message):
                return
            if await process_private(message, bot):
                return
            await process_message(message)

        @dispatcher.edited_message()
        async def on_edit(message: Message):
            if await process_receipt(message, edited=True):
                return
            await process_message(message, edited=True)

        self.stdout.write(self.style.SUCCESS("Service bot started."))
        dispatcher.run_polling(bot)
