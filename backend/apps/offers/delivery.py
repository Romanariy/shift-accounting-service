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
        if delivery.purpose in ("photos", "topic"):
            if offer.state != "publishing":
                delivery.state = "cancelled"
                delivery.save()
                continue
            try:
                service.validate_publish(offer)
            except ValidationError as error:
                before = service.snapshot(offer)
                offer.state, offer.error = "failed", " ".join(error.messages)
                service.changed(offer, "publication_blocked", "telegram-worker", before)
                offer.deliveries.filter(purpose__in=("photos", "topic"), state="pending").update(state="cancelled")
                service.prompt(offer)
                continue
            if delivery.purpose == "topic" and not offer.deliveries.filter(purpose="photos", state="sent").exists():
                continue
        elif delivery.purpose in ("review", "question", "edit_menu", "assignment") and delivery.version != offer.version:
            delivery.state = "cancelled"
            delivery.save()
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
    if delivery.purpose == "topic" and offer.state == "publishing":
        before = service.snapshot(offer)
        offer.state, offer.published_at = "open", timezone.now()
        service.changed(offer, "published", "telegram-worker", before)
    # State could change while sending; the corrective edit always uses current database state.
    if delivery.purpose != "photos":
        OfferMessageEdit.objects.update_or_create(delivery=delivery, defaults={"state": "pending", "available_at": timezone.now()})
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
    if current.revision != edit.revision:
        return
    current.state = "failed" if permanent else "pending" if error else "done"
    current.error = error
    current.available_at = timezone.now() + timedelta(seconds=delay)
    current.save()


async def run_once(bot):
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
    from aiogram.types import FSInputFile, InputMediaPhoto, InlineKeyboardMarkup
    await sync_to_async(WorkerHeartbeat.objects.update_or_create)(name="offers", defaults={"detail": "queue_ready"})
    edit = await sync_to_async(claim_edit)()
    if edit:
        try:
            text, markup = await sync_to_async(render)(edit.delivery)
            # ForceReply only applies to the initial send; editing it away would break the reply flow.
            if edit.delivery.purpose != "question" or edit.delivery.reply_resolved or edit.delivery.version != edit.delivery.offer.version:
                for message_id in edit.delivery.message_ids:
                    await bot.edit_message_text(chat_id=edit.delivery.recipient, message_id=message_id, text=text,
                        reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None)
            await sync_to_async(finish_edit)(edit)
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
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
        if delivery.purpose == "photos":
            images = await sync_to_async(list)(delivery.offer.images.filter(purged_at=None))
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
        if delivery.purpose != "photos" and len(message_ids) != 1:
            raise ValidationError("Для управляющего сообщения нужен один ID.")
        finish_delivery(delivery.pk, message_ids)
    elif resolution == "not_sent":
        delivery.state, delivery.available_at, delivery.error = "pending", timezone.now(), ""
        delivery.save()
    else:
        raise ValidationError("Подтвердите отправку или отсутствие сообщения в Telegram.")
    log_change("shift_offer", offer.pk, "delivery_reconciled", "web", after={"delivery": delivery.pk, "resolution": resolution, "message_ids": message_ids})
