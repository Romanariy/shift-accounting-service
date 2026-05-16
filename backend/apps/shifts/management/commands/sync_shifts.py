import time

from django.core.management.base import BaseCommand

from apps.shifts.sync import try_sync_once


class Command(BaseCommand):
    help = "Передает локальные изменения смен в глобальную БД."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Запустить постоянный цикл.")
        parser.add_argument("--interval", type=int, default=3600, help="Интервал в секундах.")
        parser.add_argument("--limit", type=int, default=50, help="Сколько записей отправлять за попытку.")

    def handle(self, *args, **options):
        while True:
            result = try_sync_once(limit=options["limit"])
            self.stdout.write(self.style.SUCCESS(f"Sync result: {result}"))

            if not options["loop"]:
                break

            time.sleep(options["interval"])

