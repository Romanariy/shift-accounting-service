from datetime import date
from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def seed_initial_data(apps, _schema_editor):
    Employee = apps.get_model("shifts", "Employee")
    PayRule = apps.get_model("shifts", "PayRule")

    employees = (
        {
            "short_name": "Рамис",
            "full_name": "Рамис",
            "telegram_username": "minca131",
            "default_work_type": "small_admin",
            "aliases": ["Рамис"],
            "sort_order": 10,
        },
        {
            "short_name": "Наташа",
            "full_name": "Наталья",
            "telegram_username": "Natali_eve725",
            "default_work_type": "big_admin",
            "aliases": ["Наташа", "Наталья", "Натали"],
            "sort_order": 20,
        },
        {
            "short_name": "Полина",
            "full_name": "Полина",
            "telegram_username": "polink_aaa",
            "default_work_type": "small_admin",
            "aliases": ["Полина"],
            "sort_order": 30,
        },
        {
            "short_name": "Ксюша",
            "full_name": "Ксюша",
            "telegram_username": "queenisneverlate",
            "default_work_type": "small_admin",
            "aliases": ["Ксюша", "Ксения"],
            "sort_order": 40,
        },
        {
            "short_name": "Рома",
            "full_name": "Рома",
            "telegram_username": "",
            "default_work_type": "small_admin",
            "aliases": ["Рома", "Роман"],
            "sort_order": 50,
        },
    )

    for payload in employees:
        Employee.objects.update_or_create(
            short_name=payload["short_name"],
            defaults=payload,
        )

    rules = (
        ("big_admin", "Большой админ", "fixed", None, Decimal("1400.00"), None, None),
        ("small_admin", "Малый админ", "hourly", Decimal("200.00"), None, Decimal("600.00"), Decimal("1200.00")),
        ("photobar", "Админ Фотобар", "hourly", Decimal("200.00"), None, Decimal("600.00"), Decimal("1200.00")),
        ("cyclorama_painting", "Покраска циклораммы", "fixed", None, Decimal("1000.00"), None, None),
        ("cleaning", "Уборка", "fixed", None, Decimal("700.00"), None, None),
        ("companion", "Сопровождение", "per_unit", None, Decimal("500.00"), None, None),
        ("phone_with_big_admin", "Телефоны при большом админе", "fixed", None, Decimal("200.00"), None, None),
        ("phone_without_big_admin", "Телефоны без большого админа", "fixed", None, Decimal("400.00"), None, None),
    )

    for code, title, calculation_type, hourly_rate, fixed_amount, min_amount, max_amount in rules:
        PayRule.objects.update_or_create(
            code=code,
            active_from=date(2026, 1, 1),
            defaults={
                "title": title,
                "calculation_type": calculation_type,
                "hourly_rate": hourly_rate,
                "fixed_amount": fixed_amount,
                "min_amount": min_amount,
                "max_amount": max_amount,
                "active_to": None,
                "is_active": True,
            },
        )


