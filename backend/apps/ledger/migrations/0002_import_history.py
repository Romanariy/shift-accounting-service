from datetime import timedelta
from decimal import Decimal

from django.db import migrations
from django.utils import timezone


def migrate_history(apps, schema_editor):
    Service = apps.get_model("ledger", "Service")
    Rate = apps.get_model("ledger", "Rate")
    Record = apps.get_model("ledger", "Record")
    Settings = apps.get_model("ledger", "Settings")
    Organization = apps.get_model("shifts", "Organization")
    PayRule = apps.get_model("shifts", "PayRule")
    Shift = apps.get_model("shifts", "ShiftEntry")
    Companion = apps.get_model("shifts", "CompanionEntry")
    services = {}
    for code, name, aliases, mode, frequency in [
        ("big_admin", "Большой админ", ["большой админ"], "time", "entry"),
        ("small_admin", "Малый админ", ["малый админ", "смена"], "time", "entry"),
        ("cleaning", "Уборка", ["уборка"], "mark", "entry"),
        ("cyclorama_painting", "Покраска циклорамы", ["покраска циклорамы", "покраска циклораммы"], "mark", "entry"),
        ("companion", "Сопровождение", ["сопр", "сопровождения", "сопровождений"], "quantity", "entry"),
        ("phone", "Телефоны", ["телефоны"], "mark", "daily"),
    ]:
        services[code] = Service.objects.create(name=name, aliases=aliases, input_type=mode, frequency=frequency, legacy_code=code)
    for org in Organization.objects.all():
        apps.get_model("ledger", "OrganizationBilling").objects.create(organization=org)
        for code, service in services.items():
            if code == "phone":
                continue
            rules = list(PayRule.objects.filter(code=code, organization_id=None if code == "companion" else org.pk, is_active=True).order_by("active_from", "updated_at", "pk"))
            # Flatten legacy precedence into disjoint intervals without changing stored entries.
            boundaries = sorted({r.active_from for r in rules} | {r.active_to + timedelta(days=1) for r in rules if r.active_to})
            for i, start in enumerate(boundaries):
                active = [r for r in rules if r.active_from <= start and (not r.active_to or r.active_to >= start)]
                if not active:
                    continue
                r = active[-1]
                end = boundaries[i + 1] - timedelta(days=1) if i + 1 < len(boundaries) else r.active_to
                Rate.objects.create(service=service, organization=org, calculation="quantity" if r.calculation_type == "per_unit" else r.calculation_type,
                                    price=r.hourly_rate if r.calculation_type == "hourly" else r.fixed_amount or 0,
                                    minimum=r.min_amount, maximum=r.max_amount, start=start, end=end)
    for kind, Model in (("shift", Shift), ("companion", Companion)):
        for entry in Model.objects.all().iterator():
            Record.objects.create(kind="service", service=services[entry.work_type if kind == "shift" else "companion"],
                organization_id=entry.organization_id, employee_id=entry.employee_id, employee_name=entry.employee_name_snapshot,
                date=entry.date, description=entry.comment, units=entry.hours if kind == "shift" else entry.count,
                start_time=getattr(entry, "start_time", None), end_time=getattr(entry, "end_time", None),
                amount=entry.calculated_amount, review=entry.status != "confirmed", source=entry.source,
                author_id=entry.telegram_author_user_id, author_name=entry.telegram_author_username, raw_text=entry.raw_text,
                chat_id=entry.telegram_chat_id, message_id=entry.telegram_message_id,
                part=Record.objects.filter(chat_id=entry.telegram_chat_id, message_id=entry.telegram_message_id).count() if entry.telegram_message_id is not None else 0,
                legacy_kind=kind, legacy_id=entry.pk, deleted_at=entry.deleted_at)
    # Freeze the previously virtual global phone history; leave it unassigned for manual review.
    first = Shift.objects.order_by("date").first()
    if first:
        day = first.date.replace(day=1)
        today = timezone.localdate()
        while day < today:
            big = Shift.objects.filter(date=day, work_type="big_admin", deleted_at__isnull=True).exists()
            code = "phone_with_big_admin" if big else "phone_without_big_admin"
            candidates = [r for r in PayRule.objects.filter(code=code, organization=None, is_active=True, active_from__lte=day).order_by("active_from", "updated_at", "pk") if not r.active_to or r.active_to >= day]
            amount = candidates[-1].fixed_amount if candidates else Decimal(0)
            if amount:
                Record.objects.create(kind="service", service=services["phone"], date=day, amount=amount,
                    description="Архив общего расчёта телефонов", source="import", review=True, included=False,
                    auto_key=f"legacy-phone:{day}")
            day += timedelta(days=1)
    Settings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [migrations.RunPython(migrate_history, migrations.RunPython.noop)]
