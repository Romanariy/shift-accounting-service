"""All offer transitions run under the ledger lock; OCR and network IO never do."""
import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ledger.engine import lock
from apps.ledger.models import Settings
from apps.shifts.audit import log_change
from apps.shifts.models import Employee, Organization
from .models import (OfferConfig, OfferDelivery, OfferImage, OfferMessageEdit,
                     RecognitionJob, RecognitionProfile, ShiftOffer)
from .storage import remove_image

TERMINAL = {"completed", "expired", "cancelled", "duplicate", "applied"}
PHOTO_PURPOSES = {"photos", "claimed_photos", "assignment_photos"}
ACTIVE_PURPOSES = {"photos", "topic"}
CLAIMED_PURPOSES = {"claimed_photos", "claimed_topic", "assignment", "assignment_photos"}
STATE_LABELS = {"collecting": "Получаем фото", "processing": "Распознаём", "needs_input": "Нужно уточнить",
    "review": "Подтвердите результат", "publishing": "Публикуется", "open": "Свободна", "claimed": "Занята",
    "completed": "Завершена", "expired": "Не была взята", "cancelled": "Отменена", "failed": "Ошибка", "duplicate": "Повтор"}
STATE_LABELS.update(update_review="Проверить изменения", applied="Обновление применено", composing="Заполняется")


def coordinator_id():
    config = Settings.objects.select_related("approver").filter(pk=1).first()
    return config.approver.user_id if config and config.approver_id else None


def allowed_organizations(user_id):
    if user_id == coordinator_id():
        return set(Organization.objects.filter(is_active=True).values_list("id", flat=True))
    return set(RecognitionProfile.objects.filter(active=True, organization__is_active=True,
        publishers__user_id=user_id).values_list("organization_id", flat=True))


def authorize_sender(user_id, organization_id=None):
    allowed = allowed_organizations(user_id)
    if not allowed or (organization_id is not None and organization_id not in allowed):
        raise ValidationError("Нет разрешения публиковать предложения этой организации. Обратитесь к администратору.")
    return allowed


def authorize_edit(offer, user_id):
    if user_id is None:  # Website is protected by the project's administrative Basic Auth.
        return
    if user_id != coordinator_id() and user_id != offer.sender_id:
        raise ValidationError("Изменить предложение может его отправитель или главный администратор.")
    authorize_sender(user_id, offer.organization_id)


def snapshot(offer):
    return {"kind": offer.kind, "service": offer.service_id, "service_name": offer.service_name,
            "input_type": offer.input_type, "units": offer.units, "amount": offer.amount,
            "update_target": offer.update_target_id, "update_target_version": offer.update_target_version,
            "update_mode": offer.update_mode,
            "state": offer.state, "date": offer.date, "organization": offer.organization_id,
            "start_time": offer.start_time, "end_time": offer.end_time, "employee": offer.employee_id,
            "employee_name": offer.employee_name, "comment": offer.comment, "version": offer.version,
            "intervals": deepcopy(offer.intervals), "questions": deepcopy(offer.questions), "author": offer.sender_id}


def changed(offer, action, actor, before=None):
    offer.version += 1
    offer.save()
    log_change("shift_offer", offer.pk, action, actor=actor, before=before, after=snapshot(offer))
    for delivery in offer.deliveries.filter(deleted_at=None):
        reconcile_message(delivery, offer)


def obsolete(delivery, offer):
    if delivery.purpose in ACTIVE_PURPOSES:
        return delivery.generation != offer.delivery_generation or offer.state not in ("publishing", "open")
    if delivery.purpose in CLAIMED_PURPOSES:
        return delivery.generation != offer.delivery_generation or offer.state not in ("claimed", "completed")
    return delivery.purpose == "released"  # Superseded legacy private release notifications.


