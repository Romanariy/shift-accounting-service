from django.core.management.base import BaseCommand
from apps.offers.client_scans import enqueue_existing


class Command(BaseCommand):
    help = "Queue automatic client bonus OCR for existing offer photographs without republishing offers."

    def handle(self, *args, **options):
        self.stdout.write(f"Queued offer photograph sets: {enqueue_existing()}")
