"""Per-organization schedule, with legacy scalar fields kept as a projection."""
from copy import deepcopy
from django.core.exceptions import ValidationError
from apps.shifts.models import Organization
from .models import OfferPart


def values(offer):
    saved = list(offer.parts.select_related("organization").order_by("id")) if offer.pk else []
    if saved:
        return [{"id": p.pk, "organization": p.organization_id, "organization_name": p.organization.name,
                 "image_ids": list(p.images.filter(active=True).values_list("id", flat=True)),
                 "start_time": p.start_time, "end_time": p.end_time, "intervals": deepcopy(p.intervals)} for p in saved]
    if offer.organization_id:
        return [{"organization": offer.organization_id, "organization_name": offer.organization.name,
                 "start_time": offer.start_time, "end_time": offer.end_time, "intervals": deepcopy(offer.intervals)}]
    return []


def replace(offer, data):
    """Caller holds the singleton lock and an atomic transaction."""
    from .service import parse_time, authorize_sender
    if not isinstance(data, list) or not 1 <= len(data) <= 2:
        raise ValidationError("Нужны одна или две студии.")
    old_ids = {p.pk: p.organization_id for p in offer.parts.all()}
    seen, keep, moves = set(), [], []
    for item in data:
        org = Organization.objects.get(pk=int(item["organization"]), is_active=True)
        if org.pk in seen:
            raise ValidationError("Студии должны быть разными.")
        seen.add(org.pk)
        if item.get("id") and int(item["id"]) not in old_ids:
            raise ValidationError("Часть предложения изменилась. Обновите страницу.")
        image_ids = list(offer.images.filter(part_id=item["id"]).values_list("id", flat=True)) if item.get("id") else []
        authorize_sender(offer.sender_id, org.pk)
        intervals = deepcopy(item.get("intervals", []))
        if not isinstance(intervals, list) or len(intervals) > 100:
            raise ValidationError("Проверьте список записей студии.")
        for interval in intervals:
            interval["organization"] = org.pk
            for field in ("start", "end"):
                if interval.get(field):
                    interval[field] = parse_time(interval[field]).strftime("%H:%M")
            if interval.get("start") and interval.get("end") and interval["start"] >= interval["end"]:
                raise ValidationError("Окончание записи должно быть позже начала.")
        start = parse_time(item["start_time"]) if item.get("start_time") else None
        end = parse_time(item["end_time"]) if item.get("end_time") else None
        complete = [i for i in intervals if i.get("start") and i.get("end")]
        if complete:
            start = parse_time(min(i["start"] for i in complete))
            end = parse_time(max(i["end"] for i in complete))
        if start and end and start >= end:
            raise ValidationError("Проверьте интервал студии.")
        part, _ = OfferPart.objects.update_or_create(offer=offer, organization=org,
            defaults={"start_time": start, "end_time": end, "intervals": intervals})
        keep.append(part.pk)
        moves.append((part.pk, image_ids))
    for part_id, image_ids in moves:
        offer.images.filter(pk__in=image_ids).update(part_id=part_id)
    for index, image in enumerate(offer.images.select_related("part").all()):
        if image.part_id and index < len(offer.evidence.get("images", [])):
            offer.evidence["images"][index]["organization"] = image.part.organization_id
    offer.parts.exclude(pk__in=keep).delete()
    project(offer)


def project(offer):
    saved = list(offer.parts.order_by("id"))
    if not saved:
        return
    offer.organization_id = saved[0].organization_id
    offer.start_time = min((p.start_time for p in saved if p.start_time), default=None)
    offer.end_time = max((p.end_time for p in saved if p.end_time), default=None)
    offer.intervals = [{**i, "organization": p.organization_id} for p in saved for i in p.intervals]


def sync(offer):
    """Rebuild child intervals from the clarified legacy-compatible booking list."""
    data = values(offer)
    if not data:
        return
    for p in data:
        p["intervals"] = [i for i in offer.intervals if i.get("organization", offer.organization_id) == p["organization"]]
    unknown = [i for i in offer.intervals if not i.get("organization", offer.organization_id)]
    replace(offer, data)
    offer.intervals.extend(unknown)


def assign_images(offer):
    mapping = {p.organization_id: p.pk for p in offer.parts.all()}
    evidence = offer.evidence.get("images", [])
    for index, image in enumerate(offer.images.all()):
        if index < len(evidence):
            image.part_id = mapping.get(evidence[index].get("organization"))
            image.save(update_fields=("part",))


def from_intervals(offer):
    organizations = {i.get("organization", offer.organization_id) for i in offer.intervals}
    organizations.update(i.get("organization") for i in offer.evidence.get("images", []))
    organizations.discard(None)
    if len(organizations) > 2:
        if not any(q["key"] == "split" for q in offer.questions):
            offer.questions.insert(0, {"key": "split", "label": "В альбоме больше двух студий. Разделите его на сайте.", "type": "split"})
        return
    if organizations:
        original = offer.intervals
        current = {p["organization"]: p for p in values(offer)}
        replace(offer, [{**current.get(org, {}), "organization": org,
                         "intervals": [i for i in original if i.get("organization", offer.organization_id) == org]}
                        for org in sorted(organizations)])
        offer.intervals = original
        assign_images(offer)
