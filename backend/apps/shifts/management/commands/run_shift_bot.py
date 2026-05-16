from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from asgiref.sync import sync_to_async

from apps.shifts.ingest import ingest_telegram_message
from apps.shifts.models import TelegramSource
from apps.shifts.parser import ParseError


def source_is_allowed(message):
    chat_id = message.chat.id
    thread_id = getattr(message, "message_thread_id", None)

    sources = TelegramSource.objects.filter(chat_id=chat_id, is_active=True)
    if not sources.exists():
        return True

    return sources.filter(thread_id=thread_id).exists() or sources.filter(thread_id__isnull=True).exists()


class Command(BaseCommand):
    help = "Запускает Telegram-бота учета смен."

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise CommandError("Укажите TELEGRAM_BOT_TOKEN в окружении.")

        try:
            from aiogram import Bot, Dispatcher
            from aiogram.types import Message
        except ImportError as error:
            raise CommandError("Установите aiogram: pip install -r requirements.txt") from error

        bot = Bot(token=token)
        dispatcher = Dispatcher()

        @dispatcher.message()
        async def handle_message(message: Message):
            if not message.text or not await sync_to_async(source_is_allowed)(message):
                return

            author = message.from_user
            username = author.username if author and author.username else ""
            user_id = author.id if author else None

            try:
                result = await sync_to_async(ingest_telegram_message)(
                    message.text,
                    author_username=username,
                    author_user_id=user_id,
                    chat_id=message.chat.id,
                    thread_id=getattr(message, "message_thread_id", None),
                    message_id=message.message_id,
                )
            except ParseError as error:
                await message.reply(f"Не смог разобрать смену: {error}")
                return

            await message.reply(result.message)

        self.stdout.write(self.style.SUCCESS("Shift bot started."))
        dispatcher.run_polling(bot)