def reconcile_message(delivery, offer):
    """Also mark in-flight sends: once their IDs arrive they must be deleted, never resurrected."""
    if obsolete(delivery, offer):
        delivery.delete_requested = True
        if delivery.state in ("pending", "failed") and not delivery.message_ids:
            delivery.state = "cancelled"
        delivery.save(update_fields=("delete_requested", "state"))
    if delivery.state != "sent" or delivery.deleted_at:
        return
    if not delivery.delete_requested and (delivery.purpose in PHOTO_PURPOSES or delivery.purpose in ("release_notice", "update_notice", "wizard")):
        return
    edit, _ = OfferMessageEdit.objects.get_or_create(delivery=delivery, defaults={"available_at": timezone.now()})
    edit.revision += 1
    edit.state, edit.available_at, edit.error = "pending", timezone.now(), ""
    edit.save()


def enqueue(offer, purpose, recipient, *, thread_id=None, suffix="", prompt_key="", payload=None):
    if purpose in PHOTO_PURPOSES:
        ids = list(dict((digest, pk) for pk, digest in offer.images.filter(active=True, purged_at=None).values_list("id", "sha256")).values())
        if not ids:
            return None
        payloads = [{"image_ids": ids[i:i + 10]} for i in range(0, len(ids), 10)]
    else:
        payloads = [payload or {}]
    deliveries = []
    for index, item in enumerate(payloads):
        tail = suffix if index == 0 else f"{suffix}:part:{index}"
        deliveries.append(OfferDelivery.objects.get_or_create(key=f"{offer.pk}:{offer.version}:{purpose}:{recipient}:{tail}",
            defaults={"offer": offer, "purpose": purpose, "recipient": recipient, "thread_id": thread_id,
                      "version": offer.version, "generation": offer.delivery_generation, "payload": item,
                      "prompt_key": prompt_key, "available_at": timezone.now()})[0])
    return deliveries[0]


def publication_config(require_enabled=True):
    from .routing import validate_routes
    config, _ = OfferConfig.objects.get_or_create(pk=1)
    if require_enabled and not config.enabled:
        raise ValidationError("Приём и публикация предложений выключены.")
    if any(value is None for value in (config.chat_id, config.thread_id, config.claimed_thread_id, config.released_thread_id)):
        raise ValidationError("Настройте топики активных и взятых смен и уведомлений об освобождении на сайте.")
    validate_routes(Settings.objects.get(pk=1), config)
    return config


def enqueue_active(offer):
    enqueue(offer, "photos", offer.topic_chat_id, thread_id=offer.topic_thread_id)
    enqueue(offer, "topic", offer.topic_chat_id, thread_id=offer.topic_thread_id)


def prompt(offer):
    from .updates import prepare
    prepare(offer)
    if offer.state in ("update_review", "duplicate"):
        enqueue(offer, "update_review" if offer.state == "update_review" else "notice", offer.sender_id)
        return
    purpose = "question" if offer.questions else "review"
    key = offer.questions[0]["key"] if offer.questions else ""
    enqueue(offer, purpose, offer.sender_id, prompt_key=key)


@transaction.atomic
def receive_image(user_id, sender_name, source_key, message_id, path, sha256, file_id="", caption=""):
    lock()
    config, _ = OfferConfig.objects.get_or_create(pk=1)
    if not config.enabled:
        raise ValidationError("Приём предложений пока выключен администратором.")
    authorize_sender(user_id)
    offer, created = ShiftOffer.objects.get_or_create(source_key=source_key,
        defaults={"sender_id": user_id, "sender_name": sender_name})
    if offer.sender_id != user_id:
        raise ValidationError("Чужой альбом.")
    if offer.images.filter(message_id=message_id).exists():
        transaction.on_commit(lambda: remove_image(path))
        return offer, False
    if offer.state not in ("collecting", "processing", "review", "needs_input", "failed"):
        raise ValidationError("Альбом уже опубликован. Отправьте новый альбом для новой заявки.")
    if offer.images.count() >= 10:
        raise ValidationError("В одном предложении допускается до 10 фотографий.")
    before = snapshot(offer)
    OfferImage.objects.create(offer=offer, path=path, sha256=sha256, message_id=message_id, telegram_file_id=file_id)
    if caption and (offer.caption_message_id is None or message_id < offer.caption_message_id):
        offer.comment, offer.caption_message_id = caption[:4000], message_id
    offer.state, offer.questions, offer.error = "collecting", [], ""
    changed(offer, "image_received", f"telegram:{user_id}", before)
    # Persist album assembly. Late parts invalidate the previous job revision, even across restarts.
    offer.jobs.filter(state="pending").update(state="cancelled")
    RecognitionJob.objects.create(offer=offer, revision=offer.version, available_at=timezone.now() + timedelta(seconds=1.5))
    return offer, created


