"""Private screenshot staging; unconfirmed OCR never creates a financial charge."""
import json
from datetime import date, time

from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction
from django.http import FileResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.shifts.audit import log_change
from apps.shifts.models import Organization
from apps.offers.models import RecognitionJob
from apps.offers.storage import store_image, media_path, remove_image
from .engine import lock
from .models import BonusClient, ClientBooking, ClientBookingImport


def sanitize_phones(results):
    known = {phone: client.pk for client in BonusClient.objects.filter(active=True) for phone in client.phones}
    for result in results:
        for interval in result.get("intervals", []):
            if "phone_candidates" not in interval:
                continue
            candidates = interval.pop("phone_candidates", [])
            matched = [(known[c["number"]], c.get("score", 0)) for c in candidates if c.get("number") in known]
            interval["clients"] = sorted({c for c, _ in matched})
            interval["phone_uncertain"] = len(interval["clients"]) > 1 or any(score < .95 for _, score in matched)
            interval["unknown_phone"] = any(c.get("number") not in known for c in candidates)
    return results


def stage_bookings(intervals, organization, day, source):
    staged = []
    if not organization or not day:
        return staged
    from .engine import digest
    for interval in intervals:
        for client in interval.get("clients", []):
            if not BonusClient.objects.filter(pk=client, active=True).exists():
                continue
            start = time.fromisoformat(interval["start"]) if interval.get("start") else None
            end = time.fromisoformat(interval["end"]) if interval.get("end") else None
            if start and end and end <= start:
                end = None
            lookup = dict(client_id=client, organization_id=organization, date=day, start_time=start, end_time=end)
            obj = ClientBooking.objects.filter(**lookup).first()
            if obj is None:
                obj = ClientBooking.objects.create(**lookup)
            evidence = {**source, "card": digest({"source": source, "box": interval.get("box"),
                "room": interval.get("room"), "start": interval.get("start"), "end": interval.get("end")}),
                "ambiguous_client": len(interval.get("clients", [])) > 1,
                "uncertain": bool(source.get("date_conflict") or interval.get("phone_uncertain") or interval.get("phone_incomplete") or interval.get("issues"))}
            rooms = sorted(set(obj.rooms) | {interval.get("room", "")})
            changed = False
            if evidence not in obj.sources:
                obj.sources.append(evidence); changed = True
            if rooms != obj.rooms:
                obj.rooms = rooms; changed = True
            if changed:
                obj.revision += 1
                obj.save()
                log_change("client_booking", obj.pk, "ocr_source", actor="ocr", after={"source": evidence, "rooms": rooms})
            staged.append(obj.pk)
    return sorted(set(staged))


def sync_offer(offer):
    # Even a schedule duplicate can contain new client evidence. Staging never changes earnings.
    if not offer.date or any(q.get("key") in ("split", "date") for q in offer.questions):
        return
    from apps.offers.service import allowed_organizations
    allowed = allowed_organizations(offer.sender_id)
    images = list(offer.images.order_by("id"))
    for interval in offer.intervals:
        org = interval.get("organization", offer.organization_id)
        if org in allowed:
            index = interval.get("image_index", 0)
            stage_bookings([interval], org, offer.date, {"type": "offer", "id": offer.pk, "image": index,
                "image_id": images[index].pk if type(index) is int and 0 <= index < len(images) else None})


def import_data(obj):
    return {"id": obj.pk, "organization": obj.organization_id, "date": str(obj.date), "state": obj.state,
        "revision": obj.revision, "results": obj.results, "error": obj.error,
        "image_count": len(obj.images), "created_at": obj.created_at.isoformat()}


