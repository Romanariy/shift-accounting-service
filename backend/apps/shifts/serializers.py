from decimal import Decimal

from django.utils import timezone

from .constants import WORK_TYPE_CHOICES
from .models import AuditLog, CompanionEntry, Employee, PayRule, ShiftEntry


WORK_TYPE_LABELS = dict(WORK_TYPE_CHOICES)


def decimal_to_string(value):
    if value is None:
        return None
    return str(Decimal(value).quantize(Decimal("0.01")))


def date_to_string(value):
    return value.isoformat() if value else None


def time_to_string(value):
    return value.strftime("%H:%M") if value else None


def datetime_to_string(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat()


def employee_to_dict(employee: Employee):
    return {
        "id": employee.id,
        "shortName": employee.short_name,
        "fullName": employee.full_name,
        "telegramUsername": employee.telegram_username,
        "telegramUserId": employee.telegram_user_id,
        "aliases": employee.aliases,
        "defaultWorkType": employee.default_work_type,
        "isActive": employee.is_active,
        "sortOrder": employee.sort_order,
        "createdAt": datetime_to_string(employee.created_at),
        "updatedAt": datetime_to_string(employee.updated_at),
    }


def organization_to_dict(organization):
    return {"id": organization.id, "name": organization.name, "aliases": organization.aliases,
            "excelSheet": organization.excel_sheet, "isActive": organization.is_active}


def pay_rule_to_dict(rule: PayRule):
    return {
        "id": rule.id,
        "organizationId": rule.organization_id,
        "code": rule.code,
        "title": rule.title,
        "calculationType": rule.calculation_type,
        "hourlyRate": decimal_to_string(rule.hourly_rate),
        "fixedAmount": decimal_to_string(rule.fixed_amount),
        "minAmount": decimal_to_string(rule.min_amount),
        "maxAmount": decimal_to_string(rule.max_amount),
        "activeFrom": date_to_string(rule.active_from),
        "activeTo": date_to_string(rule.active_to),
        "isActive": rule.is_active,
        "createdAt": datetime_to_string(rule.created_at),
        "updatedAt": datetime_to_string(rule.updated_at),
    }


def shift_to_dict(entry: ShiftEntry):
    return {
        "kind": "shift",
        "organizationId": entry.organization_id,
        "organizationName": entry.organization.name if entry.organization_id else "",
        "id": entry.id,
        "date": date_to_string(entry.date),
        "employeeId": entry.employee_id,
        "employeeName": entry.employee_name_snapshot,
        "workType": entry.work_type,
        "workTypeLabel": WORK_TYPE_LABELS.get(entry.work_type, entry.work_type),
        "startTime": time_to_string(entry.start_time),
        "endTime": time_to_string(entry.end_time),
        "hours": decimal_to_string(entry.hours),
        "comment": entry.comment,
        "calculatedAmount": decimal_to_string(entry.calculated_amount),
        "source": entry.source,
        "status": entry.status,
        "telegramAuthorUsername": entry.telegram_author_username,
        "rawText": entry.raw_text,
        "syncStatus": entry.sync_status,
        "syncError": entry.sync_error,
        "deletedAt": datetime_to_string(entry.deleted_at),
        "createdAt": datetime_to_string(entry.created_at),
        "updatedAt": datetime_to_string(entry.updated_at),
    }


def companion_to_dict(entry: CompanionEntry):
    return {
        "kind": "companion",
        "organizationId": entry.organization_id,
        "organizationName": entry.organization.name if entry.organization_id else "",
        "id": entry.id,
        "date": date_to_string(entry.date),
        "employeeId": entry.employee_id,
        "employeeName": entry.employee_name_snapshot,
        "count": entry.count,
        "comment": entry.comment,
        "calculatedAmount": decimal_to_string(entry.calculated_amount),
        "source": entry.source,
        "status": entry.status,
        "telegramAuthorUsername": entry.telegram_author_username,
        "rawText": entry.raw_text,
        "syncStatus": entry.sync_status,
        "syncError": entry.sync_error,
        "deletedAt": datetime_to_string(entry.deleted_at),
        "createdAt": datetime_to_string(entry.created_at),
        "updatedAt": datetime_to_string(entry.updated_at),
    }


def audit_to_dict(entry: AuditLog):
    return {
        "id": entry.id,
        "entityType": entry.entity_type,
        "entityId": entry.entity_id,
        "action": entry.action,
        "actor": entry.actor,
        "diff": entry.diff,
        "before": entry.before,
        "after": entry.after,
        "createdAt": datetime_to_string(entry.created_at),
    }
