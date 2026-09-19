from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils import timezone
from .models import Employee


def team_data():
    from apps.ledger.models import TelegramContact
    contacts = {c.user_id: c for c in TelegramContact.objects.all()}
    conflicts = set(Employee.objects.filter(is_active=True, telegram_user_id__isnull=False)
        .values("telegram_user_id").annotate(count=Count("id")).filter(count__gt=1)
        .values_list("telegram_user_id", flat=True))
    result = []
    for employee in Employee.objects.all():
        contact = contacts.get(employee.telegram_user_id)
        result.append({"id": employee.pk, "name": employee.display_name, "active": employee.is_active,
            "telegram_user_id": employee.telegram_user_id,
            "telegram_contact": {"name": contact.name, "username": contact.username, "user_id": contact.user_id} if contact else None,
            "telegram_conflict": employee.telegram_user_id in conflicts})
    return result


def validate_binding(employee, previous):
    from apps.ledger.models import TelegramContact
    from apps.offers.models import ShiftOffer
    from apps.offers.service import end_at
    changed = employee.telegram_user_id != (previous.telegram_user_id if previous else None)
    if changed and previous:
        if any(end_at(offer) is None or end_at(offer) > timezone.now()
               for offer in ShiftOffer.objects.filter(employee=previous, state="claimed")):
            raise ValidationError("Сначала снимите сотрудника со взятой смены, затем измените привязку Telegram.")
    user_id = employee.telegram_user_id
    if user_id is None:
        return
    if user_id <= 0:
        raise ValidationError("Укажите положительный Telegram ID пользователя.")
    contact = TelegramContact.objects.filter(user_id=user_id).first()
    if changed and not contact:
        raise ValidationError("Пользователь должен сначала написать боту /start. Выберите его из подключённых контактов.")
    if employee.is_active and (changed or not previous or not previous.is_active):
        if Employee.objects.filter(is_active=True, telegram_user_id=user_id).exclude(pk=employee.pk).exists():
            raise ValidationError("Этот Telegram ID уже привязан к другому активному сотруднику.")
    if changed and contact:
        employee.telegram_username = contact.username or ""
