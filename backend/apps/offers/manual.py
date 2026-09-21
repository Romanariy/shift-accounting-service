"""Manual opportunities share delivery and assignment, never ledger accruals."""
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ledger.models import Service
from apps.shifts.models import Organization
from . import service
from .models import ShiftOffer, OfferImage
from .storage import remove_image


def decimal_value(value, label, zero=False):
    try:
        result = Decimal(str(value).replace(",", "."))
        if not result.is_finite() or result < 0 or (not zero and result == 0) or result >= Decimal("10000000000") or result.as_tuple().exponent < -2:
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{label}: укажите {'неотрицательное' if zero else 'положительное'} число, до двух знаков после запятой.")


def fill(offer, data, *, existing=False):
    offer.organization = Organization.objects.get(pk=int(data["organization"]), is_active=True)
    service.authorize_sender(offer.sender_id, offer.organization_id)
    offer.date = date.fromisoformat(str(data["date"]))
    if not existing:
        if data.get("service"):
            catalog = Service.objects.get(pk=int(data["service"]), active=True, deleted_at=None)
            offer.service, offer.service_name, offer.input_type = catalog, catalog.name, catalog.input_type
        else:
            offer.service_name = str(data.get("service_name", "")).strip()
            offer.input_type = str(data.get("input_type", ""))
    if not offer.service_name or len(offer.service_name) > 160 or offer.input_type not in ("time", "quantity", "amount", "mark"):
        raise ValidationError("Укажите название услуги (до 160 символов) и формат ввода.")
    offer.start_time = offer.end_time = offer.units = offer.amount = None
    if offer.input_type == "time":
        if data.get("start_time") or data.get("end_time"):
            offer.start_time, offer.end_time = service.parse_time(data.get("start_time")), service.parse_time(data.get("end_time"))
            if offer.start_time == offer.end_time:
                raise ValidationError("Начало и окончание не должны совпадать.")
            minutes = ((offer.end_time.hour * 60 + offer.end_time.minute) - (offer.start_time.hour * 60 + offer.start_time.minute)) % 1440
            offer.units = (Decimal(minutes) / 60).quantize(Decimal(".01"))
        else:
            offer.units = decimal_value(data.get("units"), "Часы")
    elif offer.input_type == "quantity":
        offer.units = decimal_value(data.get("units"), "Количество")
    elif offer.input_type == "amount":
        offer.amount = decimal_value(data.get("amount"), "Сумма", zero=True)
    offer.comment = str(data.get("comment", ""))[:4000]


@transaction.atomic
def create(data, *, user_id=None, sender_name="Администратор сайта", source_key=None):
    service.lock()
    service.publication_config()
    sender = user_id if user_id is not None else service.coordinator_id()
    service.authorize_sender(sender)
    key = source_key or "manual:" + uuid4().hex
    previous = ShiftOffer.objects.filter(source_key=key).first()
    if previous:
        if previous.sender_id != sender:
            raise ValidationError("Чужое предложение.")
        return previous
    offer = ShiftOffer(source_key=key, sender_id=sender, sender_name=sender_name[:160], kind="service", state="review")
    fill(offer, data)
    service.validate_publish(offer)
    service.changed(offer, "created", f"telegram:{sender}" if user_id else "web")
    return offer


@transaction.atomic
def edit(offer_id, data, *, user_id=None, version=None):
    service.lock()
    offer = ShiftOffer.objects.get(pk=offer_id, kind="service")
    service.authorize_edit(offer, user_id)
    service.require_current(offer, version)
    if offer.state not in ("review", "open"):
        raise ValidationError("Сначала снимите сотрудника; завершённые предложения менять нельзя.")
    before = service.snapshot(offer)
    merged = {**before, **data}
    fill(offer, merged, existing=True)
    service.validate_publish(offer)
    service.changed(offer, "edited", f"telegram:{user_id}" if user_id else "web", before)
    return offer


@transaction.atomic
def attach(offer_id, path, digest, message_id, *, user_id=None, version=None, file_id=""):
    service.lock()
    offer = ShiftOffer.objects.get(pk=offer_id, kind="service")
    service.authorize_edit(offer, user_id)
    service.require_current(offer, version)
    if offer.state != "review":
        raise ValidationError("Фотографии прикладываются до публикации.")
    if offer.images.filter(message_id=message_id).exists():
        transaction.on_commit(lambda: remove_image(path))
        return offer
    if offer.images.count() >= 10:
        raise ValidationError("Можно приложить не более 10 фотографий.")
    OfferImage.objects.create(offer=offer, path=path, sha256=digest, message_id=message_id, telegram_file_id=file_id)
    service.changed(offer, "image_received", f"telegram:{user_id}" if user_id else "web")
    return offer
