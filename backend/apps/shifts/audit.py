from datetime import date, datetime, time
from decimal import Decimal

from .models import AuditLog


def json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def build_diff(before, after):
    before = before or {}
    after = after or {}
    keys = sorted(set(before) | set(after))
    diff = {}

    for key in keys:
        old_value = before.get(key)
        new_value = after.get(key)
        if old_value != new_value:
            diff[key] = {"from": old_value, "to": new_value}

    return diff


def log_change(entity_type, entity_id, action, actor="", before=None, after=None):
    safe_before = json_safe(before or {})
    safe_after = json_safe(after or {})

    return AuditLog.objects.create(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        before=safe_before,
        after=safe_after,
        diff=build_diff(safe_before, safe_after),
    )

