"""Keep legacy writes inside the same shared-shift transaction and tariff rules."""
from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.shifts.models import ShiftEntry, CompanionEntry


def ready():
    return "ledger_dailycalculation" in connection.introspection.table_names()


def enabled(instance):
    return not getattr(instance, "_ledger_bridge", False) and ready()


@receiver(pre_save, sender=ShiftEntry)
@receiver(pre_save, sender=CompanionEntry)
def lock_legacy_write(sender, instance, **kwargs):
    if enabled(instance):
        from .engine import lock
        lock()


@receiver(post_save, sender=ShiftEntry)
@receiver(post_save, sender=CompanionEntry)
def legacy_saved(sender, instance, **kwargs):
    if not enabled(instance):
        return
    update_fields = kwargs.get("update_fields")
    if update_fields and not set(update_fields) - {"sync_status", "updated_at"}:
        return
    from .models import Record, Service
    from .grouping import commit_records
    kind = "shift" if sender == ShiftEntry else "companion"
    code = instance.work_type if kind == "shift" else "companion"
    service = Service.objects.filter(legacy_code=code).first()
    if not service:
        return
    record = Record.objects.filter(legacy_kind=kind, legacy_id=instance.pk).first()
    if record is None:
        record = Record(kind="service", legacy_kind=kind, legacy_id=instance.pk, amount=instance.calculated_amount)
    record.service = service
    record.organization_id = instance.organization_id
    record.employee_id = instance.employee_id
    record.employee_name = instance.employee_name_snapshot
    record.date = instance.date
    record.description = instance.comment
    record.units = instance.hours if kind == "shift" else instance.count
    record.start_time = getattr(instance, "start_time", None)
    record.end_time = getattr(instance, "end_time", None)
    record.review = instance.status != "confirmed"
    record.deleted_at = instance.deleted_at
    record.source = instance.source
    record.author_id = instance.telegram_author_user_id
    record.author_name = instance.telegram_author_username
    record.raw_text = instance.raw_text
    if record.organization_id is None:
        # Pre-organization history has no daily group or organization invoice.
        from django.core.exceptions import ValidationError
        from .engine import is_frozen
        if record.daily_calculation_id or (record.pk and is_frozen(record)):
            raise ValidationError("Нельзя убрать организацию из учтённой смены.")
        record.review = True
        record.save()
        instance.calculated_amount = record.amount
        return
    commit_records([record], actor="legacy")
    # Old API serializers and their outbox must receive the allocated amount as well.
    instance.calculated_amount = record.amount