def end_at(offer):
    if offer.kind == "service" and offer.date and not offer.start_time and not offer.end_time:
        return timezone.make_aware(datetime.combine(offer.date + timedelta(days=1), time.min))
    if not (offer.date and offer.start_time and offer.end_time):
        return None
    day = offer.date + timedelta(days=int(offer.end_time < offer.start_time))
    return timezone.make_aware(datetime.combine(day, offer.end_time))


def require_current(offer, version):
    if version is not None and offer.version != int(version):
        raise ValidationError("Предложение изменилось. Откройте последнее сообщение или обновите страницу.")


def validate_publish(offer):
    if offer.questions:
        raise ValidationError("Сначала ответьте на вопросы распознавания.")
    if offer.kind == "service":
        if not offer.organization_id or not offer.date or not offer.service_name:
            raise ValidationError("Укажите организацию, дату и услугу.")
        if not offer.organization.is_active or not end_at(offer) or end_at(offer) <= timezone.now():
            raise ValidationError("Предложение на прошедшее время или неактивную организацию.")
        if offer.start_time and timezone.make_aware(datetime.combine(offer.date, offer.start_time)) <= timezone.now():
            raise ValidationError("Начало услуги уже прошло.")
        authorize_sender(offer.sender_id, offer.organization_id)
        return
    if not offer.organization_id or not offer.date or not offer.start_time or not offer.end_time:
        raise ValidationError("Нужны организация, дата, начало и окончание.")
    if not offer.organization.is_active or offer.start_time >= offer.end_time:
        raise ValidationError("Проверьте организацию и интервал смены.")
    start = timezone.make_aware(datetime.combine(offer.date, offer.start_time))
    if start <= timezone.now():
        raise ValidationError("Нельзя публиковать предложение на прошедшее время. Исправьте дату или начало.")
    authorize_sender(offer.sender_id, offer.organization_id)


@transaction.atomic
def publish(offer_id, *, user_id=None, version=None):
    lock()
    offer = ShiftOffer.objects.select_related("organization").get(pk=offer_id)
    authorize_edit(offer, user_id)
    require_current(offer, version)
    if offer.state not in ("review", "needs_input"):
        raise ValidationError("Это предложение уже опубликовано или закрыто.")
    validate_publish(offer)
    from .updates import prepare
    if prepare(offer):
        prompt(offer)
        return offer
    config = publication_config()
    before = snapshot(offer)
    offer.state, offer.topic_chat_id, offer.topic_thread_id = "publishing", config.chat_id, config.thread_id
    offer.delivery_generation += 1
    changed(offer, "publish_requested", f"telegram:{user_id}" if user_id else "web", before)
    enqueue_active(offer)
    return offer


@transaction.atomic
def claim(offer_id, user_id, version=None, actor=None):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    require_current(offer, version)
    if offer.state != "open" or not end_at(offer) or end_at(offer) <= timezone.now():
        raise ValidationError("Эта смена уже занята или закрыта.")
    employees = list(Employee.objects.filter(telegram_user_id=user_id, is_active=True)[:2])
    if len(employees) != 1:
        raise ValidationError("Администратор должен привязать ваш Telegram ID к одному активному сотруднику.")
    employee = employees[0]
    config = publication_config(require_enabled=False)
    before = snapshot(offer)
    offer.state, offer.employee, offer.employee_name, offer.assignee_user_id = "claimed", employee, employee.display_name, user_id
    offer.delivery_generation += 1
    changed(offer, "claimed", actor or f"telegram:{user_id}", before)
    enqueue(offer, "claimed_photos", config.chat_id, thread_id=config.claimed_thread_id)
    enqueue(offer, "claimed_topic", config.chat_id, thread_id=config.claimed_thread_id)
    for recipient in {user_id, coordinator_id()} - {None}:
        enqueue(offer, "assignment_photos", recipient)
        enqueue(offer, "assignment", recipient)
    return offer


