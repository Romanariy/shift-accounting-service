import asyncio
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from .engine import lock
from .models import Delivery, Invoice
from .payments import payment_keyboard, queue_keyboard_removal, update_message_once


@transaction.atomic
def claim():
    lock()
    # A worker crash after Telegram accepted a message cannot safely be retried automatically.
    Delivery.objects.filter(state="sending", started_at__lt=timezone.now() - timedelta(minutes=5)).update(
        state="unknown", error="Процесс прервался во время доставки. Проверьте Telegram перед повтором.")
    for item in Delivery.objects.filter(state="pending").select_related("batch", "invoice").order_by("id"):
        if item.purpose == "owner" and (item.batch.state != "approved" or not item.invoice_id or
                Invoice.objects.filter(replaces_id=item.invoice_id, batch__state="approved").exists()):
            item.state, item.error = "cancelled", "Счёт не утверждён или заменён новой версией."
            item.save()
            continue
        if item.purpose != "owner" and (item.version != item.batch.version or item.batch.state == "approved"):
            item.state = "cancelled"
            item.save()
            continue
        if item.purpose == "approval" and Delivery.objects.filter(batch=item.batch, version=item.version, purpose="preview").exclude(state="sent").exists():
            continue
        item.state, item.started_at, item.attempts = "sending", timezone.now(), item.attempts + 1
        item.save()
        return item
    return None


@transaction.atomic
def finish(pk, state, error="", message_id=None):
    lock()
    changed = Delivery.objects.filter(pk=pk, state="sending").update(state=state, error=error[:2000], message_id=message_id, updated_at=timezone.now())
    if changed and state == "sent":
        item = Delivery.objects.select_related("invoice").get(pk=pk)
        if item.purpose == "owner" and item.invoice.payment_state != "open":
            # Payment can arrive from the first recipient while another document is in flight.
            queue_keyboard_removal(item.invoice)


async def deliver_once(bot):
    from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError
    if await update_message_once(bot):
        return True
    item = await sync_to_async(claim)()
    if not item:
        return False
    try:
        if item.purpose in ("owner", "preview"):
            inv = item.invoice
            replacement = f"\nЗаменяет счёт №{inv.replaces_id}." if inv.replaces_id else ""
            caption = f"{'На проверку · ' if item.purpose == 'preview' else ''}{inv.organization_name}\n{item.batch.start:%d.%m.%Y} — {item.batch.end:%d.%m.%Y}\nСчёт №{inv.pk}, версия {inv.version}\nИтого: {inv.total:,.2f} ₽{replacement}"
            if item.purpose == "preview" and inv.errors:
                caption += "\nЕсть замечания. Исправьте их на сайте перед подтверждением."
            markup = payment_keyboard(inv, item.recipient) if item.purpose == "owner" else None
            if markup:
                caption += "\nПосле оплаты нажмите «Оплатил». Подтверждение закроет счёт для всех получателей организации."
            result = await bot.send_document(item.recipient, BufferedInputFile(bytes(inv.artifact), filename=f"invoice-{inv.pk}-v{inv.version}.xlsx"), caption=caption, reply_markup=markup)
        else:
            invoices = await sync_to_async(list)(item.batch.invoices.all())
            lines = [f"Пакет №{item.batch_id}, версия {item.version}", f"{item.batch.start:%d.%m.%Y} — {item.batch.end:%d.%m.%Y}"]
            for inv in invoices:
                lines.append(f"{inv.organization_name}: {inv.total:,.2f} ₽ → {', '.join(str(x) for x in inv.recipients) or 'получатель не назначен'}")
            lines.append("Проверьте все файлы. После подтверждения счета отправятся владельцам.")
            result = await bot.send_message(item.recipient, "\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Подтвердить отправку", callback_data=f"ledger:approve:{item.batch_id}:{item.version}")],
                [InlineKeyboardButton(text="Обновить отчёты", callback_data=f"ledger:refresh:{item.batch_id}:{item.version}")],
            ]))
        await sync_to_async(finish)(item.pk, "sent", message_id=result.message_id)
    except TelegramRetryAfter as error:
        # Explicit rejection: Telegram did not accept the request.
        await sync_to_async(finish)(item.pk, "failed", f"Лимит Telegram. Повторите через {error.retry_after} секунд.")
    except (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError) as error:
        await sync_to_async(finish)(item.pk, "failed", str(error))
    except Exception:
        await sync_to_async(finish)(item.pk, "unknown", "Telegram не подтвердил результат доставки. Проверьте чат перед повтором.")
    return True
