import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def choices(*values):
    return [(v, v) for v in values]


class Service(models.Model):
    name = models.CharField(max_length=120, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    input_type = models.CharField(max_length=16, choices=choices("time", "quantity", "amount", "mark"), default="time")
    frequency = models.CharField(max_length=16, choices=choices("entry", "daily", "monthly"), default="entry")
    default_organization = models.ForeignKey("shifts.Organization", null=True, blank=True, on_delete=models.PROTECT)
    sheet = models.CharField(max_length=31, blank=True)
    sort_order = models.IntegerField(default=100)
    included = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    legacy_code = models.CharField(max_length=48, blank=True)

    class Meta:
        ordering = ("sort_order", "id")

    def clean(self):
        self.name = self.name.strip()
        self.sheet = self.sheet.strip()
        if not isinstance(self.aliases, list) or any(not isinstance(a, str) for a in self.aliases):
            raise ValidationError("Алиасы должны быть списком строк.")
        self.aliases = list(dict.fromkeys(a.strip().casefold() for a in self.aliases if a.strip()))
        tokens = {self.name.casefold(), *self.aliases}
        if not self.name:
            raise ValidationError("Укажите название услуги.")
        for other in Service.objects.exclude(pk=self.pk).filter(active=True):
            if self.active and tokens & {other.name.casefold(), *other.aliases}:
                raise ValidationError(f"Алиас уже используется услугой «{other.name}».")
            if self.sheet and self.sheet.casefold() == other.sheet.casefold():
                self.sheet = other.sheet
        if self.sheet and (re.search(r"[\\/*?:\[\]\x00-\x1f]", self.sheet) or self.sheet.startswith("'") or self.sheet.endswith("'") or self.sheet.casefold() in {"основной", "расходы", "history"}):
            raise ValidationError("Недопустимое или зарезервированное название листа.")


class Rate(models.Model):
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    organization = models.ForeignKey("shifts.Organization", on_delete=models.PROTECT)
    calculation = models.CharField(max_length=16, choices=choices("fixed", "hourly", "quantity", "amount"), default="fixed")
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    minimum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    maximum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    start = models.DateField()
    end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    def clean(self):
        if self.end and self.end < self.start:
            raise ValidationError("Конец тарифа раньше начала.")
        if any(v is not None and (not v.is_finite() or v < 0) for v in (self.price, self.minimum, self.maximum)):
            raise ValidationError("Стоимость и ограничения должны быть неотрицательными.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValidationError("Минимум превышает максимум.")
        if self.service.legacy_code == "phone" and self.calculation != "fixed":
            raise ValidationError("Телефоны используют фиксированную стоимость за день.")
        required_input = {"hourly": "time", "quantity": "quantity", "amount": "amount"}.get(self.calculation)
        if required_input and self.service.input_type != required_input:
            raise ValidationError("Способ расчёта не соответствует формату ввода услуги.")
        overlaps = Rate.objects.exclude(pk=self.pk).filter(service_id=self.service_id, organization_id=self.organization_id, active=True).filter(Q(end__isnull=True) | Q(end__gte=self.start))
        if self.end:
            overlaps = overlaps.filter(start__lte=self.end)
        if self.active and overlaps.exists():
            raise ValidationError("Период пересекается с другим тарифом этой организации.")


class Accrual(models.Model):
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    organization = models.ForeignKey("shifts.Organization", on_delete=models.PROTECT)
    employee = models.ForeignKey("shifts.Employee", null=True, blank=True, on_delete=models.PROTECT)
    start = models.DateField()
    end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("service", "organization"), name="ledger_accrual_org_service")]

    def clean(self):
        from apps.shifts.models import Employee
        if self.employee_id and Employee.objects.filter(pk=self.employee_id, is_active=False).exists():
            previous = Accrual.objects.filter(pk=self.pk).values_list("employee_id", flat=True).first() if self.pk else None
            if previous != self.employee_id:
                raise ValidationError("Выберите активного сотрудника для автоначисления.")
        if self.service.frequency == "entry":
            raise ValidationError("Выберите ежедневную или ежемесячную услугу.")
        if self.end and self.end < self.start:
            raise ValidationError("Конец периода раньше начала.")


class EmployeePreference(models.Model):
    employee = models.OneToOneField("shifts.Employee", on_delete=models.CASCADE)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)

    def clean(self):
        if self.service.input_type != "time" or self.service.frequency != "entry":
            raise ValidationError("Для смены сотрудника выберите услугу с интервалом времени и начислением по записи.")


class TelegramContact(models.Model):
    user_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=160)
    username = models.CharField(max_length=120, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)


class OrganizationBilling(models.Model):
    organization = models.OneToOneField("shifts.Organization", on_delete=models.PROTECT)
    recipients = models.ManyToManyField(TelegramContact, blank=True)
    monthly = models.BooleanField(default=False)
    include_expenses = models.BooleanField(default=True)
    excluded_services = models.ManyToManyField(Service, blank=True)


