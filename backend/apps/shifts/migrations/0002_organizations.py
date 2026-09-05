from datetime import date
from django.db import migrations, models
import django.db.models.deletion


WORK_TYPES = [("big_admin", "Большой админ"), ("small_admin", "Малый админ"),
              ("cyclorama_painting", "Покраска циклораммы"), ("cleaning", "Уборка")]
PAY_CODES = WORK_TYPES + [("companion", "Сопровождение"),
                         ("phone_with_big_admin", "Телефоны при большом админе"),
                         ("phone_without_big_admin", "Телефоны без большого админа")]


def migrate_data(apps, schema_editor):
    db = schema_editor.connection.alias
    Organization = apps.get_model("shifts", "Organization")
    PayRule = apps.get_model("shifts", "PayRule")
    AuditLog = apps.get_model("shifts", "AuditLog")
    fields = ("code", "title", "calculation_type", "hourly_rate", "fixed_amount",
              "min_amount", "max_amount", "active_from", "active_to", "is_active")
    seeds = {
        "big_admin": dict(calculation_type="fixed", fixed_amount="1400.00"),
        "small_admin": dict(calculation_type="hourly", hourly_rate="200.00", min_amount="600.00", max_amount="1200.00"),
        "cyclorama_painting": dict(calculation_type="fixed", fixed_amount="1000.00"),
        "cleaning": dict(calculation_type="fixed", fixed_amount="700.00"),
    }
    for name, aliases, sheet, is_default in [
        ("Фокус", [], "Фокус и Фотобар", True),
        ("Фотобар", [], "Фокус и Фотобар", False),
        ("Квин", ["к"], "Квин", False),
    ]:
        org = Organization.objects.using(db).create(name=name, aliases=aliases, excel_sheet=sheet, is_default=is_default)
        for code, title in WORK_TYPES:
            rules = list(PayRule.objects.using(db).filter(code=code, organization=None).order_by("active_from", "updated_at", "pk").values(*fields))
            if not rules:
                rule = PayRule.objects.using(db).create(code=code, title=title, active_from=date(2026, 1, 1), **seeds[code])
                rules = [{field: getattr(rule, field) for field in fields}]
            for values in rules:
                PayRule.objects.using(db).create(organization=org, **values)

    for model_name, field, entity_type in [("ShiftEntry", "work_type", "shift"),
                                           ("Employee", "default_work_type", "employee")]:
        model = apps.get_model("shifts", model_name)
        for entry in model.objects.using(db).filter(**{field: "photobar"}):
            AuditLog.objects.using(db).create(
                entity_type=entity_type, entity_id=entry.pk, action="migrate", actor="migration:0002",
                before={field: "photobar"}, after={field: "small_admin"},
                diff={field: {"from": "photobar", "to": "small_admin"}},
            )
        model.objects.using(db).filter(**{field: "photobar"}).update(**{field: "small_admin"})
    for rule in PayRule.objects.using(db).filter(code="photobar"):
        values = {field: str(getattr(rule, field)) if getattr(rule, field) is not None else None for field in fields}
        AuditLog.objects.using(db).create(entity_type="pay_rule", entity_id=rule.pk, action="archive",
                                         actor="migration:0002", before=values, after={})
    PayRule.objects.using(db).filter(code="photobar").delete()


class Migration(migrations.Migration):
    dependencies = [("shifts", "0001_initial")]
    operations = [
        migrations.CreateModel(name="Organization", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField("Название", max_length=120, unique=True)),
            ("aliases", models.JSONField("Алиасы", default=list, blank=True)),
            ("excel_sheet", models.CharField("Лист Excel", max_length=31)),
            ("is_active", models.BooleanField("Активна", default=True)),
            ("is_default", models.BooleanField(default=False, editable=False)),
        ], options={"ordering": ("id",)}),
        *[migrations.AddField(model_name=model, name="organization", field=models.ForeignKey(
            to="shifts.organization", on_delete=django.db.models.deletion.PROTECT, null=True, blank=True,
        )) for model in ("payrule", "shiftentry", "companionentry")],
        migrations.RunPython(migrate_data),
        migrations.AlterField(model_name="employee", name="default_work_type", field=models.CharField(
            "Тип смены по умолчанию", max_length=40, choices=WORK_TYPES, default="small_admin")),
        migrations.AlterField(model_name="shiftentry", name="work_type", field=models.CharField(
            "Тип работы", max_length=40, choices=WORK_TYPES)),
        migrations.AlterField(model_name="payrule", name="code", field=models.CharField(
            "Код", max_length=48, choices=PAY_CODES)),
    ]
