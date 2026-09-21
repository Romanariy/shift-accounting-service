import json
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, ValidationError, RequestDataTooBig
from django.db import transaction
from django.db.models import F, Case, When, Value, CharField
from django.http import FileResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.ledger.engine import lock
from apps.ledger.models import Settings, TelegramContact, Service
from apps.shifts.audit import log_change
from apps.shifts.models import AuditLog, Organization, Employee
from . import service
from .models import OfferConfig, OfferDelivery, OfferImage, RecognitionJob, RecognitionProfile, ShiftOffer, WorkerHeartbeat
from .storage import media_path, remove_image, store_image


def endpoint(function):
    @csrf_exempt
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        try:
            return function(request, *args, **kwargs)
        except ObjectDoesNotExist:
            return JsonResponse({"error": "Объект не найден."}, status=404)
        except RequestDataTooBig:
            return JsonResponse({"error": "Изображение слишком большое (максимум 10 МБ)."}, status=413)
        except (ValidationError, ValueError, KeyError, TypeError, IndexError) as error:
            return JsonResponse({"error": " ".join(error.messages) if isinstance(error, ValidationError)
                else "Проверьте формат заполненных полей."}, status=400)
    return wrapped


def body(request):
    value = json.loads(request.body or b"{}")
    if not isinstance(value, dict):
        raise ValidationError("Ожидается объект полей.")
    return value


def method(request, *allowed):
    if request.method not in allowed:
        raise ValidationError("Метод не поддерживается.")


def serialize_offer(offer, detail=False):
    data = {**service.snapshot(offer), "id": offer.pk, "organization_name": offer.organization.name if offer.organization_id else "",
        "state_label": service.STATE_LABELS.get(offer.state, offer.state), "sender_name": offer.sender_name,
        "created_at": offer.created_at, "closed_at": offer.closed_at, "error": offer.error,
        "duplicate_of": offer.duplicate_of_id, "evidence": offer.evidence}
    if detail:
        from .updates import preview
        data["update_preview"] = preview(offer)
        data.update(images=[{"id": i.pk, "url": f"/api/shifts/ledger/offer-images/{i.pk}/" if not i.purged_at else None,
            "purged_at": i.purged_at, "recognized": i.recognized, "active": i.active} for i in offer.images.all()],
            jobs=list(offer.jobs.order_by("-id").values("id", "state", "attempts", "elapsed_ms", "error")[:20]),
            deliveries=list(offer.deliveries.order_by("id").values("id", "purpose", "recipient", "thread_id", "state", "message_ids", "error", "delete_requested", "deleted_at",
                edit_state=F("offermessageedit__state"), edit_error=F("offermessageedit__error"))),
            history=list(AuditLog.objects.filter(entity_type="shift_offer", entity_id=offer.pk).order_by("-created_at", "-id")
                .values("id", "created_at", "action", "actor", "diff")[:150]))
    return data


