from django.db import migrations, models
import django.db.models.deletion


def copy_employee(apps, schema_editor):
    Service = apps.get_model("ledger", "Service")
    Accrual = apps.get_model("ledger", "Accrual")
    alias = schema_editor.connection.alias
    for service in Service.objects.using(alias).exclude(default_employee=None).iterator():
        Accrual.objects.using(alias).filter(service_id=service.pk).update(employee_id=service.default_employee_id)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0005_service_default_employee")]
    operations = [
        migrations.AddField(
            model_name="accrual", name="employee",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="shifts.employee"),
        ),
        migrations.RunPython(copy_employee),
        migrations.RemoveField(model_name="service", name="default_employee"),
    ]
