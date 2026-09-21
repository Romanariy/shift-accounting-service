"""Confirmed semantic updates. The incoming offer is the immutable source/audit draft."""
from copy import deepcopy
import re
import shutil
import unicodedata
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import service
from .models import ShiftOffer, RecognitionProfile, OfferImage
from .storage import media_path

TARGET_STATES = ("review", "open", "claimed", "publishing")


def normalize(value):
    return re.sub(r"[^а-яa-z0-9]", "", unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е"))


def canonical(offer, intervals=None):
    profile = RecognitionProfile.objects.filter(organization_id=offer.organization_id).first()
    aliases = {}
    for room in profile.rooms if profile else []:
        for alias in [room["name"], *room.get("aliases", [])]:
            aliases[normalize(alias)] = normalize(room["name"])
    return sorted({(aliases.get(normalize(i.get("room", "")), normalize(i.get("room", ""))),
                    str(i.get("start") or "")[:5], str(i.get("end") or "")[:5])
                   for i in (offer.intervals if intervals is None else intervals)})


def candidates(offer):
    return ShiftOffer.objects.filter(kind="shift", organization_id=offer.organization_id, date=offer.date,
        state__in=TARGET_STATES, update_target=None,
        start_time__isnull=False, end_time__isnull=False).filter(
            Q(pk__lt=offer.pk) | Q(state__in=("open", "claimed", "publishing"))).exclude(pk=offer.pk).order_by("id")


def same(left, right):
    return canonical(left) == canonical(right) and left.start_time == right.start_time and left.end_time == right.end_time


def prepare(offer):
    if offer.kind != "shift" or offer.state != "review" or offer.questions or not offer.images.exists():
        return offer.state in ("update_review", "duplicate")
    matches = list(candidates(offer)) if offer.organization_id and offer.date else []
    if not matches:
        return False
    before = service.snapshot(offer)
    if len(matches) == 1 and same(offer, matches[0]):
        offer.state, offer.duplicate_of, offer.closed_at = "duplicate", matches[0], timezone.now()
        service.changed(offer, "unchanged", "system", before)
    else:
        offer.state = "update_review"
        if len(matches) == 1:
            offer.update_target, offer.update_target_version = matches[0], matches[0].version
        service.changed(offer, "update_detected", "system", before)
    return True


def merged_intervals(draft, target, mode):
    items = deepcopy(target.intervals if mode == "merge" else []) + deepcopy(draft.intervals)
    seen, result = set(), []
    for item in items:
        key = tuple(canonical(draft, [item])[0])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return sorted(result, key=lambda i: (i.get("start") or "", i.get("room") or ""))


def preview(draft):
    if draft.state != "update_review":
        return None
    options = list(candidates(draft))
    target = next((o for o in options if o.pk == draft.update_target_id), None)
    return {"candidates": [{**service.snapshot(o), "id": o.pk} for o in options],
        "stale": bool(draft.update_target_id and (not target or target.version != draft.update_target_version)),
        "before": service.snapshot(target) if target else None,
        "intervals": merged_intervals(draft, target, draft.update_mode) if target and draft.update_mode else None}


def get_draft(pk, user_id, version):
    draft = ShiftOffer.objects.get(pk=pk)
    service.authorize_edit(draft, user_id)
    service.require_current(draft, version)
    if draft.state != "update_review" or draft.questions:
        raise ValidationError("Обновление уже обработано или требует уточнения.")
    return draft


@transaction.atomic
def choose(pk, target_id, mode="", *, user_id=None, version=None):
    service.lock()
    draft = get_draft(pk, user_id, version)
    target = candidates(draft).get(pk=int(target_id))
    if mode not in ("", "merge", "replace"):
        raise ValidationError("Выберите дополнение или замену.")
    before = service.snapshot(draft)
    draft.update_target, draft.update_target_version, draft.update_mode = target, target.version, mode
    service.changed(draft, "update_prepared", f"telegram:{user_id}" if user_id else "web", before)
    service.enqueue(draft, "update_review", user_id or draft.sender_id)
    return draft


@transaction.atomic
def escalate(pk, *, user_id=None, version=None):
    service.lock()
    draft = get_draft(pk, user_id, version)
    if not draft.update_target_id or not draft.update_mode:
        raise ValidationError("Сначала выберите предложение и способ обновления.")
    service.enqueue(draft, "update_review", service.coordinator_id(), suffix="approval")
    return draft


@transaction.atomic
def apply(pk, *, user_id=None, version=None):
    service.lock()
    draft = get_draft(pk, user_id, version)
    if not draft.update_target_id or draft.update_mode not in ("merge", "replace"):
        raise ValidationError("Сначала выберите предложение и способ обновления.")
    target = ShiftOffer.objects.get(pk=draft.update_target_id)
    service.authorize_edit(target, user_id)
    service.require_current(target, draft.update_target_version)
    if target.state not in TARGET_STATES or target.organization_id != draft.organization_id or target.date != draft.date:
        raise ValidationError("Исходная смена закрыта или изменена. Обновите сравнение.")
    before = service.snapshot(target)
    target.intervals = merged_intervals(draft, target, draft.update_mode)
    if not target.intervals or any(not i.get("start") or not i.get("end") or i["start"] >= i["end"] for i in target.intervals):
        raise ValidationError("Уточните начало и окончание каждой записи.")
    service.recompute(target)
    if service.end_at(target) <= timezone.now():
        raise ValidationError("Нельзя применить расписание на прошедшее время.")
    if target.state in ("review", "publishing"):
        service.validate_publish(target)
    # Each row owns its own file; purging the source draft cannot remove current photos.
    if draft.update_mode == "replace":
        target.images.update(active=False)
    existing = set(target.images.filter(active=True).values_list("sha256", flat=True))
    next_message = min([0, *target.images.values_list("message_id", flat=True)]) - 1
    for image in draft.images.filter(active=True, purged_at=None):
        if image.sha256 in existing:
            continue
        path = f"images/{uuid4().hex}.png"
        shutil.copyfile(media_path(image.path), media_path(path))
        OfferImage.objects.create(offer=target, path=path, sha256=image.sha256, message_id=next_message,
            telegram_file_id=image.telegram_file_id, recognized=image.recognized)
        existing.add(image.sha256)
        next_message -= 1
    target.delivery_generation += 1
    actor = f"telegram:{user_id}" if user_id else "web"
    service.changed(target, "schedule_updated", actor, before)
    config = service.publication_config(require_enabled=False)
    if target.state in ("open", "publishing"):
        service.enqueue_active(target)
    elif target.state == "claimed":
        service.enqueue(target, "claimed_photos", config.chat_id, thread_id=config.claimed_thread_id)
        service.enqueue(target, "claimed_topic", config.chat_id, thread_id=config.claimed_thread_id)
        for recipient in {target.assignee_user_id, service.coordinator_id()} - {None}:
            service.enqueue(target, "assignment_photos", recipient)
            service.enqueue(target, "assignment", recipient)
    else:
        service.enqueue(target, "review", target.sender_id)
    from .presentation import describe
    def lines(items):
        return "\n".join(f'{i.get("room", "")} {i.get("start")}–{i.get("end")}' for i in items)
    text = ("Расписание обновлено\n" + describe(target) +
        f'\nБыло: {before["start_time"]:%H:%M}–{before["end_time"]:%H:%M}\nСтало: {target.start_time:%H:%M}–{target.end_time:%H:%M}' +
        "\nПрежние записи:\n" + lines(before["intervals"])[:1100] + "\nНовые записи:\n" + lines(target.intervals)[:1100])
    service.enqueue(target, "update_notice", config.chat_id, thread_id=config.released_thread_id, payload={"text": text})
    if target.assignee_user_id:
        service.enqueue(target, "update_notice", target.assignee_user_id, payload={"text": text})
    draft.state, draft.closed_at = "applied", timezone.now()
    service.changed(draft, "update_applied", actor, {"target": target.pk})
    service.enqueue(draft, "notice", draft.sender_id)
    return draft