@transaction.atomic
def release(offer_id, *, user_id=None, version=None):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    require_current(offer, version)
    if user_id is not None and user_id not in (offer.assignee_user_id, coordinator_id()):
        raise ValidationError("Снять сотрудника может он сам или главный администратор.")
    if offer.state != "claimed" or end_at(offer) <= timezone.now():
        raise ValidationError("Смена уже свободна или завершена.")
    previous_name = offer.employee_name
    before = snapshot(offer)
    offer.state, offer.employee, offer.employee_name, offer.assignee_user_id = "open", None, "", None
    offer.delivery_generation += 1
    config = OfferConfig.objects.filter(pk=1).first()
    # Releasing must remain possible even when intake is disabled. Preserve the original
    # active route if settings were cleared after this offer was published.
    if config and config.chat_id and config.thread_id:
        offer.topic_chat_id, offer.topic_thread_id = config.chat_id, config.thread_id
    changed(offer, "released", f"telegram:{user_id}" if user_id else "web", before)
    if offer.topic_chat_id and offer.topic_thread_id:
        enqueue_active(offer)
    if config and config.chat_id and config.released_thread_id:
        from .presentation import describe
        enqueue(offer, "release_notice", config.chat_id, thread_id=config.released_thread_id,
                payload={"text": "Предложение освободилось\n" + describe(offer) +
                         f"\nСнят сотрудник: {previous_name}\nПредложение возвращено в активные."})
    return offer


@transaction.atomic
def cancel(offer_id, *, user_id=None, version=None):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    authorize_edit(offer, user_id)
    require_current(offer, version)
    if offer.state in TERMINAL:
        return offer
    before = snapshot(offer)
    offer.state, offer.closed_at = "cancelled", timezone.now()
    offer.jobs.filter(state="pending").update(state="cancelled")
    changed(offer, "cancelled", f"telegram:{user_id}" if user_id else "web", before)
    for recipient in {offer.assignee_user_id, coordinator_id(), offer.sender_id} - {None}:
        enqueue(offer, "notice", recipient)
    return offer


def parse_time(value):
    try:
        return time.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError("Укажите время в формате ЧЧ:ММ.")


def recompute(offer):
    valid = [item for item in offer.intervals if item.get("start") and item.get("end")]
    if valid:
        offer.start_time = parse_time(min(item["start"] for item in valid))
        offer.end_time = parse_time(max(item["end"] for item in valid))


