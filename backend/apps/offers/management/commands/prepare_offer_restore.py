"""Quarantine sends from an older database snapshot before resuming Telegram workers."""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.ledger.engine import lock
from apps.shifts.audit import log_change
from apps.offers.models import OfferDelivery, RecognitionJob


class Command(BaseCommand):
    help = "After restoring a backup with workers stopped, require reconciliation of pending Telegram sends."

    @transaction.atomic
    def handle(self, *args, **options):
        lock()
        deliveries = list(OfferDelivery.objects.filter(state__in=("pending", "sending")))
        for delivery in deliveries:
            delivery.state = "unknown"
            delivery.error = "БД восстановлена из резервной копии. Сверьте доставку с Telegram перед повтором."
            delivery.save(update_fields=("state", "error", "updated_at"))
            log_change("shift_offer", delivery.offer_id, "delivery_quarantined_after_restore", "restore",
                after={"delivery": delivery.pk})
        count = RecognitionJob.objects.filter(state="running").update(state="pending", available_at=timezone.now())
        self.stdout.write(f"Quarantined {len(deliveries)} sends; recovered {count} OCR jobs. Review Telegram before starting workers.")
