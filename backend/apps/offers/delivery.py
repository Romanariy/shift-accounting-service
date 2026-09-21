"""Durable Telegram outbox: uncertain sends require reconciliation; edits are retryable."""
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ledger.engine import lock
from apps.shifts.audit import log_change
from . import service
from .models import OfferDelivery, OfferMessageEdit, ShiftOffer, WorkerHeartbeat
from .presentation import render
from .storage import media_path


@transaction.atomic
def claim_delivery():
    lock()
    now = timezone.now()
    # A process may die after Telegram accepted a send but before the result was committed.
    OfferDelivery.objects.filter(state="sending", started_at__lt=now - timedelta(minutes=3)).update(
        state="unknown", error="Отправка прервалась. Проверьте Telegram перед повтором.")
    for delivery in OfferDelivery.objects.filter(state="pending", available_at__lte=now).select_related("offer__organization").order_by("id")[:100]:
        offer = delivery.offer
        if delivery.delete_requested or service.obsolete(delivery, offer):
            delivery.state = "cancelled"
            delivery.save()
            continue
        if delivery.purpose in service.ACTIVE_PURPOSES and offer.state == "publishing":
            try:
                service.validate_publish(offer)
            except ValidationError as error:
                before = service.snapshot(offer)
                offer.state, offer.error = "failed", " ".join(error.messages)
                service.changed(offer, "publication_blocked", "telegram-worker", before)
                offer.deliveries.filter(purpose__in=("photos", "topic"), state="pending").update(state="cancelled")
                service.prompt(offer)
                continue
        elif delivery.purpose in ("review", "question", "edit_menu", "update_review") and delivery.version != offer.version:
            delivery.state = "cancelled"
            delivery.save()
            continue
        photo_purpose = {"topic": "photos", "claimed_topic": "claimed_photos", "assignment": "assignment_photos"}.get(delivery.purpose)
        if photo_purpose:
            photos = offer.deliveries.filter(purpose=photo_purpose, deleted_at=None, delete_requested=False,
                generation=delivery.generation, recipient=delivery.recipient, thread_id=delivery.thread_id)
            if photos.exclude(state="sent").exists():
                continue
        delivery.state, delivery.started_at, delivery.attempts = "sending", now, delivery.attempts + 1
        delivery.save()
        return delivery
    return None


@transaction.atomic
def finish_delivery(delivery_id, message_ids, attempt=None):
    lock()
    delivery = OfferDelivery.objects.select_related("offer").get(pk=delivery_id)
    if attempt is not None and (delivery.attempts != attempt or delivery.state not in ("sending", "unknown")):
        return
    delivery.state, delivery.message_ids, delivery.error = "sent", message_ids, ""
    delivery.save()
    offer = delivery.offer
    if (delivery.purpose == "topic" and offer.state == "publishing"
            and delivery.generation == offer.delivery_generation and not delivery.delete_requested):
        before = service.snapshot(offer)
        offer.state, offer.published_at = "open", timezone.now()
        service.changed(offer, "published", "telegram-worker", before)
    # State could change while sending; the corrective edit always uses current database state.
    service.reconcile_message(delivery, offer)
    log_change("shift_offer", offer.pk, "delivered", "telegram-worker", after={"delivery": delivery.pk, "purpose": delivery.purpose})


@transaction.atomic
def fail_delivery(delivery_id, state, text, delay=0, attempt=None):
    lock()
    delivery = OfferDelivery.objects.get(pk=delivery_id)
    if attempt is not None and (delivery.attempts != attempt or delivery.state != "sending"):
        return
    delivery.state, delivery.error = state, text
    delivery.available_at = timezone.now() + timedelta(seconds=delay)
    delivery.save()
    log_change("shift_offer", delivery.offer_id, "delivery_" + state, "telegram-worker", after={"delivery": delivery.pk, "reason": text})


@transaction.atomic
def claim_edit():
    lock()
    now = timezone.now()
    OfferMessageEdit.objects.filter(state="running", started_at__lt=now - timedelta(minutes=3)).update(state="pending", available_at=now)
    edit = OfferMessageEdit.objects.filter(state="pending", available_at__lte=now).select_related("delivery__offer__organization").order_by("available_at").first()
    if edit:
        edit.state, edit.started_at, edit.attempts = "running", now, edit.attempts + 1
        edit.save()
    return edit


@transaction.atomic
def finish_edit(edit, error="", delay=0, permanent=False):
    lock()
    current = OfferMessageEdit.objects.get(pk=edit.pk)
    if current.revision != edit.revision or current.attempts != edit.attempts:
        return
    current.state = "failed" if permanent else "pending" if error else "done"
    current.error = error
    current.available_at = timezone.now() + timedelta(seconds=delay)
    current.save()
    if not error and edit.delivery.delete_requested:
        OfferDelivery.objects.filter(pk=edit.delivery_id, delete_requested=True).update(deleted_at=timezone.now())