@endpoint
def offers(request, pk=None, action=None):
    if pk is None:
        method(request, "GET", "POST")
        if request.method == "POST":
            from .manual import create
            data = body(request)
            key = str(data.get("request_key", ""))
            if not key or len(key) > 100:
                raise ValidationError("Нужен ключ запроса создания.")
            return JsonResponse(serialize_offer(create(data, source_key="web:" + key), True), status=201)
        query = ShiftOffer.objects.select_related("organization")
        if request.GET.get("section") == "cancelled" or request.GET.get("state") == "cancelled":
            query = query.filter(state="cancelled")
        else:
            query = query.exclude(state="cancelled")
        for field in ("state", "organization", "date", "kind", "employee"):
            if request.GET.get(field):
                query = query.filter(**{field: request.GET[field]})
        if request.GET.get("service") == "free":
            query = query.filter(kind="service", service__isnull=True)
        elif request.GET.get("service"):
            query = query.filter(service_id=int(request.GET["service"]))
        for param, lookup in (("date_from", "date__gte"), ("date_to", "date__lte")):
            if request.GET.get(param):
                query = query.filter(**{lookup: request.GET[param]})
        if request.GET.get("search"):
            query = query.filter(service_name__icontains=request.GET["search"][:160])
        ordering = request.GET.get("ordering", "-created_at")
        if ordering not in ("date", "-date", "created_at", "-created_at", "service_name", "-service_name"):
            raise ValidationError("Неизвестная сортировка.")
        query = query.annotate(sort_name=Case(When(kind="shift", then=Value("Смена")), default=F("service_name"), output_field=CharField()))
        query = query.order_by(ordering.replace("service_name", "sort_name"), "-id")
        offset = max(0, int(request.GET.get("offset", 0)))
        return JsonResponse({"items": [serialize_offer(o) for o in query[offset:offset + 50]], "count": query.count()})
    if request.method == "GET" and not action:
        return JsonResponse(serialize_offer(ShiftOffer.objects.select_related("organization").get(pk=pk), True))
    method(request, "POST")
    if action == "image":
        from django.conf import settings
        from .manual import attach
        upload = request.FILES.get("image")
        if not upload or upload.size > settings.OFFER_UPLOAD_BYTES:
            raise ValidationError("Загрузите изображение размером до 10 МБ.")
        path, digest = store_image(upload.read())
        try:
            result = attach(pk, path, digest, int(request.POST["message_id"]), version=int(request.POST["version"]))
        except Exception:
            remove_image(path)
            raise
        return JsonResponse(serialize_offer(result, True))
    data = body(request)
    version = int(data["version"])
    if action in ("update-choose", "update-apply", "update-escalate"):
        from . import updates
        if action == "update-choose":
            result = updates.choose(pk, data["target"], data.get("mode", ""), version=version)
        elif action == "update-apply":
            result = updates.apply(pk, version=version)
        else:
            result = updates.escalate(pk, version=version)
    elif action == "publish":
        result = service.publish(pk, version=version)
    elif action == "cancel":
        result = service.cancel(pk, version=version)
    elif action == "release":
        result = service.release(pk, version=version)
    elif action == "assign":
        employee = Employee.objects.get(pk=int(data["employee"]), is_active=True)
        if not employee.telegram_user_id:
            raise ValidationError("У сотрудника не указан Telegram ID.")
        result = service.claim(pk, employee.telegram_user_id, version, actor="web")
    elif action == "answer":
        result = service.answer(pk, data["answers"], version=version)
    elif action == "edit":
        result = service.edit_published(pk, data["fields"], version=version)
    elif action == "retry":
        service.require_current(ShiftOffer.objects.get(pk=pk), version)
        result = service.retry_recognition(pk)
    elif action == "split":
        return JsonResponse({"children": service.split_offer(pk, data["groups"], version)})
    elif action == "delivery":
        from .delivery import resolve_delivery
        resolve_delivery(pk, int(data["delivery"]), data["resolution"], data.get("message_ids", []), version)
        result = ShiftOffer.objects.get(pk=pk)
    else:
        raise ValidationError("Неизвестное действие.")
    return JsonResponse(serialize_offer(result, True))


def serialize_profile(profile):
    return {"id": profile.pk, "organization": profile.organization_id, "rooms": profile.rooms,
        "publishers": list(profile.publishers.values_list("id", flat=True)), "active": profile.active,
        "confirmed": profile.confirmed, "suggestions": profile.suggestions, "error": profile.sample_error,
        "processing": bool(profile.sample_path) and not profile.sample_error,
        "header_url": f"/api/shifts/ledger/offer-profiles/{profile.organization_id}/header/?v={profile.sample_version}" if profile.header_path else None}


