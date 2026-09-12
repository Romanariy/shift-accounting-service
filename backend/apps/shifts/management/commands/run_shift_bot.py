from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ledger.billing import approve, refresh
from apps.ledger.engine import lock
from apps.ledger.ingest import ingest, source_kind
from apps.ledger.models import Batch, TelegramContact
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


class Command(BaseCommand):
    help = "Запускает Telegram-бота учёта услуг, расходов и подтверждения счетов."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Укажите TELEGRAM_BOT_TOKEN в окружении.")
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import CommandStart
        from aiogram.types import CallbackQuery, Message
        bot, dispatcher = Bot(token=settings.TELEGRAM_BOT_TOKEN), Dispatcher()

        @dispatcher.message(CommandStart(), F.chat.type == "private")
        async def start(message: Message):
            await sync_to_async(register_contact)(message.from_user)
            await message.answer("Вы подключены. Администратор может назначить вас получателем счетов или утверждающим.\nВаш Telegram ID: " + str(message.from_user.id))

        @dispatcher.callback_query(F.data.startswith("ledger:"))
        async def callback(query: CallbackQuery):
            await query.answer()
            try:
                _, action, bid, version = query.data.split(":")
                if action == "approve":
                    result = await sync_to_async(approve)(int(bid), int(version), actor=f"telegram:{query.from_user.id}", telegram_user=query.from_user.id)
                    text = "Пакет подтверждён. Счета поставлены в очередь отправки." if result["approved"] else result["message"]
                elif action == "refresh":
                    await sync_to_async(refresh_authorized)(int(bid), int(version), query.from_user.id)
                    text = "Отчёты обновлены и поставлены в очередь доставки вам."
                else:
                    return
            except (ValidationError, ValueError, Batch.DoesNotExist) as error:
                text = " ".join(error.messages) if isinstance(error, ValidationError) else "Пакет не найден или кнопка устарела."
            await bot.send_message(query.from_user.id, text)

        async def process(message: Message, edited=False):
            if not message.text or message.chat.type == "private":
                return
            kind = await sync_to_async(source_kind)(message.chat.id, getattr(message, "message_thread_id", None))
            if not kind:
                return
            author = message.from_user
            try:
                rows = await sync_to_async(ingest)(message.text, chat_id=message.chat.id, message_id=message.message_id,
                    thread_id=getattr(message, "message_thread_id", None), user_id=author.id if author else None,
                    username=(author.username or "") if author else "", author_name=author.full_name if author else "",
                    expense=kind == "expense", today=timezone.localtime(message.date).date(), edited=edited)
                lines = ["Записи обновлены:" if edited else "Записано:"]
                for r in rows:
                    title = r.service.name if r.service_id else r.description
                    if r.service_id and r.description:
                        title += " — " + r.description
                    lines.append(f"{r.date:%d.%m} · {title} · {r.organization.name} · {r.employee_name or r.author_name} · {r.amount:,.2f} ₽" + (" · нужно проверить" if r.review else ""))
                current_ids = {r.pk for r in rows}
                changes = getattr(rows[0], "allocation_changes", []) if rows else []
                for change in changes:
                    if change["id"] not in current_ids:
                        lines.append(f'Перераспределено: {change["employee_name"] or "Без исполнителя"} · запись №{change["id"]}: {change["before"]} → {change["after"]} ₽')
                response = "\n".join(lines)
            except ValidationError as error:
                response = "Не удалось сохранить: " + " ".join(error.messages)
            await message.reply(response[:4000])

        @dispatcher.message()
        async def on_message(message: Message):
            await process(message)

        @dispatcher.edited_message()
        async def on_edit(message: Message):
            await process(message, edited=True)

        self.stdout.write(self.style.SUCCESS("Service bot started."))
        dispatcher.run_polling(bot)
