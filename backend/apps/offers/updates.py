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
    aliases = {}
    for profile in RecognitionProfile.objects.filter(organization_id__in=organization_ids(offer)):
        aliases[profile.organization_id] = {normalize(alias): normalize(room["name"])
            for room in profile.rooms for alias in [room["name"], *room.get("aliases", [])]}
    return sorted({(i.get("organization", offer.organization_id),
                    aliases.get(i.get("organization", offer.organization_id), {}).get(normalize(i.get("room", "")), normalize(i.get("room", ""))),
                    str(i.get("start") or "")[:5], str(i.get("end") or "")[:5])
                   for i in (schedule_intervals(offer) if intervals is None else intervals)})


def organization_ids(offer):
    from .parts import values
    return {p["organization"] for p in values(offer)}


def schedule_intervals(offer):
    from .parts import values
    return [{**i, "organization": p["organization"]} for p in values(offer) for i in p["intervals"]]


def candidates(offer):
    query = ShiftOffer.objects.filter(kind="shift", date=offer.date,
        state__in=TARGET_STATES, update_target=None,
        start_time__isnull=False, end_time__isnull=False).filter(
            Q(pk__lt=offer.pk) | Q(state__in=("open", "claimed", "publishing"))).exclude(pk=offer.pk)
    for org in organization_ids(offer):
        query = query.filter(Q(organization_id=org) | Q(parts__organization_id=org))
    return query.distinct().order_by("id")


def same(left, right):
    from .parts import values
    ids = organization_ids(left)
    return canonical(left) == canonical(right, [i for i in schedule_intervals(right) if i["organization"] in ids]) and {
        p["organization"]: (p["start_time"], p["end_time"]) for p in values(left)} == {
        p["organization"]: (p["start_time"], p["end_time"]) for p in values(right) if p["organization"] in ids}


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
    ids = organization_ids(draft)
    items = deepcopy([i for i in schedule_intervals(target) if mode == "merge" or i["organization"] not in ids]) + deepcopy(schedule_intervals(draft))
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
    if target.state not in TARGET_STATES or not organization_ids(draft) <= organization_ids(target) or target.date != draft.date:
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
        target.images.filter(Q(part__organization_id__in=organization_ids(draft)) |
                             Q(part__isnull=True, offer__organization_id__in=organization_ids(draft))).update(active=False)
    # Normalize legacy single-studio targets before assigning copied images.
    if not target.parts.exists():
        from .parts import from_intervals
        from_intervals(target)
        target.images.filter(part=None).update(part=target.parts.first())
    mapping = {p.organization_id: p for p in target.parts.all()}
    existing = {(org or target.organization_id, digest) for org, digest in
                target.images.filter(active=True).values_list("part__organization_id", "sha256")}
    next_message = min([0, *target.images.values_list("message_id", flat=True)]) - 1
    for image in draft.images.filter(active=True, purged_at=None):
        org = image.part.organization_id if image.part_id else draft.organization_id
        if (org, image.sha256) in existing:
            continue
        path = f"images/{uuid4().hex}.png"
        shutil.copyfile(media_path(image.path), media_path(path))
        OfferImage.objects.create(offer=target, part=mapping[org], path=path, sha256=image.sha256, message_id=next_message,
            telegram_file_id=image.telegram_file_id, recognized=image.recognized)
        existing.add((org, image.sha256))
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
    from .presentation import describe, booking_lines
    from .parts import values
    old_parts = {p["organization"]: p for p in before["parts"]}
    changes = "\n".join(f'{p["organization_name"]}: было {old_parts[p["organization"]]["start_time"]:%H:%M}–{old_parts[p["organization"]]["end_time"]:%H:%M}, '
                        f'стало {p["start_time"]:%H:%M}–{p["end_time"]:%H:%M}' for p in values(target)
                        if p["organization"] in organization_ids(draft))
    text = ("Расписание обновлено\n" + describe(target) +
        f'\nБыло: {before["start_time"]:%H:%M}–{before["end_time"]:%H:%M}\nСтало: {target.start_time:%H:%M}–{target.end_time:%H:%M}' +
        "\n" + changes + "\nПрежние записи:\n" + booking_lines(target, before["intervals"], before["parts"])[:1100] +
        "\nНовые записи:\n" + booking_lines(target, target.intervals)[:1100])
    service.enqueue(target, "update_notice", config.chat_id, thread_id=config.released_thread_id, payload={"text": text})
    if target.assignee_user_id:
        service.enqueue(target, "update_notice", target.assignee_user_id, payload={"text": text})
    draft.state, draft.closed_at = "applied", timezone.now()
    service.changed(draft, "update_applied", actor, {"target": target.pk})
    service.enqueue(draft, "notice", draft.sender_id)
    return draft
