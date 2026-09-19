import asyncio
import time
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from apps.offers.models import WorkerHeartbeat


class Command(BaseCommand):
    help = "Deliver offers, update Telegram messages, expire shifts and remove old local images."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--healthcheck", action="store_true")

    def handle(self, *args, **options):
        if options["healthcheck"]:
            if not WorkerHeartbeat.objects.filter(name="offers", updated_at__gt=timezone.now() - timedelta(minutes=3)).exists():
                raise CommandError("Offer queue worker is not ready")
            return
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Set TELEGRAM_BOT_TOKEN")
        asyncio.run(self.work(options["once"]))

    async def work(self, once):
        from aiogram import Bot
        from apps.offers.delivery import run_once
        from apps.offers.service import expire_offers, purge_images
        maintenance = 0
        async with Bot(settings.TELEGRAM_BOT_TOKEN) as bot:
            while True:
                await sync_to_async(close_old_connections)()
                if time.monotonic() - maintenance > 30:
                    await sync_to_async(expire_offers)()
                    await sync_to_async(purge_images)()
                    maintenance = time.monotonic()
                worked = await run_once(bot)
                if once:
                    return
                if not worked:
                    await asyncio.sleep(.25)
