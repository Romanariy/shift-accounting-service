import asyncio
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ledger.billing import schedule
from apps.ledger.engine import accrue
from apps.ledger.worker import deliver_once
from apps.ledger import earnings


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
                errors = []
                # Invoice validation failures must not block the independent earnings report.
                for name, step in (("accrue", accrue), ("invoices", schedule), ("earnings", earnings.schedule)):
                    try:
                        await sync_to_async(step)()
                    except Exception as error:
                        logging.getLogger(__name__).exception("Billing step failed: %s", name)
                        errors.append(error)
                if bot:
                    for sender in (earnings.deliver_once, deliver_once):
                        try:
                            for _ in range(50):
                                if not await sender(bot):
                                    break
                                await asyncio.sleep(1)
                        except Exception as error:
                            logging.getLogger(__name__).exception("Delivery cycle failed: %s", sender.__module__)
                            errors.append(error)
                if once:
                    if errors:
                        raise errors[0]
                    break
                await asyncio.sleep(30)
        finally:
            if bot:
                await bot.session.close()
