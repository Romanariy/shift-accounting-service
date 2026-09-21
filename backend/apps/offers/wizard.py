"""Restart-safe /offer dialog. Prompts use the same durable Telegram outbox."""
from datetime import datetime
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ledger.models import Service
from apps.shifts.models import Organization
from . import service, manual
from .models import OfferWizard, ShiftOffer


def instructions(wizard):
    step = wizard.step
    if step == "source":
        return "Предложить услугу: напишите 1 — из справочника, 2 — свободная услуга. /canceloffer — отмена."
    if step == "catalog":
        return "Введите номер услуги:\n" + "\n".join(f"{s.pk}. {s.name}" for s in Service.objects.filter(active=True, deleted_at=None))
    if step == "name":
        return "Напишите название свободной услуги."
    if step == "format":
        return "Формат: 1 — время или часы, 2 — количество, 3 — готовая сумма, 4 — отметка."
    if step == "organization":
        return "Введите номер организации:\n" + "\n".join(f"{o.pk}. {o.name}" for o in Organization.objects.filter(pk__in=service.allowed_organizations(wizard.user_id)))
    if step == "date":
        return "Укажите дату ДД.ММ.ГГГГ."
    if step == "value":
        return {"time": "Укажите интервал ЧЧ:ММ–ЧЧ:ММ либо число часов.", "quantity": "Укажите количество.", "amount": "Укажите готовую сумму в рублях."}[wizard.data["input_type"]]
    if step == "comment":
        return "Напишите комментарий или /skip, чтобы пропустить."
    if step == "attachments":
        return "Пришлите фотографии (до 10) или альбом. Они будут вложениями без распознавания. Затем /done — проверить и опубликовать."
    return "Предложение подготовлено."


@transaction.atomic
def consume(user_id, name, message_id, text):
    service.lock()
    value = (text or "").strip()
    command = value.split()[0].split("@")[0] if value else ""
    wizard = OfferWizard.objects.filter(user_id=user_id).first()
    if wizard and message_id in wizard.processed_messages:
        return True
    if command == "/offer":
        service.publication_config()
        service.authorize_sender(user_id)
        if wizard and wizard.step != "done" and wizard.offer and wizard.offer.state in ("composing", "review"):
            service.cancel(wizard.offer_id, user_id=user_id)
        offer, created = ShiftOffer.objects.get_or_create(source_key=f"wizard:{user_id}:{message_id}",
            defaults={"sender_id": user_id, "sender_name": name[:160], "kind": "service", "state": "composing"})
        if not created:
            return True
        wizard, _ = OfferWizard.objects.update_or_create(user_id=user_id,
            defaults={"offer": offer, "step": "source", "data": {}, "processed_messages": []})
    elif not wizard or wizard.step == "done":
        return False
    else:
        service.authorize_sender(user_id)
        data = dict(wizard.data)
        if command == "/canceloffer":
            service.cancel(wizard.offer_id, user_id=user_id)
            wizard.step = "done"
        elif wizard.step == "source":
            if value not in ("1", "2"):
                raise ValidationError("Введите 1 — из справочника или 2 — свободная услуга.")
            wizard.step = "catalog" if value == "1" else "name"
        elif wizard.step == "catalog":
            catalog = Service.objects.get(pk=int(value), active=True, deleted_at=None)
            data.update(service=catalog.pk, input_type=catalog.input_type)
            wizard.step = "organization"
        elif wizard.step == "name":
            if not value or len(value) > 160:
                raise ValidationError("Название должно содержать от 1 до 160 символов.")
            data["service_name"], wizard.step = value, "format"
        elif wizard.step == "format":
            if value not in ("1", "2", "3", "4"):
                raise ValidationError("Введите номер формата от 1 до 4.")
            data["input_type"] = {"1": "time", "2": "quantity", "3": "amount", "4": "mark"}[value]
            wizard.step = "organization"
        elif wizard.step == "organization":
            organization = int(value)
            service.authorize_sender(user_id, organization)
            data["organization"], wizard.step = organization, "date"
        elif wizard.step == "date":
            try:
                day = datetime.strptime(value, "%d.%m.%Y").date()
            except ValueError:
                day = datetime.strptime(value, "%Y-%m-%d").date()
            if day < timezone.localdate():
                raise ValidationError("Нельзя предложить услугу на прошедшую дату.")
            data["date"] = day.isoformat()
            wizard.step = "comment" if data["input_type"] == "mark" else "value"
        elif wizard.step == "value":
            match = re.fullmatch(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", value)
            if data["input_type"] == "time" and match:
                data["start_time"], data["end_time"] = [service.parse_time(v.zfill(5)).strftime("%H:%M") for v in match.groups()]
            else:
                key = "amount" if data["input_type"] == "amount" else "units"
                data[key] = str(manual.decimal_value(value, "Объём услуги", zero=key == "amount"))
            wizard.step = "comment"
        elif wizard.step == "comment":
            data["comment"] = "" if command == "/skip" else value
            offer = wizard.offer
            manual.fill(offer, data)
            service.validate_publish(offer)
            offer.state = "review"
            service.changed(offer, "created", f"telegram:{user_id}")
            wizard.step = "attachments"
        elif wizard.step == "attachments":
            if command != "/done":
                raise ValidationError("Отправьте фотографии или /done. /canceloffer — отмена.")
            service.prompt(ShiftOffer.objects.get(pk=wizard.offer_id))
            wizard.step = "done"
        wizard.data = data
    wizard.processed_messages = [*wizard.processed_messages, message_id][-200:]
    wizard.save()
    if wizard.step != "done":
        service.enqueue(wizard.offer, "wizard", user_id, suffix=str(message_id), payload={"text": instructions(wizard)})
    return True


def attachment_target(user_id):
    wizard = OfferWizard.objects.filter(user_id=user_id).exclude(step="done").first()
    if wizard and wizard.step != "attachments":
        raise ValidationError("Сначала заполните услугу. " + instructions(wizard))
    return wizard.offer_id if wizard else None
