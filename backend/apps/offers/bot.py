import io
import re

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction

from apps.ledger.engine import lock
from . import service
from .models import OfferDelivery, ShiftOffer
from .storage import remove_image, store_image


def error_text(error):
    return " ".join(error.messages) if isinstance(error, ValidationError) else "Предложение изменилось или больше недоступно. Откройте последнее сообщение."


@transaction.atomic
def callback_action(data, user_id, chat_id, message_id):
    lock()
    parts = data.split(":")
    if len(parts) not in (4, 5) or parts[0] != "offer":
        raise ValidationError("Некорректная кнопка.")
    _, action, offer_id, version = parts[:4]
    offer = ShiftOffer.objects.get(pk=int(offer_id))
    service.require_current(offer, int(version))
    deliveries = [d for d in offer.deliveries.filter(recipient=chat_id, state="sent") if message_id in d.message_ids]
    if not deliveries:
        raise ValidationError("Откройте исходное сообщение бота.")
    if action == "claim":
        if not any(d.purpose == "topic" for d in deliveries):
            raise ValidationError("Взять смену можно в топике предложений.")
        service.claim(offer.pk, user_id, int(version))
        return "Смена закреплена за вами. Кнопка «Сняться» придёт в личный чат."
    if chat_id != user_id:
        raise ValidationError("Действие доступно в личном чате бота.")
    if action == "release":
        if not any(d.purpose == "assignment" for d in deliveries):
            raise ValidationError("Откройте сообщение о назначении.")
        service.release(offer.pk, user_id=user_id, version=int(version))
        return "Сотрудник снят. Смена снова доступна в топике."
    service.authorize_edit(offer, user_id)
    if action == "publish":
        service.publish(offer.pk, user_id=user_id, version=int(version))
        return "Публикация поставлена в очередь."
    if action == "cancel":
        service.cancel(offer.pk, user_id=user_id, version=int(version))
        return "Предложение отменено."
    if offer.state not in ("review", "needs_input", "failed"):
        raise ValidationError("Редактирование через бота доступно до публикации.")
    if action == "edit":
        service.enqueue(offer, "edit_menu", user_id)
        return "Выберите поле в новом сообщении."
    if action == "field" and len(parts) == 5 and parts[4] in ("date", "organization", "start_time", "end_time", "comment"):
        service.enqueue(offer, "question", user_id, suffix=parts[4], prompt_key=parts[4])
        return "Уточнение придёт отдельным сообщением."
    if action == "org" and len(parts) == 5 and any(d.prompt_key == "organization" and not d.reply_resolved for d in deliveries):
        service.answer(offer.pk, {"organization": int(parts[4])}, user_id=user_id, version=int(version))
        return "Организация сохранена."
    raise ValidationError("Кнопка больше недоступна.")


async def process_callback(query):
    try:
        if not query.message:
            raise ValidationError("Откройте исходное сообщение.")
        text = await sync_to_async(callback_action)(query.data, query.from_user.id, query.message.chat.id, query.message.message_id)
    except (ValidationError, ObjectDoesNotExist, ValueError, TypeError) as error:
        text = error_text(error)
    await query.answer(text[:200], show_alert=True)


@transaction.atomic
def reply_to_question(user_id, reply_id, text):
    lock()
    deliveries = [d for d in OfferDelivery.objects.filter(recipient=user_id, purpose="question", state="sent", reply_resolved=False)
        .select_related("offer").order_by("-id") if reply_id in d.message_ids]
    if not deliveries:
        return False
    delivery = deliveries[0]
    value = text.strip()
    if delivery.prompt_key == "intervals":
        values = [re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})\s*", part)
                  for part in re.split(r"[,;\n]", value)]
        if not values or any(match is None for match in values):
            raise ValidationError("Напишите интервалы: 13:00–16:00, 16:30–20:00.")
        value = [{"start": match[1].zfill(5), "end": match[2].zfill(5)} for match in values]
    elif delivery.prompt_key == "comment" and value == "—":
        value = ""
    service.answer(delivery.offer_id, {delivery.prompt_key: value}, user_id=user_id, version=delivery.version)
    return True


async def process_private(message, bot):
    if message.chat.type != "private" or not message.from_user:
        return False
    try:
        if message.text and message.reply_to_message:
            found = await sync_to_async(reply_to_question)(message.from_user.id, message.reply_to_message.message_id, message.text)
            if found:
                return True
        file = message.photo[-1] if message.photo else None
        if not file and message.document and (message.document.mime_type or "").startswith("image/"):
            file = message.document
        if not file:
            return False
        await sync_to_async(service.authorize_sender)(message.from_user.id)
        if file.file_size and file.file_size > settings.OFFER_UPLOAD_BYTES:
            raise ValidationError("Фотография должна быть не больше 10 МБ.")
        # aiogram's download is bounded by Telegram file metadata and checked again before decoding.
        info = await bot.get_file(file.file_id)
        if info.file_size and info.file_size > settings.OFFER_UPLOAD_BYTES:
            raise ValidationError("Фотография должна быть не больше 10 МБ.")
        content = io.BytesIO()
        await bot.download_file(info.file_path, destination=content, timeout=30)
        path, digest = await sync_to_async(store_image)(content.getvalue())
        key = f"telegram:{message.chat.id}:" + (f"album:{message.media_group_id}" if message.media_group_id else f"message:{message.message_id}")
        try:
            await sync_to_async(service.receive_image)(message.from_user.id, message.from_user.full_name[:160], key,
                message.message_id, path, digest, file.file_id, message.caption or "")
        except Exception:
            await sync_to_async(remove_image)(path)
            raise
        return True
    except (ValidationError, ValueError, ObjectDoesNotExist) as error:
        await message.reply(error_text(error))
        return True
    except Exception:
        await message.reply("Не удалось принять изображение. Повторите отправку; повтор одного сообщения не создаёт новую заявку.")
        return True
