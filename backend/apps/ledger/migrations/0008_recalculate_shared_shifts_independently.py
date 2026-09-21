from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


def money(value):
    return Decimal(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def recalculate(apps, schema_editor):
    Record = apps.get_model("ledger", "Record")
    DailyCalculation = apps.get_model("ledger", "DailyCalculation")
    ShiftEntry = apps.get_model("shifts", "ShiftEntry")
    CompanionEntry = apps.get_model("shifts", "CompanionEntry")
    AuditLog = apps.get_model("shifts", "AuditLog")

    # Recalculate every record that still points at a shared snapshot. An inactive
    # snapshot may still have linked rows after an interrupted or older update.
    group_ids = list(Record.objects.exclude(daily_calculation_id=None).values_list(
        "daily_calculation_id", flat=True
    ).distinct())
    for group in DailyCalculation.objects.filter(pk__in=group_ids).iterator():
        records = list(Record.objects.filter(daily_calculation_id=group.pk, deleted_at=None).iterator())
        for record in records:
            before = str(record.amount)
            amount = Decimal(record.units) * Decimal(group.price)
            if group.minimum is not None and amount > 0:
                amount = max(amount, Decimal(group.minimum))
            if group.maximum is not None:
                amount = min(amount, Decimal(group.maximum))
            amount = money(amount)
            record.amount = amount
            record.daily_calculation_id = None
            record.calculation_version = 1
            record.error = ""
            record.save(update_fields=("amount", "daily_calculation", "calculation_version", "error", "updated_at"))

            if record.legacy_id:
                Model = CompanionEntry if record.legacy_kind == "companion" else ShiftEntry
                Model.objects.filter(pk=record.legacy_id).update(calculated_amount=amount)
            if before != str(amount):
                AuditLog.objects.create(
                    entity_type="record", entity_id=record.pk, action="update",
                    actor="migration:independent-shifts",
                    before={"amount": before, "daily_calculation": group.pk},
                    after={"amount": str(amount), "daily_calculation": None},
                    diff={
                        "amount": {"from": before, "to": str(amount)},
                        "daily_calculation": {"from": group.pk, "to": None},
                    },
                )
        # Deleted records are not recalculated, but must not retain an active group link.
        Record.objects.filter(daily_calculation_id=group.pk).update(daily_calculation_id=None)
        group.active = False
        group.save(update_fields=("active",))


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0007_invoice_payment_confirmation"),
    ]

    operations = [
        migrations.RunPython(recalculate, migrations.RunPython.noop),
    ]