@endpoint
def profiles(request, organization=None, action=None):
    if organization is None:
        method(request, "GET")
        return JsonResponse({"items": [serialize_profile(p) for p in RecognitionProfile.objects.prefetch_related("publishers")]})
    org = Organization.objects.get(pk=organization)
    if action == "header":
        method(request, "GET")
        profile = RecognitionProfile.objects.get(organization=org)
        return private_file(profile.header_path)
    method(request, "POST", "PUT")
    with transaction.atomic():
        lock()
        profile, _ = RecognitionProfile.objects.get_or_create(organization=org)
        before = serialize_profile(profile)
        if action == "sample":
            from django.conf import settings
            upload = request.FILES.get("image")
            if not upload or upload.size > settings.OFFER_UPLOAD_BYTES:
                raise ValidationError("Загрузите PNG, JPEG или WebP размером до 10 МБ.")
            relative, _ = store_image(upload.read())
            previous = profile.sample_path
            profile.sample_path, profile.sample_error = relative, ""
            profile.sample_version += 1
            profile.save()
            RecognitionJob.objects.filter(profile=profile, state="pending").update(state="cancelled")
            RecognitionJob.objects.create(profile=profile, revision=profile.sample_version, available_at=timezone.now())
            transaction.on_commit(lambda: remove_image(previous))
        else:
            data = body(request)
            rooms = data.get("rooms", [])
            if not isinstance(rooms, list) or len(rooms) > 100:
                raise ValidationError("Укажите не более 100 залов.")
            cleaned = []
            for room in rooms:
                name = str(room["name"]).strip()
                aliases = room.get("aliases", [])
                if not name or len(name) > 120 or not isinstance(aliases, list) or len(aliases) > 20 or any(not isinstance(a, str) or not 1 <= len(a.strip()) <= 120 for a in aliases):
                    raise ValidationError("Проверьте названия залов и сокращения (до 120 символов).")
                cleaned.append({"name": name, "aliases": [a.strip() for a in aliases]})
            publishers = [int(p) for p in data.get("publishers", [])]
            if TelegramContact.objects.filter(pk__in=publishers).count() != len(set(publishers)):
                raise ValidationError("Контакт должен сначала написать боту /start.")
            profile.rooms, profile.active = cleaned, bool(data.get("active", True))
            profile.confirmed = bool(data.get("confirmed", False)) and bool(cleaned)
            profile.save()
            profile.publishers.set(publishers)
        log_change("recognition_profile", profile.pk, "sample_uploaded" if action == "sample" else "updated",
            "web", before=before, after=serialize_profile(profile))
    return JsonResponse(serialize_profile(profile))


@endpoint
def config(request):
    from .routing import config_data, update_config, validate_routes
    method(request, "GET", "POST")
    value, _ = OfferConfig.objects.get_or_create(pk=1)
    if request.method == "POST":
        data = body(request)
        previous = config_data(value)
        update_config(value, data)
        validate_routes(Settings.objects.get(pk=1), value)
        value.save()
        log_change("offer_config", 1, "updated", "web", before=previous, after=config_data(value))
    return JsonResponse({**config_data(value),
        "coordinator": service.coordinator_id(), "auto_publish": service.quality_gate(),
        "workers": list(WorkerHeartbeat.objects.values("name", "updated_at", "detail")),
        "organizations": list(Organization.objects.filter(is_active=True).values("id", "name")),
        "services": list(Service.objects.values("id", "name", "input_type", "active", "deleted_at", "default_organization")),
        "employees": [{"id": e.pk, "name": e.display_name} for e in Employee.objects.filter(is_active=True, telegram_user_id__isnull=False)],
        "contacts": list(TelegramContact.objects.values("id", "name", "user_id"))})


def private_file(path):
    if not path or not media_path(path).is_file():
        raise ObjectDoesNotExist()
    response = FileResponse(media_path(path).open("rb"), content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@endpoint
def image(request, pk):
    method(request, "GET")
    value = OfferImage.objects.get(pk=pk, purged_at=None)
    return private_file(value.path)
