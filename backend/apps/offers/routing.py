"""Shared validation for the two settings APIs; callers hold the ledger lock."""
from django.core.exceptions import ValidationError
from .models import OfferConfig


def config_data(config=None):
    config = config or OfferConfig.objects.filter(pk=1).first()
    return {"enabled": config.enabled if config else False,
            **{field: getattr(config, field) if config else None for field in
               ("chat_id", "thread_id", "claimed_thread_id", "released_thread_id")}}


def update_config(config, payload):
    if not isinstance(payload, dict):
        raise ValidationError("Проверьте настройки публикации предложений.")
    for field in ("chat_id", "thread_id", "claimed_thread_id", "released_thread_id"):
        if field in payload:
            setattr(config, field, int(payload[field]) if payload[field] not in (None, "") else None)
    if "enabled" in payload:
        if type(payload["enabled"]) is not bool:
            raise ValidationError("Включение предложений должно быть флажком.")
        config.enabled = payload["enabled"]


def validate_routes(ledger, offers):
    ledger.full_clean()
    if offers.chat_id is not None and offers.chat_id >= 0:
        raise ValidationError("Chat ID группы предложений должен быть отрицательным.")
    threads = [offers.thread_id, offers.claimed_thread_id, offers.released_thread_id]
    if any(thread is not None and thread < 1 for thread in threads):
        raise ValidationError("ID топиков предложений должны быть положительными.")
    if len([t for t in threads if t is not None]) != len({t for t in threads if t is not None}):
        raise ValidationError("Для активных, взятых смен и уведомлений нужны разные топики.")
    if offers.enabled and (offers.chat_id is None or any(t is None for t in threads) or not ledger.approver_id):
        raise ValidationError("Укажите группу, топики активных и взятых смен, топик освобождения и главного утверждающего счетов.")
    if offers.chat_id is not None:
        if any(chat == offers.chat_id and (thread is None or thread in threads)
               for chat, thread in ((ledger.service_chat, ledger.service_thread),
                                    (ledger.expense_chat, ledger.expense_thread))):
            raise ValidationError("Топики предложений должны отличаться от топиков услуг и расходов.")
