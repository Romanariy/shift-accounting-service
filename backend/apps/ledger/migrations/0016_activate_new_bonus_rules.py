from django.db import migrations


def activate_new_rules(apps, schema_editor):
    Rule = apps.get_model("ledger", "BonusRule")
    for rule in Rule.objects.using(schema_editor.connection.alias).filter(applied={}):
        rule.applied = {"name": rule.name, "organization": rule.organization_id,
            "clients": list(rule.clients.order_by("id").values_list("id", flat=True)),
            "amount": str(rule.amount), "start": str(rule.start),
            "end": str(rule.end) if rule.end else None, "active": rule.active, "revision": rule.revision}
        rule.save(update_fields=("applied",))


class Migration(migrations.Migration):
    dependencies = [("ledger", "0015_client_charge_revision")]
    # Activate previously uninitialised configurations, without altering work or invoices.
    operations = [migrations.RunPython(activate_new_rules, migrations.RunPython.noop)]
