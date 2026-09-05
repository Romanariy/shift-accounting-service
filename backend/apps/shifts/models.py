import re

from django.core.exceptions import ValidationError
from django.db import models

from .constants import (
    CALCULATION_TYPE_CHOICES,
    ENTRY_SOURCE_CHOICES,
    ENTRY_STATUS_CHOICES,
    PAY_CODE_CHOICES,
    SYNC_STATUS_CHOICES,
    WORK_TYPE_CHOICES,
    CalculationType,
    EntrySource,
    EntryStatus,
    PayCode,
    SyncStatus,
    WorkType,
)


class Employee(models.Model):
    short_name = models.CharField("Короткое имя", max_length=80, unique=True)
    full_name = models.CharField("Полное имя", max_length=160, blank=True)
    telegram_username = models.CharField("Telegram username", max_length=120, blank=True)
    telegram_user_id = models.BigIntegerField("Telegram user id", null=True, blank=True)
    aliases = models.JSONField("Алиасы для парсера", default=list, blank=True)
    default_work_type = models.CharField(
        "Тип смены по умолчанию",
        max_length=40,
        choices=WORK_TYPE_CHOICES,
        default=WorkType.SMALL_ADMIN,
    )
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=100)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ("sort_order", "short_name")

    def __str__(self):
        username = f" @{self.telegram_username}" if self.telegram_username else ""
        return f"{self.short_name}{username}"

    @property
    def display_name(self):
        return self.short_name or self.full_name