class Settings(models.Model):
    # A singleton also serializes mutations and approval snapshots on PostgreSQL.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    earnings_enabled = models.BooleanField(default=True)
    approver = models.ForeignKey(TelegramContact, null=True, blank=True, on_delete=models.PROTECT)
    day = models.PositiveSmallIntegerField(default=1)
    hour = models.PositiveSmallIntegerField(default=10)
    minute = models.PositiveSmallIntegerField(default=0)
    service_chat = models.BigIntegerField(null=True, blank=True)
    service_thread = models.BigIntegerField(null=True, blank=True)
    expense_chat = models.BigIntegerField(null=True, blank=True)
    expense_thread = models.BigIntegerField(null=True, blank=True)

    def clean(self):
        if not 1 <= self.day <= 28 or self.hour > 23 or self.minute > 59:
            raise ValidationError("Укажите день 1–28 и корректное время.")
        if self.enabled and not self.approver_id:
            raise ValidationError("Назначьте утверждающего перед включением рассылки.")
        if self.service_chat is not None and self.service_chat == self.expense_chat and (self.service_thread is None or self.expense_thread is None or self.service_thread == self.expense_thread):
            raise ValidationError("Топики услуг и расходов должны различаться.")


class DailyCalculation(models.Model):
    active = models.BooleanField(default=True)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    organization = models.ForeignKey("shifts.Organization", on_delete=models.PROTECT)
    date = models.DateField()
    price = models.DecimalField(max_digits=12, decimal_places=2)
    minimum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    maximum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    hours = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("service", "organization", "date"), name="ledger_daily_calculation")]


class Record(models.Model):
    kind = models.CharField(max_length=16, choices=choices("service", "oneoff", "expense"))
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.PROTECT)
    organization = models.ForeignKey("shifts.Organization", null=True, blank=True, on_delete=models.PROTECT)
    employee = models.ForeignKey("shifts.Employee", null=True, blank=True, on_delete=models.PROTECT)
    employee_name = models.CharField(max_length=160, blank=True)
    date = models.DateField(db_index=True)
    description = models.TextField(blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    units = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    daily_calculation = models.ForeignKey(DailyCalculation, null=True, blank=True, on_delete=models.PROTECT, related_name="records")
    calculation_version = models.PositiveSmallIntegerField(default=0)
    review = models.BooleanField(default=False)
    included = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    source = models.CharField(max_length=16, default="web")
    author_id = models.BigIntegerField(null=True, blank=True)
    author_name = models.CharField(max_length=160, blank=True)
    raw_text = models.TextField(blank=True)
    chat_id = models.BigIntegerField(null=True, blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    part = models.PositiveIntegerField(default=0)
    legacy_kind = models.CharField(max_length=16, blank=True)
    legacy_id = models.PositiveBigIntegerField(null=True, blank=True)
    auto_key = models.CharField(max_length=100, null=True, blank=True, unique=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-id")
        constraints = [
            models.UniqueConstraint(fields=("chat_id", "message_id", "part"), condition=Q(chat_id__isnull=False, message_id__isnull=False), name="ledger_message_part"),
            models.UniqueConstraint(fields=("legacy_kind", "legacy_id"), condition=Q(legacy_id__isnull=False), name="ledger_legacy_record"),
        ]


class Batch(models.Model):
    start = models.DateField()
    end = models.DateField()
    organization_ids = models.JSONField(default=list)
    state = models.CharField(max_length=16, default="draft")
    version = models.PositiveIntegerField(default=1)
    fingerprint = models.CharField(max_length=64, blank=True)
    scheduled_key = models.CharField(max_length=16, null=True, blank=True, unique=True)
    approver_id_snapshot = models.BigIntegerField(null=True, blank=True)
    approved_by = models.CharField(max_length=160, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Invoice(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="invoices")
    organization = models.ForeignKey("shifts.Organization", on_delete=models.PROTECT)
    organization_name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    replaces = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    lines = models.JSONField(default=list)
    adjustments = models.JSONField(default=list, blank=True)
    excluded_ids = models.JSONField(default=list, blank=True)
    recipients = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    artifact = models.BinaryField(default=bytes)


class Delivery(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT)
    purpose = models.CharField(max_length=24)  # owner, preview, approval
    recipient = models.BigIntegerField()
    version = models.PositiveIntegerField()
    key = models.CharField(max_length=160, unique=True)
    state = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EarningsDelivery(models.Model):
    month = models.CharField(max_length=7, unique=True)
    snapshot = models.JSONField(default=dict)
    artifact = models.BinaryField(default=bytes)
    recipient = models.BigIntegerField()
    state = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
