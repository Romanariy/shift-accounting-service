"""Organization invoice acknowledgments and durable Telegram keyboard cleanup."""
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.shifts.audit import log_change
from .engine import lock
from .models import Delivery, Invoice, PaymentMessageUpdate


def payment_status(invoice):
    return {
        "payment_state": invoice.payment_state,
        "payment_recipients": invoice.payment_recipients,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "paid_by": invoice.paid_by,
    }


def payment_keyboard(invoice, recipient):
    if invoice.payment_state != "open" or recipient not in invoice.payment_recipients:
        return None
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Оплатил", callback_data=f"ledger:paid:{invoice.pk}:{invoice.version}")
    ]])


def queue_keyboard_removal(invoice):
    """Called under the ledger lock; pending deliveries are reconciled by finish()."""
    for delivery in Delivery.objects.filter(invoice=invoice, purpose="owner", state="sent",
                                            recipient__in=invoice.payment_recipients).exclude(message_id=None):
        PaymentMessageUpdate.objects.get_or_create(
            recipient=delivery.recipient, message_id=delivery.message_id,
            defaults={"invoice": invoice},
        )


@transaction.atomic
def mark_paid(invoice_id, telegram_user, *, version=None, message_id=None, chat_id=None):
    lock()
    invoice = Invoice.objects.select_for_update().select_related("batch").get(pk=invoice_id)
    if telegram_user not in invoice.payment_recipients:
        raise ValidationError("Подтверждение оплаты доступно только назначенному получателю этого счёта.")
    if version is not None and invoice.version != int(version):
        raise ValidationError("Кнопка относится к другой версии счёта.")
    if chat_id is not None and chat_id != telegram_user:
        raise ValidationError("Подтвердите оплату в личном сообщении со счётом.")
    delivered = Delivery.objects.filter(invoice=invoice, purpose="owner", recipient=telegram_user, state="sent")
    if message_id is not None:
        delivered = delivered.filter(message_id=message_id)
    if not delivered.exists():
        raise ValidationError("Доставка счёта ещё не подтверждена. Повторите через несколько секунд.")
    if invoice.batch.state != "approved" or invoice.payment_state in ("none", "superseded"):
        raise ValidationError("Этот счёт не ожидает оплаты или заменён новой версией.")
    # A paid ancestor remains historical, but its obsolete buttons cannot mutate a new invoice.
    if Invoice.objects.filter(replaces=invoice, batch__state="approved").exists():
        raise ValidationError("Этот счёт заменён новой версией. Откройте новый счёт.")
    already_paid = invoice.payment_state == "paid"
    if not already_paid:
        before = payment_status(invoice)
        invoice.payment_state, invoice.paid_at, invoice.paid_by = "paid", timezone.now(), telegram_user
        invoice.save(update_fields=("payment_state", "paid_at", "paid_by"))
        log_change("invoice", invoice.pk, "paid", actor=f"telegram:{telegram_user}", before=before, after=payment_status(invoice))
    queue_keyboard_removal(invoice)
    return {**payment_status(invoice), "invoice_id": invoice.pk, "already_paid": already_paid}


def supersede_payment(invoice):
    if invoice.payment_state == "open":
        before = payment_status(invoice)
        invoice.payment_state = "superseded"
        invoice.save(update_fields=("payment_state",))
        log_change("invoice", invoice.pk, "payment_superseded", actor="system", before=before, after=payment_status(invoice))
    queue_keyboard_removal(invoice)


@transaction.atomic
def claim_message_update():
    lock()
    now = timezone.now()
    # Unlike document sending, replaying a keyboard edit cannot duplicate a bill.
    PaymentMessageUpdate.objects.filter(state="sending", started_at__lt=now - timedelta(minutes=5)).update(
        state="pending", retry_at=now, error="Повтор обновления кнопки после перезапуска.")
    item = PaymentMessageUpdate.objects.filter(state="pending").filter(
        Q(retry_at__isnull=True) | Q(retry_at__lte=now)).order_by("id").first()
    if item:
        item.state, item.started_at, item.attempts = "sending", now, item.attempts + 1
        item.save()
    return item


def finish_message_update(item, *, error="", retry_after=None, failed=False):
    now = timezone.now()
    PaymentMessageUpdate.objects.filter(pk=item.pk, state="sending", attempts=item.attempts).update(
        state="pending" if retry_after is not None else "failed" if failed else "sent",
        retry_at=now + timedelta(seconds=retry_after) if retry_after is not None else None,
        error=error[:2000], updated_at=now,
    )


async def update_message_once(bot):
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError
    item = await sync_to_async(claim_message_update)()
    if not item:
        return False
    try:
        await bot.edit_message_reply_markup(chat_id=item.recipient, message_id=item.message_id, reply_markup=None)
        await sync_to_async(finish_message_update)(item)
    except TelegramRetryAfter as error:
        await sync_to_async(finish_message_update)(item, error=str(error), retry_after=max(1, error.retry_after))
    except TelegramBadRequest as error:
        message = str(error).casefold()
        # Repeating an accepted edit or editing a deleted message is already terminal.
        if "message is not modified" in message or "message to edit not found" in message:
            await sync_to_async(finish_message_update)(item)
        else:
            await sync_to_async(finish_message_update)(item, error=str(error), failed=True)
    except TelegramForbiddenError as error:
        await sync_to_async(finish_message_update)(item, error=str(error), failed=True)
    except TelegramUnauthorizedError as error:
        await sync_to_async(finish_message_update)(item, error=str(error), retry_after=300)
    except Exception:
        await sync_to_async(finish_message_update)(item, error="Не удалось обновить кнопку в Telegram. Повторим автоматически.",
                                                   retry_after=min(3600, 30 * 2 ** min(item.attempts - 1, 7)))
    return True
