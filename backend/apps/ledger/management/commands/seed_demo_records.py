"""Populate the local working surface without sending or queueing anything externally."""
from datetime import time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ledger.engine import audit_record, calculate, lock
from apps.ledger.models import Record, Service
from apps.shifts.models import Employee, Organization
from apps.shifts.parser import calculate_hours


class Command(BaseCommand):
    help = "Добавляет помеченные тестовые записи текущего месяца в локальную SQLite-базу."

    @transaction.atomic
    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        expected = Path(settings.BASE_DIR) / "db.sqlite3"
        if database["ENGINE"] != "django.db.backends.sqlite3" or Path(database["NAME"]).resolve() != expected.resolve():
            raise CommandError("Команда предназначена только для локальной backend/db.sqlite3.")
        lock()
        organizations = list(Organization.objects.filter(is_active=True).order_by("id")[:3])
        employees = list(Employee.objects.filter(is_active=True).order_by("id"))
        services = {s.legacy_code: s for s in Service.objects.filter(active=True) if s.legacy_code}
        if not organizations or not employees or not {"small_admin", "big_admin", "cleaning", "cyclorama_painting", "companion", "phone"} <= services.keys():
            raise CommandError("Сначала нужны организации, сотрудники и исходные услуги.")
        today = timezone.localdate()
        created = skipped = 0

        def add(org, key, day, kind="service", service=None, employee=None, description="", amount=0, units=1, start=None, end=None):
            nonlocal created, skipped
            marker = f"demo:v1:{today:%Y-%m}:{org.pk}:{key}"
            if Record.objects.filter(source="demo", raw_text=marker).exists():
                skipped += 1
                return
            record = Record(
                kind=kind, service=service, organization=org, employee=employee,
                employee_name=employee.display_name if employee else ("Тестовый исполнитель — проверить" if kind == "oneoff" else ""),
                date=today.replace(day=min(day, today.day)), description="[ТЕСТ] " + description,
                amount=Decimal(str(amount)), units=Decimal(str(units)), start_time=start, end_time=end,
                source="demo", author_name="Демо-данные", raw_text=marker, included=True,
                review=kind == "oneoff" and employee is None,
            )
            try:
                record.amount = calculate(record)
            except ValidationError as error:
                record.error = " ".join(error.messages)
                record.review = True
            record.full_clean()
            record.save()
            audit_record(record, None, "demo-seed")
            created += 1

        intervals = [(10, 12), (10, 14), (9, 17), (22, 2)]
        for index, org in enumerate(organizations):
            for number, (begin, finish) in enumerate(intervals):
                start, end = time(begin), time(finish)
                employee = employees[(index + number) % len(employees)]
                add(org, f"shift-{number}", number + 1, service=services["big_admin" if number == 2 else "small_admin"],
                    employee=employee, description=["Короткая смена: минимум тарифа", "Дневная смена", "Полная смена большого администратора", "Ночная смена через полночь"][number],
                    units=calculate_hours(start, end), start=start, end=end)
            add(org, "cleaning", 5, service=services["cleaning"], employee=employees[index % len(employees)], description="Уборка студии после съёмки")
            add(org, "painting", 6, service=services["cyclorama_painting"], employee=employees[(index + 1) % len(employees)], description="Обновление циклорамы")
            add(org, "oneoff-repair", 4, kind="oneoff", employee=employees[(index + 2) % len(employees)], amount=1500 + index * 250, description="Починил дверь и заменил ручку")
            add(org, "oneoff-extra", 7, kind="oneoff", employee=None if index < 2 else employees[0], amount=850 + index * 100, description="Собрал стеллаж и закрепил полки")
            add(org, "expense-paint", 5, kind="expense", amount="1680.50", description="Краска, кисти и малярная лента")
            add(org, "expense-supplies", 7, kind="expense", amount=420 + index * 130, description="Чистящие средства и расходные материалы")

        org = organizations[0]
        for number, count in enumerate((1, 2, 3)):
            add(org, f"companion-{number}", 2 + number * 2, service=services["companion"],
                employee=employees[number % len(employees)], units=count, description=f"Сопровождение съёмок: {count}")
        add(org, "phone-review", 7, service=services["phone"], description="Телефоны: пример записи для проверки тарифа")
        self.stdout.write(self.style.SUCCESS(f"Создано: {created}; уже существовало: {skipped}. Все записи помечены [ТЕСТ]; перед рабочей рассылкой удалите их из журнала."))