def unseed_initial_data(apps, _schema_editor):
    Employee = apps.get_model("shifts", "Employee")
    PayRule = apps.get_model("shifts", "PayRule")
    Employee.objects.filter(short_name__in=["Рамис", "Наташа", "Полина", "Ксюша", "Рома"]).delete()
    PayRule.objects.filter(active_from=date(2026, 1, 1)).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Employee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("short_name", models.CharField(max_length=80, unique=True, verbose_name="Короткое имя")),
                ("full_name", models.CharField(blank=True, max_length=160, verbose_name="Полное имя")),
                ("telegram_username", models.CharField(blank=True, max_length=120, verbose_name="Telegram username")),
                ("telegram_user_id", models.BigIntegerField(blank=True, null=True, verbose_name="Telegram user id")),
                ("aliases", models.JSONField(blank=True, default=list, verbose_name="Алиасы для парсера")),
                (
                    "default_work_type",
                    models.CharField(
                        choices=[
                            ("big_admin", "Большой админ"),
                            ("small_admin", "Малый админ"),
                            ("photobar", "Админ Фотобар"),
                            ("cyclorama_painting", "Покраска циклораммы"),
                            ("cleaning", "Уборка"),
                        ],
                        default="small_admin",
                        max_length=40,
                        verbose_name="Тип смены по умолчанию",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                ("sort_order", models.PositiveIntegerField(default=100, verbose_name="Порядок")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Сотрудник",
                "verbose_name_plural": "Сотрудники",
                "ordering": ("sort_order", "short_name"),
            },
        ),
        migrations.CreateModel(
            name="PayRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "code",
                    models.CharField(
                        choices=[
                            ("big_admin", "Большой админ"),
                            ("small_admin", "Малый админ"),
                            ("photobar", "Админ Фотобар"),
                            ("cyclorama_painting", "Покраска циклораммы"),
                            ("cleaning", "Уборка"),
                            ("companion", "Сопровождение"),
                            ("phone_with_big_admin", "Телефоны при большом админе"),
                            ("phone_without_big_admin", "Телефоны без большого админа"),
                        ],
                        max_length=48,
                        verbose_name="Код",
                    ),
                ),
                ("title", models.CharField(max_length=160, verbose_name="Название")),
                (
                    "calculation_type",
                    models.CharField(
                        choices=[("fixed", "Фиксированная"), ("hourly", "Почасовая"), ("per_unit", "За штуку")],
                        default="fixed",
                        max_length=20,
                        verbose_name="Тип расчета",
                    ),
                ),
                ("hourly_rate", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Ставка в час")),
                ("fixed_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Фиксированная сумма")),
                ("min_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Минимальная сумма")),
                ("max_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Максимальная сумма")),
                ("active_from", models.DateField(verbose_name="Действует с")),
                ("active_to", models.DateField(blank=True, null=True, verbose_name="Действует до")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активно")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Правило оплаты",
                "verbose_name_plural": "Правила оплаты",
                "ordering": ("code", "-active_from"),
            },
        ),
        migrations.CreateModel(
            name="TelegramSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160, verbose_name="Название")),
                ("chat_id", models.BigIntegerField(verbose_name="Chat ID")),
                ("thread_id", models.BigIntegerField(blank=True, null=True, verbose_name="Topic/thread ID")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Источник Telegram",
                "verbose_name_plural": "Источники Telegram",
                "unique_together": {("chat_id", "thread_id")},
            },
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity_type", models.CharField(max_length=60, verbose_name="Тип сущности")),
                ("entity_id", models.PositiveIntegerField(verbose_name="ID сущности")),
                ("action", models.CharField(max_length=40, verbose_name="Действие")),
                ("actor", models.CharField(blank=True, max_length=160, verbose_name="Автор")),
                ("diff", models.JSONField(blank=True, default=dict, verbose_name="Изменения")),
                ("before", models.JSONField(blank=True, default=dict, verbose_name="Было")),
                ("after", models.JSONField(blank=True, default=dict, verbose_name="Стало")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
            ],
            options={
                "verbose_name": "История изменений",
                "verbose_name_plural": "История изменений",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="SyncOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity_type", models.CharField(max_length=60, verbose_name="Тип сущности")),
                ("entity_id", models.PositiveIntegerField(verbose_name="ID сущности")),
                ("action", models.CharField(max_length=40, verbose_name="Действие")),
                ("payload", models.JSONField(default=dict, verbose_name="Данные")),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Ожидает"), ("synced", "Передано"), ("failed", "Ошибка")],
                        default="pending",
                        max_length=20,
                        verbose_name="Статус",
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0, verbose_name="Попытки")),
                ("last_error", models.TextField(blank=True, verbose_name="Последняя ошибка")),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True, verbose_name="Последняя попытка")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Очередь синхронизации",
                "verbose_name_plural": "Очередь синхронизации",
                "ordering": ("created_at",),
            },
        ),
        migrations.CreateModel(
            name="ShiftEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(verbose_name="Дата")),
                ("employee_name_snapshot", models.CharField(blank=True, max_length=120, verbose_name="Имя сотрудника")),
                ("comment", models.TextField(blank=True, verbose_name="Комментарий")),
                ("calculated_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Сумма")),
                (
                    "source",
                    models.CharField(
                        choices=[("telegram", "Telegram"), ("manual", "Вручную"), ("import", "Импорт")],
                        default="manual",
                        max_length=20,
                        verbose_name="Источник",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("confirmed", "Подтверждено"), ("needs_review", "Нужно проверить")],
                        default="confirmed",
                        max_length=24,
                        verbose_name="Статус",
                    ),
                ),
                ("telegram_chat_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_thread_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_message_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_author_username", models.CharField(blank=True, max_length=120)),
                ("telegram_author_user_id", models.BigIntegerField(blank=True, null=True)),
                ("raw_text", models.TextField(blank=True, verbose_name="Исходное сообщение")),
                (
                    "sync_status",
                    models.CharField(
                        choices=[("pending", "Ожидает"), ("synced", "Передано"), ("failed", "Ошибка")],
                        default="pending",
                        max_length=20,
                        verbose_name="Синхронизация",
                    ),
                ),
                ("sync_error", models.TextField(blank=True, verbose_name="Ошибка синхронизации")),
                ("remote_id", models.CharField(blank=True, max_length=120, verbose_name="ID в глобальной БД")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Удалено")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                (
                    "work_type",
                    models.CharField(
                        choices=[
                            ("big_admin", "Большой админ"),
                            ("small_admin", "Малый админ"),
                            ("photobar", "Админ Фотобар"),
                            ("cyclorama_painting", "Покраска циклораммы"),
                            ("cleaning", "Уборка"),
                        ],
                        max_length=40,
                        verbose_name="Тип работы",
                    ),
                ),
                ("start_time", models.TimeField(blank=True, null=True, verbose_name="Начало")),
                ("end_time", models.TimeField(blank=True, null=True, verbose_name="Конец")),
                ("hours", models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name="Кол-во часов")),
                (
                    "employee",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="shifts.employee", verbose_name="Сотрудник"),
                ),
            ],
            options={
                "verbose_name": "Смена",
                "verbose_name_plural": "Смены",
                "ordering": ("-date", "-created_at"),
            },
        ),
        migrations.CreateModel(
            name="CompanionEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(verbose_name="Дата")),
                ("employee_name_snapshot", models.CharField(blank=True, max_length=120, verbose_name="Имя сотрудника")),
                ("comment", models.TextField(blank=True, verbose_name="Комментарий")),
                ("calculated_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Сумма")),
                (
                    "source",
                    models.CharField(
                        choices=[("telegram", "Telegram"), ("manual", "Вручную"), ("import", "Импорт")],
                        default="manual",
                        max_length=20,
                        verbose_name="Источник",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("confirmed", "Подтверждено"), ("needs_review", "Нужно проверить")],
                        default="confirmed",
                        max_length=24,
                        verbose_name="Статус",
                    ),
                ),
                ("telegram_chat_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_thread_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_message_id", models.BigIntegerField(blank=True, null=True)),
                ("telegram_author_username", models.CharField(blank=True, max_length=120)),
                ("telegram_author_user_id", models.BigIntegerField(blank=True, null=True)),
                ("raw_text", models.TextField(blank=True, verbose_name="Исходное сообщение")),
                (
                    "sync_status",
                    models.CharField(
                        choices=[("pending", "Ожидает"), ("synced", "Передано"), ("failed", "Ошибка")],
                        default="pending",
                        max_length=20,
                        verbose_name="Синхронизация",
                    ),
                ),
                ("sync_error", models.TextField(blank=True, verbose_name="Ошибка синхронизации")),
                ("remote_id", models.CharField(blank=True, max_length=120, verbose_name="ID в глобальной БД")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Удалено")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("count", models.PositiveIntegerField(default=1, verbose_name="Кол-во сопровождений")),
                (
                    "employee",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="shifts.employee", verbose_name="Сотрудник"),
                ),
            ],
            options={
                "verbose_name": "Сопровождение",
                "verbose_name_plural": "Сопровождения",
                "ordering": ("-date", "-created_at"),
            },
        ),
        migrations.AddIndex(
            model_name="payrule",
            index=models.Index(fields=["code", "is_active", "active_from"], name="shifts_payr_code_605e4d_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["entity_type", "entity_id", "-created_at"], name="shifts_audi_entity__a699d1_idx"),
        ),
        migrations.AddIndex(
            model_name="syncoutbox",
            index=models.Index(fields=["status", "created_at"], name="shifts_sync_status_b390b4_idx"),
        ),
        migrations.AddIndex(
            model_name="shiftentry",
            index=models.Index(fields=["date", "work_type"], name="shifts_shif_date_3e95ad_idx"),
        ),
        migrations.AddIndex(
            model_name="shiftentry",
            index=models.Index(fields=["employee", "date"], name="shifts_shif_employe_2ce77c_idx"),
        ),
        migrations.AddIndex(
            model_name="shiftentry",
            index=models.Index(fields=["deleted_at", "date"], name="shifts_shif_deleted_d79110_idx"),
        ),
        migrations.AddIndex(
            model_name="companionentry",
            index=models.Index(fields=["employee", "date"], name="shifts_comp_employe_80272d_idx"),
        ),
        migrations.AddIndex(
            model_name="companionentry",
            index=models.Index(fields=["deleted_at", "date"], name="shifts_comp_deleted_26808e_idx"),
        ),
        migrations.RunPython(seed_initial_data, unseed_initial_data),
    ]