class Organization(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    aliases = models.JSONField("Алиасы", default=list, blank=True)
    excel_sheet = models.CharField("Лист Excel", max_length=31)
    is_active = models.BooleanField("Активна", default=True)
    is_default = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return self.name

    def clean(self):
        self.name = " ".join(self.name.split()).rstrip(".")
        self.excel_sheet = self.excel_sheet.strip()
        if not isinstance(self.aliases, list) or any(not isinstance(a, str) for a in self.aliases):
            raise ValidationError("Алиасы должны быть списком строк.")
        self.aliases = list(dict.fromkeys(" ".join(a.split()).rstrip(".").casefold() for a in self.aliases if a.strip()))
        tokens = {a.casefold() for a in [self.name, *self.aliases]}
        if not self.name or "" in tokens:
            raise ValidationError("Название и алиасы не могут быть пустыми.")
        for other in Organization.objects.exclude(pk=self.pk):
            if tokens & {a.casefold() for a in [other.name, *other.aliases]}:
                raise ValidationError(f"Название или алиас уже используется: {other.name}.")
        sheet = self.excel_sheet
        if (not sheet or len(sheet) > 31 or re.search(r"[\\/*?:\[\]\x00-\x1f]", sheet)
                or sheet.startswith("'") or sheet.endswith("'")
                or sheet.casefold() in {"сопровождения", "телефоны", "без организации", "history"}):
            raise ValidationError("Недопустимое или зарезервированное название листа Excel.")
        existing = next((org for org in Organization.objects.exclude(pk=self.pk)
                         if org.excel_sheet.casefold() == sheet.casefold()), None)
        if existing:
            self.excel_sheet = existing.excel_sheet


class PayRule(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, null=True, blank=True)
    code = models.CharField("Код", max_length=48, choices=PAY_CODE_CHOICES)
    title = models.CharField("Название", max_length=160)
    calculation_type = models.CharField(
        "Тип расчета",
        max_length=20,
        choices=CALCULATION_TYPE_CHOICES,
        default=CalculationType.FIXED,
    )
    hourly_rate = models.DecimalField(
        "Ставка в час", max_digits=10, decimal_places=2, null=True, blank=True
    )
    fixed_amount = models.DecimalField(
        "Фиксированная сумма", max_digits=10, decimal_places=2, null=True, blank=True
    )
    min_amount = models.DecimalField(
        "Минимальная сумма", max_digits=10, decimal_places=2, null=True, blank=True
    )
    max_amount = models.DecimalField(
        "Максимальная сумма", max_digits=10, decimal_places=2, null=True, blank=True
    )
    active_from = models.DateField("Действует с")
    active_to = models.DateField("Действует до", null=True, blank=True)
    is_active = models.BooleanField("Активно", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Правило оплаты"
        verbose_name_plural = "Правила оплаты"
        ordering = ("code", "-active_from")
        indexes = [
            models.Index(fields=("code", "is_active", "active_from"), name="shifts_payr_code_605e4d_idx"),
        ]

    def __str__(self):
        return f"{self.title} с {self.active_from:%d.%m.%Y}"

    def clean(self):
        shift_codes = dict(WORK_TYPE_CHOICES)
        if self.code in shift_codes and not self.organization_id and not self.pk:
            raise ValidationError("Для нового тарифа смены укажите организацию.")
        if self.code not in shift_codes and self.organization_id:
            raise ValidationError("Сопровождения и телефоны используют общие тарифы.")
        if self.active_to and self.active_to < self.active_from:
            raise ValidationError("Конец действия тарифа не может быть раньше начала.")
        for field in ("hourly_rate", "fixed_amount", "min_amount", "max_amount"):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValidationError("Ставки и ограничения не могут быть отрицательными.")
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValidationError("Минимальная сумма не может превышать максимальную.")
        if self.calculation_type == CalculationType.HOURLY and self.hourly_rate is None:
            raise ValidationError("Укажите почасовую ставку.")
        if self.calculation_type != CalculationType.HOURLY and self.fixed_amount is None:
            raise ValidationError("Укажите фиксированную сумму.")


class TelegramSource(models.Model):
    title = models.CharField("Название", max_length=160)
    chat_id = models.BigIntegerField("Chat ID")
    thread_id = models.BigIntegerField("Topic/thread ID", null=True, blank=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Источник Telegram"
        verbose_name_plural = "Источники Telegram"
        unique_together = ("chat_id", "thread_id")

    def __str__(self):
        topic = f":{self.thread_id}" if self.thread_id is not None else ""
        return f"{self.title} ({self.chat_id}{topic})"


class EntryBase(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, null=True, blank=True)
    date = models.DateField("Дата")
    employee = models.ForeignKey(
        Employee,
        verbose_name="Сотрудник",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    employee_name_snapshot = models.CharField("Имя сотрудника", max_length=120, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    calculated_amount = models.DecimalField(
        "Сумма", max_digits=10, decimal_places=2, default=0
    )
    source = models.CharField(
        "Источник", max_length=20, choices=ENTRY_SOURCE_CHOICES, default=EntrySource.MANUAL
    )
    status = models.CharField(
        "Статус", max_length=24, choices=ENTRY_STATUS_CHOICES, default=EntryStatus.CONFIRMED
    )
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    telegram_thread_id = models.BigIntegerField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_author_username = models.CharField(max_length=120, blank=True)
    telegram_author_user_id = models.BigIntegerField(null=True, blank=True)
    raw_text = models.TextField("Исходное сообщение", blank=True)
    sync_status = models.CharField(
        "Синхронизация", max_length=20, choices=SYNC_STATUS_CHOICES, default=SyncStatus.PENDING
    )
    sync_error = models.TextField("Ошибка синхронизации", blank=True)
    remote_id = models.CharField("ID в глобальной БД", max_length=120, blank=True)
    deleted_at = models.DateTimeField("Удалено", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        abstract = True

    def fill_employee_snapshot(self):
        if self.employee and not self.employee_name_snapshot:
            self.employee_name_snapshot = self.employee.display_name


class ShiftEntry(EntryBase):
    work_type = models.CharField("Тип работы", max_length=40, choices=WORK_TYPE_CHOICES)
    start_time = models.TimeField("Начало", null=True, blank=True)
    end_time = models.TimeField("Конец", null=True, blank=True)
    hours = models.DecimalField("Кол-во часов", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Смена"
        verbose_name_plural = "Смены"
        ordering = ("-date", "-created_at")
        indexes = [
            models.Index(fields=("date", "work_type"), name="shifts_shif_date_3e95ad_idx"),
            models.Index(fields=("employee", "date"), name="shifts_shif_employe_2ce77c_idx"),
            models.Index(fields=("deleted_at", "date"), name="shifts_shif_deleted_d79110_idx"),
        ]

    def __str__(self):
        employee = self.employee_name_snapshot or "Без сотрудника"
        return f"{self.date:%d.%m.%Y} {employee} {self.get_work_type_display()}"

    def save(self, *args, **kwargs):
        self.fill_employee_snapshot()
        super().save(*args, **kwargs)


class CompanionEntry(EntryBase):
    count = models.PositiveIntegerField("Кол-во сопровождений", default=1)

    class Meta:
        verbose_name = "Сопровождение"
        verbose_name_plural = "Сопровождения"
        ordering = ("-date", "-created_at")
        indexes = [
            models.Index(fields=("employee", "date"), name="shifts_comp_employe_80272d_idx"),
            models.Index(fields=("deleted_at", "date"), name="shifts_comp_deleted_26808e_idx"),
        ]

    def __str__(self):
        employee = self.employee_name_snapshot or "Без сотрудника"
        return f"{self.date:%d.%m.%Y} {employee}: {self.count}"

    def save(self, *args, **kwargs):
        self.fill_employee_snapshot()
        super().save(*args, **kwargs)


class AuditLog(models.Model):
    entity_type = models.CharField("Тип сущности", max_length=60)
    entity_id = models.PositiveIntegerField("ID сущности")
    action = models.CharField("Действие", max_length=40)
    actor = models.CharField("Автор", max_length=160, blank=True)
    diff = models.JSONField("Изменения", default=dict, blank=True)
    before = models.JSONField("Было", default=dict, blank=True)
    after = models.JSONField("Стало", default=dict, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "История изменений"
        verbose_name_plural = "История изменений"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("entity_type", "entity_id", "-created_at"), name="shifts_audi_entity__a699d1_idx"),
        ]

    def __str__(self):
        return f"{self.entity_type} #{self.entity_id}: {self.action}"


class SyncOutbox(models.Model):
    entity_type = models.CharField("Тип сущности", max_length=60)
    entity_id = models.PositiveIntegerField("ID сущности")
    action = models.CharField("Действие", max_length=40)
    payload = models.JSONField("Данные", default=dict)
    status = models.CharField(
        "Статус", max_length=20, choices=SYNC_STATUS_CHOICES, default=SyncStatus.PENDING
    )
    attempts = models.PositiveIntegerField("Попытки", default=0)
    last_error = models.TextField("Последняя ошибка", blank=True)
    last_attempt_at = models.DateTimeField("Последняя попытка", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Очередь синхронизации"
        verbose_name_plural = "Очередь синхронизации"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=("status", "created_at"), name="shifts_sync_status_b390b4_idx"),
        ]

    def __str__(self):
        return f"{self.entity_type} #{self.entity_id}: {self.status}"