@transaction.atomic
def answer(offer_id, answers, *, user_id=None, version=None):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    authorize_edit(offer, user_id)
    require_current(offer, version)
    if offer.kind == "service":
        from .manual import edit
        result = edit(offer_id, answers, user_id=user_id, version=version)
        prompt(result)
        return result
    if offer.state not in ("needs_input", "review", "failed"):
        raise ValidationError("Ответить можно до публикации предложения.")
    if not isinstance(answers, dict):
        raise ValidationError("Нужен объект ответов.")
    before = snapshot(offer)
    known = {q["key"] for q in offer.questions} | {"date", "organization", "start_time", "end_time", "comment", "intervals"}
    for key, value in answers.items():
        if key not in known:
            raise ValidationError("Неизвестное поле уточнения.")
        if key == "split":
            raise ValidationError("Разделите изображения на отдельные предложения на сайте.")
        if key == "date":
            try:
                offer.date = date.fromisoformat(str(value))
            except ValueError:
                try:
                    offer.date = datetime.strptime(str(value), "%d.%m.%Y").date()
                except ValueError:
                    raise ValidationError("Укажите полную дату: ДД.ММ.ГГГГ.")
        elif key == "organization":
            try:
                org = Organization.objects.get(pk=int(value), is_active=True)
            except (ValueError, TypeError, Organization.DoesNotExist):
                raise ValidationError("Выберите действующую организацию.")
            authorize_sender(offer.sender_id, org.pk)
            offer.organization = org
        elif key in ("start_time", "end_time"):
            setattr(offer, key, parse_time(value))
        elif key == "comment":
            offer.comment = str(value)[:4000]
        elif key.startswith("interval:"):
            _, index, field = key.split(":")
            if field not in ("start", "end") or not 0 <= int(index) < len(offer.intervals):
                raise ValidationError("Неверный номер записи.")
            offer.intervals[int(index)][field] = parse_time(value).strftime("%H:%M")
            offer.intervals[int(index)]["human_verified"] = True
        elif key == "intervals":
            if not isinstance(value, list) or not 1 <= len(value) <= 100:
                raise ValidationError("Укажите список интервалов записей.")
            offer.intervals = [{"start": parse_time(item["start"]).strftime("%H:%M"),
                "end": parse_time(item["end"]).strftime("%H:%M"), "room": str(item.get("room", ""))[:120],
                "human_verified": True} for item in value]
    if any(k.startswith("interval:") or k == "intervals" for k in answers):
        if any(i.get("start") and i.get("end") and i["start"] >= i["end"] for i in offer.intervals):
            raise ValidationError("Окончание каждой записи должно быть позже начала в этот день.")
        recompute(offer)
    offer.questions = [q for q in offer.questions if q["key"] not in answers
        and not ("intervals" in answers and q["key"].startswith("interval:"))]
    offer.state, offer.error = ("needs_input" if offer.questions else "review"), ""
    changed(offer, "clarified", f"telegram:{user_id}" if user_id else "web", before)
    offer.deliveries.filter(purpose="question", prompt_key__in=answers).update(reply_resolved=True)
    prompt(offer)
    return offer


@transaction.atomic
def edit_published(offer_id, payload, version=None):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    require_current(offer, version)
    if offer.kind == "service":
        from .manual import edit
        return edit(offer_id, payload, version=version)
    if offer.state not in ("open", "claimed"):
        raise ValidationError("Редактирование доступно для открытой или занятой смены.")
    # Significant changes invalidate the assignment, rather than silently changing an employee's commitment.
    if offer.state == "claimed" and any(k in payload for k in ("date", "start_time", "end_time", "organization")):
        raise ValidationError("Сначала снимите сотрудника, затем измените дату, организацию или время.")
    before = snapshot(offer)
    for key in ("start_time", "end_time"):
        if key in payload:
            setattr(offer, key, parse_time(payload[key]))
    if "date" in payload:
        offer.date = date.fromisoformat(payload["date"])
    if "organization" in payload:
        offer.organization = Organization.objects.get(pk=int(payload["organization"]), is_active=True)
    if "comment" in payload:
        offer.comment = str(payload["comment"])[:4000]
    if any(k in payload for k in ("date", "start_time", "end_time", "organization")):
        validate_publish(offer)
    changed(offer, "edited", "web", before)
    return offer


@transaction.atomic
def split_offer(offer_id, groups, version=None):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    require_current(offer, version)
    if offer.state not in ("needs_input", "review", "failed"):
        raise ValidationError("Разделение доступно до публикации.")
    ids = set(offer.images.values_list("id", flat=True))
    if not isinstance(groups, list) or not 2 <= len(groups) <= 10 or any(not isinstance(g, list) or not g for g in groups):
        raise ValidationError("Нужны минимум две непустые группы изображений.")
    flat = [int(i) for group in groups for i in group]
    if set(flat) != ids or len(flat) != len(ids):
        raise ValidationError("Каждое изображение должно входить ровно в одну группу.")
    children = []
    for index, group in enumerate(groups):
        child = ShiftOffer.objects.create(source_key=f"split:{offer.pk}:{index}", sender_id=offer.sender_id,
            sender_name=offer.sender_name, comment=offer.comment, state="processing")
        offer.images.filter(pk__in=group).update(offer=child)
        changed(child, "split_created", "web", {"parent": offer.pk})
        RecognitionJob.objects.create(offer=child, revision=child.version, available_at=timezone.now())
        children.append(child.pk)
    before = snapshot(offer)
    offer.state, offer.closed_at = "cancelled", timezone.now()
    offer.jobs.filter(state="pending").update(state="cancelled")
    changed(offer, "split", "web", before)
    log_change("shift_offer", offer.pk, "split_children", "web", after={"children": children})
    return children