@transaction.atomic
def finish_import(job, results, elapsed_ms=0):
    lock()
    lease = RecognitionJob.objects.get(pk=job.pk)
    obj = ClientBookingImport.objects.get(pk=job.booking_import_id)
    if lease.state != "running" or lease.attempts != job.attempts or obj.revision != job.revision:
        return
    sanitize_phones(results)
    clean = []
    for index, result in enumerate(results):
        date_conflict = bool(result.get("date") and result["date"] != str(obj.date))
        bookings = stage_bookings(result.get("intervals", []), obj.organization_id, obj.date,
                                 {"type": "import", "id": obj.pk, "image": index, "date_conflict": date_conflict})
        clean.append({"bookings": bookings, "found_date": result.get("date"), "headers": result.get("headers", []),
                      "issues": result.get("issues", []), "date_conflict": date_conflict,
                      "phone_questions": sum(bool(i.get("phone_uncertain") or i.get("phone_incomplete")) for i in result.get("intervals", [])),
                      "unknown_phones": sum(bool(i.get("unknown_phone")) for i in result.get("intervals", []))})
    obj.results, obj.state, obj.error = clean, "done", ""
    obj.save()
    lease.state, lease.elapsed_ms = "done", elapsed_ms; lease.save()
    log_change("client_booking_import", obj.pk, "recognized", actor="ocr", after={"results": clean})


@csrf_exempt
def imports(request, pk=None, action=None):
    from .views import reply
    paths = []
    try:
        if request.method == "GET":
            if pk:
                obj = ClientBookingImport.objects.get(pk=pk)
                if action == "image":
                    index = int(request.GET.get("index", 0))
                    if index < 0 or index >= len(obj.images):
                        raise ValidationError("Изображение не найдено.")
                    response = FileResponse(media_path(obj.images[index]["path"]).open("rb"), content_type="image/png")
                    response["Cache-Control"] = "private, no-store"
                    return response
                return reply(import_data(obj))
            return reply([import_data(i) for i in ClientBookingImport.objects.order_by("-id")[:30]])
        if request.method != "POST":
            return reply({"error": "Метод не поддерживается."}, 405)
        with transaction.atomic():
            lock()
            if action == "retry" and pk:
                obj = ClientBookingImport.objects.get(pk=pk)
                from .client_bonuses import require_revision
                payload = json.loads(request.body or b"{}")
                if not isinstance(payload, dict):
                    raise ValidationError("Тело запроса должно быть JSON-объектом.")
                require_revision(obj, payload)
                if obj.state != "failed" or not obj.images:
                    raise ValidationError("Повтор доступен после ошибки, пока сохранены изображения.")
                obj.revision += 1; obj.state, obj.error = "pending", ""; obj.save()
            elif not pk:
                files = request.FILES.getlist("images")
                if not files or len(files) > 10 or sum(f.size for f in files) > 10*1024*1024:
                    raise ValidationError("Выберите до 10 изображений общим размером не больше 10 МБ.")
                org = Organization.objects.get(pk=int(request.POST["organization"]), is_active=True)
                day = date.fromisoformat(request.POST["date"])
                images = []
                for file in files:
                    path, sha = store_image(file.read())
                    paths.append(path)
                    if sha not in {i["sha256"] for i in images}:
                        images.append({"path": path, "sha256": sha})
                    else:
                        remove_image(path); paths.remove(path)
                obj = ClientBookingImport.objects.create(organization=org, date=day, images=images)
            else:
                return reply({"error": "Метод не поддерживается."}, 405)
            RecognitionJob.objects.create(booking_import=obj, revision=obj.revision, available_at=timezone.now())
            log_change("client_booking_import", obj.pk, "retry" if pk else "create", actor="web",
                       after={"organization": obj.organization_id, "date": obj.date, "images": len(obj.images)})
            return reply(import_data(obj))
    except (ValidationError, ObjectDoesNotExist, ValueError, TypeError, KeyError, OSError) as error:
        for path in paths:
            remove_image(path)
        return reply({"error": " ".join(error.messages) if isinstance(error, ValidationError) else "Не удалось принять изображение или найти импорт."}, 400)
