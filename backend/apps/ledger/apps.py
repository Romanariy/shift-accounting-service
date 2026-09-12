from django.apps import AppConfig


class LedgerConfig(AppConfig):
    name = "apps.ledger"
    verbose_name = "Услуги и счета"

    def ready(self):
        from . import bridge  # noqa: F401
