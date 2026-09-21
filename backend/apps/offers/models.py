from django.db import models
from django.db.models import Q


class OfferConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    chat_id = models.BigIntegerField(null=True, blank=True)
    thread_id = models.BigIntegerField(null=True, blank=True)
    claimed_thread_id = models.BigIntegerField(null=True, blank=True)
    released_thread_id = models.BigIntegerField(null=True, blank=True)
    enabled = models.BooleanField(default=False)


class RecognitionProfile(models.Model):
    organization = models.OneToOneField("shifts.Organization", on_delete=models.PROTECT)
    rooms = models.JSONField(default=list)
    publishers = models.ManyToManyField("ledger.TelegramContact", blank=True, related_name="offer_profiles")
    active = models.BooleanField(default=True)
    header_path = models.CharField(max_length=200, blank=True)
    sample_path = models.CharField(max_length=200, blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    sample_error = models.TextField(blank=True)
    sample_version = models.PositiveIntegerField(default=0)
    confirmed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class ShiftOffer(models.Model):
    # Planned opportunities are deliberately separate from ledger.Record and payroll.
    source_key = models.CharField(max_length=180, unique=True)
    sender_id = models.BigIntegerField()
    sender_name = models.CharField(max_length=160, blank=True)
    source_signature = models.CharField(max_length=64, blank=True, db_index=True)
    caption_message_id = models.BigIntegerField(null=True)
    comment = models.TextField(blank=True)
    kind = models.CharField(max_length=16, default="shift", db_index=True)
    service = models.ForeignKey("ledger.Service", null=True, blank=True, on_delete=models.PROTECT)
    service_name = models.CharField(max_length=160, blank=True)
    input_type = models.CharField(max_length=16, default="time")
    units = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    update_target = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="updates")
    update_target_version = models.PositiveIntegerField(null=True, blank=True)
    update_mode = models.CharField(max_length=16, blank=True)
    organization = models.ForeignKey("shifts.Organization", null=True, on_delete=models.PROTECT)
    date = models.DateField(null=True, db_index=True)
    start_time = models.TimeField(null=True)
    end_time = models.TimeField(null=True)
    state = models.CharField(max_length=24, default="collecting", db_index=True)
    employee = models.ForeignKey("shifts.Employee", null=True, on_delete=models.PROTECT)
    employee_name = models.CharField(max_length=160, blank=True)
    assignee_user_id = models.BigIntegerField(null=True)
    intervals = models.JSONField(default=list)
    questions = models.JSONField(default=list)
    evidence = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    duplicate_of = models.ForeignKey("self", null=True, on_delete=models.SET_NULL)
    version = models.PositiveIntegerField(default=1)
    delivery_generation = models.PositiveIntegerField(default=1)
    topic_chat_id = models.BigIntegerField(null=True)
    topic_thread_id = models.BigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True)
    published_at = models.DateTimeField(null=True)


class OfferImage(models.Model):
    offer = models.ForeignKey(ShiftOffer, on_delete=models.CASCADE, related_name="images")
    path = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64, db_index=True)
    message_id = models.BigIntegerField()
    telegram_file_id = models.TextField(blank=True)
    recognized = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    purged_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("offer", "message_id"), name="offer_source_image")]
        ordering = ("message_id", "id")


class RecognitionJob(models.Model):
    offer = models.ForeignKey(ShiftOffer, null=True, on_delete=models.CASCADE, related_name="jobs")
    profile = models.ForeignKey(RecognitionProfile, null=True, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=16, default="pending", db_index=True)
    available_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    elapsed_ms = models.PositiveIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(Q(offer__isnull=False, profile__isnull=True) |
                                              Q(offer__isnull=True, profile__isnull=False)), name="recognition_one_target"),
        ]


class OfferDelivery(models.Model):
    offer = models.ForeignKey(ShiftOffer, on_delete=models.CASCADE, related_name="deliveries")
    purpose = models.CharField(max_length=24)
    recipient = models.BigIntegerField()
    thread_id = models.BigIntegerField(null=True)
    version = models.PositiveIntegerField()
    generation = models.PositiveIntegerField(default=1)
    payload = models.JSONField(default=dict, blank=True)
    delete_requested = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    key = models.CharField(max_length=180, unique=True)
    state = models.CharField(max_length=16, default="pending", db_index=True)
    message_ids = models.JSONField(default=list)
    prompt_key = models.CharField(max_length=100, blank=True)
    reply_resolved = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class OfferMessageEdit(models.Model):
    delivery = models.OneToOneField(OfferDelivery, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default="pending", db_index=True)
    revision = models.PositiveIntegerField(default=1)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    error = models.TextField(blank=True)


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=30, primary_key=True)
    updated_at = models.DateTimeField(auto_now=True)
    detail = models.CharField(max_length=200, blank=True)


class OfferWizard(models.Model):
    user_id = models.BigIntegerField(primary_key=True)
    step = models.CharField(max_length=24, default="source")
    data = models.JSONField(default=dict)
    offer = models.ForeignKey(ShiftOffer, null=True, on_delete=models.SET_NULL)
    processed_messages = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)
