from django.contrib import admin

from .models import AuditLog, CompanionEntry, Employee, Organization, PayRule, ShiftEntry, SyncOutbox, TelegramSource


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "excel_sheet", "is_active")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            from .organizations import copy_initial_rates
            copy_initial_rates(obj)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("short_name", "telegram_username", "default_work_type", "is_active", "sort_order")
    list_filter = ("is_active", "default_work_type")
    search_fields = ("short_name", "full_name", "telegram_username")


@admin.register(PayRule)
class PayRuleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "organization",
        "code",
        "calculation_type",
        "hourly_rate",
        "fixed_amount",
        "min_amount",
        "max_amount",
        "active_from",
        "active_to",
        "is_active",
    )
    list_filter = ("organization", "code", "calculation_type", "is_active")
    search_fields = ("title", "code")


@admin.register(TelegramSource)
class TelegramSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "chat_id", "thread_id", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "chat_id", "thread_id")


@admin.register(ShiftEntry)
class ShiftEntryAdmin(admin.ModelAdmin):
    list_display = ("date", "organization", "employee_name_snapshot", "work_type", "hours", "calculated_amount", "status")
    list_filter = ("work_type", "status", "source", "sync_status", "deleted_at")
    search_fields = ("employee_name_snapshot", "comment", "raw_text", "telegram_author_username")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "date"


@admin.register(CompanionEntry)
class CompanionEntryAdmin(admin.ModelAdmin):
    list_display = ("date", "organization", "employee_name_snapshot", "count", "calculated_amount", "status")
    list_filter = ("status", "source", "sync_status", "deleted_at")
    search_fields = ("employee_name_snapshot", "comment", "raw_text", "telegram_author_username")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "date"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "entity_type", "entity_id", "action", "actor")
    list_filter = ("entity_type", "action")
    search_fields = ("entity_type", "actor")
    readonly_fields = ("created_at", "before", "after", "diff")


@admin.register(SyncOutbox)
class SyncOutboxAdmin(admin.ModelAdmin):
    list_display = ("created_at", "entity_type", "entity_id", "action", "status", "attempts")
    list_filter = ("status", "entity_type", "action")
    search_fields = ("entity_type", "entity_id", "last_error")
    readonly_fields = ("created_at", "updated_at", "last_attempt_at")