async def run_once(bot):
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
    from aiogram.types import FSInputFile, InputMediaPhoto, InlineKeyboardMarkup
    await sync_to_async(WorkerHeartbeat.objects.update_or_create)(name="offers", defaults={"detail": "queue_ready"})
    edit = await sync_to_async(claim_edit)()
    if edit:
        try:
            if edit.delivery.delete_requested:
                for message_id in edit.delivery.message_ids:
                    try:
                        await bot.delete_message(chat_id=edit.delivery.recipient, message_id=message_id)
                    except TelegramBadRequest as error:
                        if "message to delete not found" not in str(error).lower():
                            raise
            else:
                text, markup = await sync_to_async(render)(edit.delivery)
                # ForceReply only applies to the initial send; editing it away would break the reply flow.
                if edit.delivery.purpose != "question" or edit.delivery.reply_resolved or edit.delivery.version != edit.delivery.offer.version:
                    for message_id in edit.delivery.message_ids:
                        await bot.edit_message_text(chat_id=edit.delivery.recipient, message_id=message_id, text=text,
                            reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None)
            await sync_to_async(finish_edit)(edit)
        except TelegramBadRequest as error:
            if edit.delivery.delete_requested:
                # Telegram disallows deletion after 48 hours. At least remove obsolete
                # controls; keep a visible, retryable cleanup error instead of claiming success.
                for message_id in edit.delivery.message_ids:
                    try:
                        await bot.edit_message_reply_markup(chat_id=edit.delivery.recipient, message_id=message_id, reply_markup=None)
                    except Exception:
                        pass
                await sync_to_async(finish_edit)(edit, "Telegram не разрешил удалить сообщение: оно старше 48 часов или нет прав. Удалите вручную либо восстановите права и повторите очистку.", permanent=True)
            elif "message is not modified" in str(error).lower():
                await sync_to_async(finish_edit)(edit)
            else:
                await sync_to_async(finish_edit)(edit, "Telegram отклонил обновление. Сообщение могло быть удалено.", permanent=True)
        except TelegramForbiddenError:
            await sync_to_async(finish_edit)(edit, "Нет доступа к сообщению.", permanent=True)
        except TelegramRetryAfter as error:
            await sync_to_async(finish_edit)(edit, "Ограничение Telegram.", delay=error.retry_after + 1)
        except Exception:
            await sync_to_async(finish_edit)(edit, "Не удалось обновить сообщение; повторим автоматически.", delay=min(300, 2 ** min(edit.attempts, 8)))
        return True
    delivery = await sync_to_async(claim_delivery)()
    if not delivery:
        return False
    try:
        target = {"chat_id": delivery.recipient}
        if delivery.thread_id:
            target["message_thread_id"] = delivery.thread_id
        if delivery.purpose in service.PHOTO_PURPOSES:
            image_query = delivery.offer.images.filter(purged_at=None)
            image_query = image_query.filter(pk__in=delivery.payload["image_ids"]) if "image_ids" in delivery.payload else image_query.filter(active=True)
            images = await sync_to_async(list)(image_query)
            # Deduplicate repeated screenshots within an album while keeping audit/source records.
            images = list({i.sha256: i for i in images}.values())
            if not images:
                raise ValidationError("Изображения удалены.")
            files = [FSInputFile(media_path(i.path)) for i in images]
            if len(files) == 1:
                result = [await bot.send_photo(**target, photo=files[0])]
            else:
                result = await bot.send_media_group(**target, media=[InputMediaPhoto(media=file) for file in files])
        else:
            text, markup = await sync_to_async(render)(delivery)
            result = [await bot.send_message(**target, text=text, reply_markup=markup)]
        await sync_to_async(finish_delivery)(delivery.pk, [m.message_id for m in result], attempt=delivery.attempts)
    except TelegramRetryAfter as error:
        await sync_to_async(fail_delivery)(delivery.pk, "pending", "Ограничение Telegram.", delay=error.retry_after + 1, attempt=delivery.attempts)
    except (TelegramBadRequest, TelegramForbiddenError, ValidationError, FileNotFoundError):
        await sync_to_async(fail_delivery)(delivery.pk, "failed", "Отправка отклонена. Проверьте права бота, топик и доступность личного чата.", attempt=delivery.attempts)
    except Exception:
        await sync_to_async(fail_delivery)(delivery.pk, "unknown", "Нет подтверждения доставки. Проверьте Telegram: сообщение могло быть отправлено.", attempt=delivery.attempts)
    return True


@transaction.atomic
def resolve_delivery(offer_id, delivery_id, resolution, message_ids, version):
    lock()
    offer = ShiftOffer.objects.get(pk=offer_id)
    service.require_current(offer, version)
    delivery = offer.deliveries.get(pk=delivery_id)
    if resolution == "retry_edit":
        edit = OfferMessageEdit.objects.get(delivery=delivery, state="failed")
        edit.state, edit.error, edit.available_at = "pending", "", timezone.now()
        edit.revision += 1
        edit.save()
        log_change("shift_offer", offer.pk, "message_edit_retried", "web", after={"delivery": delivery.pk})
        return
    if delivery.state not in ("unknown", "failed"):
        raise ValidationError("Эта доставка уже обработана.")
    if resolution == "sent":
        if delivery.state != "unknown" or not isinstance(message_ids, list) or not message_ids or any(type(n) is not int or n <= 0 for n in message_ids):
            raise ValidationError("Укажите ID реально отправленных сообщений из Telegram.")
        if delivery.purpose not in service.PHOTO_PURPOSES and len(message_ids) != 1:
            raise ValidationError("Для управляющего сообщения нужен один ID.")
        finish_delivery(delivery.pk, message_ids)
    elif resolution == "not_sent":
        delivery.state, delivery.available_at, delivery.error = "pending", timezone.now(), ""
        delivery.save()
    else:
        raise ValidationError("Подтвердите отправку или отсутствие сообщения в Telegram.")
    log_change("shift_offer", offer.pk, "delivery_reconciled", "web", after={"delivery": delivery.pk, "resolution": resolution, "message_ids": message_ids})
