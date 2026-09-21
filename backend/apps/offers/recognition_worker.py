import hashlib
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.ledger.engine import lock
from apps.shifts.audit import log_change
from .models import RecognitionJob, RecognitionProfile, ShiftOffer, WorkerHeartbeat
from .recognizer import analyze_image, merge_results
from .service import (allowed_organizations, changed, prompt, publish, quality_gate,
                      recompute, snapshot)
from .storage import crop_header, media_path, remove_image


@transaction.atomic
def claim_job():
    lock()
    now = timezone.now()
    RecognitionJob.objects.filter(state="running", started_at__lt=now - timedelta(minutes=3), attempts__lt=3).update(state="pending", available_at=now)
    for exhausted in RecognitionJob.objects.filter(state="running", started_at__lt=now - timedelta(minutes=3), attempts__gte=3):
        exhausted.state, exhausted.error = "failed", "Распознавание прервалось несколько раз. Повторите на сайте."
        exhausted.save()
        if exhausted.offer_id:
            offer = exhausted.offer
            if offer.version == exhausted.revision:
                offer.state, offer.error = "failed", exhausted.error
                changed(offer, "recognition_failed", "system")
                prompt(offer)
    job = RecognitionJob.objects.filter(state="pending", available_at__lte=now).order_by("available_at", "id").first()
    if job:
        job.state, job.started_at, job.attempts = "running", now, job.attempts + 1
        job.save()
        if job.offer_id:
            ShiftOffer.objects.filter(pk=job.offer_id, state="collecting", version=job.revision).update(state="processing")
    return job


def run_once(ocr):
    WorkerHeartbeat.objects.update_or_create(name="recognition", defaults={"detail": "models_ready"})
    job = claim_job()
    if not job:
        return False
    began = time.perf_counter()
    leased_attempt = job.attempts
    try:
        if job.offer_id:
            offer = ShiftOffer.objects.get(pk=job.offer_id)
            images = list(offer.images.filter(purged_at=None))
            profiles = [{"organization": p.organization_id, "rooms": p.rooms} for p in
                RecognitionProfile.objects.filter(active=True, confirmed=True, organization__is_active=True)]
            by_digest = {}
            results = []
            for image in images:
                if image.sha256 not in by_digest:
                    by_digest[image.sha256] = analyze_image(media_path(image.path), ocr, timezone.localdate(offer.created_at))
                results.append(by_digest[image.sha256])
            result = merge_results(results, profiles)
            finish_offer(job, images, results, result, int((time.perf_counter() - began) * 1000))
        else:
            profile = RecognitionProfile.objects.get(pk=job.profile_id)
            original = profile.sample_path
            result = analyze_image(media_path(original), ocr, timezone.localdate())
            # Store only hall/date chrome; never retain the reference screenshot's customer area.
            header = crop_header(original, result["header_bottom"]) if result.get("headers") else ""
            with transaction.atomic():
                lock()
                profile.refresh_from_db()
                lease = RecognitionJob.objects.get(pk=job.pk)
                if lease.attempts != job.attempts or lease.state != "running":
                    transaction.on_commit(lambda: remove_image(header))
                    return True
                if profile.sample_version == job.revision:
                    previous = profile.header_path
                    profile.header_path = header
                    profile.suggestions = result.get("headers", [])
                    profile.sample_path = ""
                    profile.sample_error = "" if header else "Залы не найдены. Заполните список вручную или загрузите другой скриншот."
                    profile.save()
                    transaction.on_commit(lambda: remove_image(previous))
                    transaction.on_commit(lambda: remove_image(original))
                else:
                    transaction.on_commit(lambda: remove_image(header))
                job.state = "done"
                job.elapsed_ms = int((time.perf_counter() - began) * 1000)
                job.save()
    except Exception:
        # OCR exceptions may contain recognized customer text; do not persist exception repr.
        with transaction.atomic():
            lock()
            job.refresh_from_db()
            if job.state != "running" or job.attempts != leased_attempt:
                return True
            job.state, job.error = "failed", "Не удалось обработать изображение. Проверьте OCR-worker и повторите распознавание."
            job.save()
            if job.offer_id:
                offer = ShiftOffer.objects.get(pk=job.offer_id)
                if offer.version == job.revision:
                    offer.state, offer.error = "failed", job.error
                    changed(offer, "recognition_failed", "system")
                    prompt(offer)
            elif job.profile_id:
                RecognitionProfile.objects.filter(pk=job.profile_id, sample_version=job.revision).update(sample_error=job.error)
    return True


@transaction.atomic
def finish_offer(job, images, results, result, elapsed_ms):
    lock()
    current_job = RecognitionJob.objects.get(pk=job.pk)
    offer = ShiftOffer.objects.get(pk=job.offer_id)
    if current_job.attempts != job.attempts or current_job.state != "running":
        return
    if offer.version != job.revision or offer.state not in ("collecting", "processing"):
        current_job.state = "cancelled"
        current_job.save()
        return
    signature = hashlib.sha256("|".join(sorted({image.sha256 for image in images})).encode()).hexdigest()
    before = snapshot(offer)
    offer.source_signature = signature
    # An identical old screenshot may be a real reversion after an approved update.
    # Compare current schedule semantics, never short-circuit by a historical file hash.
    for image, recognized in zip(images, results):
        image.recognized = recognized
        image.save(update_fields=("recognized",))
    offer.date = result["date"]
    offer.organization_id = result["organization"]
    offer.intervals, offer.questions, offer.evidence = result["intervals"], result["questions"], result["evidence"]
    offer.evidence["elapsed_ms"] = elapsed_ms
    if offer.organization_id not in allowed_organizations(offer.sender_id):
        offer.organization_id = None
        if not any(q["key"] == "organization" for q in offer.questions):
            offer.questions.insert(0, {"key": "organization", "label": "Нет права на найденную организацию. Выберите разрешённую.", "type": "organization"})
    recompute(offer)
    offer.state = "needs_input" if offer.questions else "review"
    changed(offer, "recognized", "ocr", before)
    if not offer.questions and quality_gate():
        try:
            publish(offer.pk, user_id=offer.sender_id, version=offer.version)
        except Exception:
            prompt(offer)
    else:
        prompt(offer)
    current_job.state, current_job.elapsed_ms = "done", elapsed_ms
    current_job.save()