@transaction.atomic
def retry_recognition(offer_id):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    if offer.kind != "shift":
        raise ValidationError("Вложения услуги не являются расписанием.")
    if offer.state not in ("failed", "needs_input", "review") or not offer.images.filter(purged_at=None).exists():
        raise ValidationError("Повтор доступен до публикации, пока сохранены фотографии.")
    before = snapshot(offer)
    offer.state, offer.questions, offer.error = "processing", [], ""
    changed(offer, "recognition_retry", "web", before)
    RecognitionJob.objects.create(offer=offer, revision=offer.version, available_at=timezone.now())
    return offer


def quality_gate():
    if not settings.OFFER_AUTO_PUBLISH or not settings.OFFER_QUALITY_REPORT:
        return False
    try:
        from pathlib import Path
        report = json.loads(Path(settings.OFFER_QUALITY_REPORT).read_text(encoding="utf-8"))
        from .recognizer import ENGINE_VERSION
        return (report["engine_version"] == ENGINE_VERSION and report["independent"] is True
                and report["lower_precision_95"] > .99 and report["coverage"] >= .90
                and report["accepted"] >= 300 and report["split_by_album"] is True)
    except (OSError, ValueError, KeyError, TypeError):
        return False


@transaction.atomic
def expire_offers():
    lock()
    now = timezone.now()
    for offer in ShiftOffer.objects.filter(state__in=("open", "claimed", "publishing"), date__lte=timezone.localdate()):
        if end_at(offer) and end_at(offer) <= now:
            before = snapshot(offer)
            offer.state, offer.closed_at = ("completed" if offer.employee_id else "expired"), now
            changed(offer, "closed", "system", before)
    # Abandoned private drafts cannot retain photos indefinitely.
    for offer in ShiftOffer.objects.filter(state__in=("composing", "collecting", "processing", "needs_input", "review", "update_review", "failed"),
            updated_at__lt=now - timedelta(days=30)):
        before = snapshot(offer)
        offer.state, offer.closed_at = "cancelled", now
        changed(offer, "abandoned", "system", before)


def purge_images():
    cutoff = timezone.now() - timedelta(days=30)
    for image in OfferImage.objects.filter(offer__closed_at__lt=cutoff, purged_at=None):
        remove_image(image.path)
        image.path, image.recognized, image.purged_at = "", {}, timezone.now()
        image.save(update_fields=("path", "recognized", "purged_at"))
        log_change("shift_offer", image.offer_id, "image_purged", actor="system", after={"image": image.pk})
    for profile in RecognitionProfile.objects.exclude(sample_path="").filter(updated_at__lt=timezone.now() - timedelta(days=1)):
        # A newly uploaded reference may replace this path while cleanup is running.
        cleared = RecognitionProfile.objects.filter(pk=profile.pk, sample_path=profile.sample_path,
            sample_version=profile.sample_version).update(sample_path="")
        if cleared:
            remove_image(profile.sample_path)
    # Clean files left by a process crash between storing a file and committing its database row.
    from pathlib import Path
    root = Path(settings.OFFER_MEDIA_ROOT).resolve()
    references = set(OfferImage.objects.exclude(path="").values_list("path", flat=True))
    references.update(RecognitionProfile.objects.exclude(header_path="").values_list("header_path", flat=True))
    references.update(RecognitionProfile.objects.exclude(sample_path="").values_list("sample_path", flat=True))
    cutoff_stamp = (timezone.now() - timedelta(days=1)).timestamp()
    for path in (root / "images").glob("*.png"):
        if path.relative_to(root).as_posix() not in references and path.stat().st_mtime < cutoff_stamp:
            remove_image(path.relative_to(root).as_posix())
