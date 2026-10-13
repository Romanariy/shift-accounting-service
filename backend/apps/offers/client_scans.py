"""Scan existing offer photographs without changing offers or Telegram publications."""
from copy import deepcopy

from django.db import transaction
from django.utils import timezone

from apps.ledger.engine import digest, lock
from apps.ledger.models import BonusClient, BonusRule
from apps.ledger.booking_imports import sanitize_phones, stage_bookings
from apps.ledger.client_bonuses import apply_days
from .models import OfferImage, RecognitionJob, ShiftOffer
from .recognizer import analyze_image
from .storage import media_path


def config_token():
    if not BonusClient.objects.filter(active=True).exists() or not BonusRule.objects.exclude(applied={}).exists():
        return ""
    return digest({"engine": "automatic-client-bonus-1",
        "clients": list(BonusClient.objects.order_by("id").values("id", "phones", "active", "revision")),
        "rules": list(BonusRule.objects.order_by("id").values("id", "applied"))})


def image_org(image, offer, index):
    if any(q.get("key") in ("organization", f"image:{index}:organization") for q in offer.questions):
        return None
    if image.part_id:
        return image.part.organization_id
    evidence = offer.evidence.get("images", [])
    if index < len(evidence) and evidence[index].get("organization"):
        return evidence[index]["organization"]
    if offer.parts.count() <= 1:
        return offer.organization_id
    return None


def offer_token(offer, token):
    return digest({"config": token, "date": offer.date, "organization": offer.organization_id,
        "parts": list(offer.parts.order_by("id").values("organization_id")),
        "clarifications": [{k: i.get(k) for k in ("organization", "room", "start", "end", "human_verified")}
                           for i in offer.intervals],
        "images": list(offer.images.order_by("id").values("id", "part_id"))})


@transaction.atomic
def enqueue_existing():
    """Idempotent backfill also catches newly registered clients and new rules."""
    lock()
    token = config_token()
    if not token:
        return 0
    queued = 0
    offers = ShiftOffer.objects.filter(kind="shift", date__isnull=False,
        images__purged_at=None).exclude(state__in=("collecting", "processing")).distinct().order_by("id")
    for offer in offers.iterator():
        if any(q.get("key") in ("date", "split") for q in offer.questions):
            continue
        marker = offer_token(offer, token)
        images = list(offer.images.select_related("part").order_by("id"))
        eligible = [i for n, i in enumerate(images) if i.purged_at is None and image_org(i, offer, n)]
        if not eligible or all(i.client_scan_token == marker for i in eligible):
            continue
        if RecognitionJob.objects.filter(offer=offer, clients_only=True, client_scan_token=marker,
                revision=offer.version).exclude(state="cancelled").exists():
            continue
        RecognitionJob.objects.create(offer=offer, revision=offer.version, clients_only=True,
            client_scan_token=marker, available_at=timezone.now())
        queued += 1
        if queued >= 50:
            break
    return queued


def scan(job, ocr):
    offer = ShiftOffer.objects.get(pk=job.offer_id)
    token = config_token()
    marker = offer_token(offer, token)
    images = list(offer.images.select_related("part").order_by("id"))
    results, cached = [], {}
    for index, image in enumerate(images):
        org = image_org(image, offer, index)
        if image.purged_at or not org or image.client_scan_token == marker:
            continue
        path = media_path(image.path)
        if not path.is_file():
            # The source was removed outside the retention worker. Never fabricate evidence.
            results.append((image, org, None))
            continue
        if image.sha256 not in cached:
            cached[image.sha256] = analyze_image(path, ocr, offer.date, read_clients=True)
        result = deepcopy(cached[image.sha256])
        sanitize_phones([result])
        # Reuse only the exact card's interval clarification, never the whole shift span.
        for item in result.get("intervals", []):
            candidates = [i for i in offer.intervals if i.get("organization", offer.organization_id) == org
                and i.get("human_verified") and i.get("room") == item.get("room")
                and i.get("start") and i.get("start") == item.get("start")]
            if len(candidates) == 1:
                item["end"] = candidates[0].get("end")
                item["issues"] = []
        results.append((image, org, result))
    finish_scan(job, marker, results)


@transaction.atomic
def finish_scan(job, marker, results):
    lock()
    lease = RecognitionJob.objects.get(pk=job.pk)
    offer = ShiftOffer.objects.get(pk=job.offer_id)
    if lease.state != "running" or lease.attempts != job.attempts:
        return
    if offer.version != job.revision or marker != job.client_scan_token or marker != offer_token(offer, config_token()):
        lease.state = "cancelled"; lease.save(update_fields=("state",))
        return
    keys, missing = set(), 0
    for image, org, result in results:
        if result is None:
            missing += 1
        else:
            # The offer's confirmed date and studio are authoritative. OCR only reads cards.
            source = {"type": "offer", "id": offer.pk, "image_id": image.pk,
                      "image": list(offer.images.order_by("id").values_list("id", flat=True)).index(image.pk)}
            stage_bookings(result.get("intervals", []), org, offer.date, source, automatic=True)
            current = OfferImage.objects.get(pk=image.pk)
            current.recognized = {**current.recognized, "client_intervals": result.get("intervals", [])}
            current.save(update_fields=("recognized",))
            keys.add((org, offer.date))
        OfferImage.objects.filter(pk=image.pk, purged_at=None).update(client_scan_token=marker)
    if keys:
        apply_days(keys, skip_frozen=True, actor="ocr:offer-bonus-backfill")
    lease.state = "done"
    lease.error = f"Недоступно исходных фото: {missing}" if missing else ""
    lease.save(update_fields=("state", "error"))
