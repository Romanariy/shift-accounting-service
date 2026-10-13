import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from apps.offers.models import WorkerHeartbeat


class Command(BaseCommand):
    help = "Local CPU OCR queue worker; loads models once and never contacts Telegram."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--healthcheck", action="store_true")

    def handle(self, *args, **options):
        if options["healthcheck"]:
            if not WorkerHeartbeat.objects.filter(name="recognition", detail="models_ready", updated_at__gt=timezone.now() - timedelta(minutes=3)).exists():
                raise CommandError("OCR worker is not ready")
            return
        from apps.offers.recognizer import LocalOCR
        from apps.offers.recognition_worker import run_once
        from apps.offers.client_scans import enqueue_existing
        ocr = LocalOCR(settings.OFFER_MODEL_DIR, settings.OFFER_OCR_THREADS)
        # A real inference validates loaded weights before advertising readiness.
        import numpy as np
        ocr.read(np.full((64, 320, 3), 255, dtype=np.uint8))
        self.stdout.write("OCR models ready; confirmation required unless independent quality gate passes.")
        next_scan = 0
        while True:
            close_old_connections()
            if time.monotonic() >= next_scan:
                queued = enqueue_existing()
                if queued:
                    self.stdout.write(f"Client bonuses: queued {queued} existing offer photograph sets.")
                next_scan = time.monotonic() + 30
            worked = run_once(ocr)
            if options["once"]:
                return
            if not worked:
                time.sleep(.25)
