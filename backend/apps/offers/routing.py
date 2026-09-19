"""Shared validation for the two settings APIs; callers hold the ledger lock."""
from django.core.exceptions import ValidationError
from .models import OfferConfig


def config_data(config=None):
    config = config or OfferConfig.objects.filter(pk=1).first()
    return {"enabled": config.enabled if config else False,
            "chat_id": config.chat_id if config else None, "thread_id": config.thread_id if config else None}


def update_config(config, payload):
    if not isinstance(payload, dict):
        raise ValidationError("Проверьте настройки публикации предложений.")
    for field in ("chat_id", "thread_id"):
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
    if offers.thread_id is not None and offers.thread_id < 1:
        raise ValidationError("ID топика предложений должен быть положительным.")
    if offers.enabled and (offers.chat_id is None or offers.thread_id is None or not ledger.approver_id):
        raise ValidationError("Нужны ID группы, ID топика предложений и главный утверждающий счетов.")
    if offers.chat_id is not None and offers.thread_id is not None:
        if any(chat == offers.chat_id and (thread is None or thread == offers.thread_id)
               for chat, thread in ((ledger.service_chat, ledger.service_thread),
                                    (ledger.expense_chat, ledger.expense_thread))):
            raise ValidationError("Топик предложений должен отличаться от топиков услуг и расходов.")
