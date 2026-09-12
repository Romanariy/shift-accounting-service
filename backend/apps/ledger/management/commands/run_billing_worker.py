import asyncio
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ledger.billing import schedule
from apps.ledger.engine import accrue
from apps.ledger.worker import deliver_once


class Command(BaseCommand):
    help = "Начисляет услуги, готовит месячные пакеты и доставляет подтверждённые счета."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        asyncio.run(self.run(options["once"]))

    async def run(self, once):
        from aiogram import Bot
        bot = Bot(settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None
        try:
            while True:
                try:
                    await sync_to_async(accrue)()
                    await sync_to_async(schedule)()
                    if bot:
                        for _ in range(50):
                            if not await deliver_once(bot):
                                break
                            await asyncio.sleep(1)
                except Exception:
                    logging.getLogger(__name__).exception("Billing cycle failed")
                    if once:
                        raise
                if once:
                    break
                await asyncio.sleep(30)
        finally:
            if bot:
                await bot.session.close()
