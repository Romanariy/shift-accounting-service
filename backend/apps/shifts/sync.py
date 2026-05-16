import json
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

from .constants import SyncStatus
from .models import SyncOutbox


def queue_sync_change(entity_type, entity_id, action, payload):
    return SyncOutbox.objects.create(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        payload=payload,
    )


def try_sync_once(limit=50):
    endpoint = getattr(settings, "SHIFT_SYNC_ENDPOINT", "")
    token = getattr(settings, "SHIFT_SYNC_TOKEN", "")

    if not endpoint:
        return {"sent": 0, "failed": 0, "skipped": "SHIFT_SYNC_ENDPOINT is not set"}

    sent = 0
    failed = 0
    items = SyncOutbox.objects.filter(status=SyncStatus.PENDING).order_by("created_at")[:limit]

    for item in items:
        item.attempts += 1
        item.last_attempt_at = timezone.now()

        try:
            body = json.dumps(
                {
                    "entityType": item.entity_type,
                    "entityId": item.entity_id,
                    "action": item.action,
                    "payload": item.payload,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}" if token else "",
                },
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Global DB returned HTTP {response.status}")

            item.status = SyncStatus.SYNCED
            item.last_error = ""
            sent += 1
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            item.status = SyncStatus.FAILED if item.attempts >= 5 else SyncStatus.PENDING
            item.last_error = str(error)
            failed += 1

        item.save(update_fields=("attempts", "last_attempt_at", "status", "last_error", "updated_at"))

    return {"sent": sent, "failed": failed}

