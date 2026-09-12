import json
from calendar import monthrange
from datetime import date

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.shifts.audit import json_safe, log_change
from apps.shifts.models import AuditLog, Employee, Organization
from .billing import approve, delete_batch, delete_invoice, prepare, refresh
from .engine import delete_record, digest, lock, money, recalculate, record_dict, save_record
from .models import Accrual, Batch, Delivery, EmployeePreference, Invoice, OrganizationBilling, Rate, Record, Service, Settings, TelegramContact
from .filters import filter_records
from . import earnings
from .models import EarningsDelivery


RESOURCES = {"services": Service, "rates": Rate, "accruals": Accrual, "preferences": EmployeePreference,
             "contacts": TelegramContact, "organizations": OrganizationBilling}
FIELDS = {
    "services": ("name", "aliases", "input_type", "frequency", "default_organization", "sheet", "sort_order", "active"),
    "rates": ("service", "organization", "calculation", "price", "minimum", "maximum", "start", "end", "active"),
    "accruals": ("service", "organization", "employee", "start", "end", "active"),
    "preferences": ("employee", "service"),
    "organizations": ("organization", "monthly", "recipients"),
    "settings": ("enabled", "earnings_enabled", "approver", "day", "hour", "minute", "service_chat", "service_thread", "expense_chat", "expense_thread"),
}


def serialized(obj):
    if isinstance(obj, Record):
        result = record_dict(obj)
        if hasattr(obj, "allocation_changes"):
            result["allocation_changes"] = obj.allocation_changes
        return result
    result = model_to_dict(obj, exclude=("artifact",))
    for key, value in list(result.items()):
        if isinstance(value, list) and value and hasattr(value[0], "pk"):
            result[key] = [item.pk for item in value]
    result["id"] = obj.pk
    if isinstance(obj, AuditLog):
        result["created_at"] = obj.created_at
    if isinstance(obj, Batch):
        result["invoices"] = [serialized(i) for i in obj.invoices.all()]
        result["deliveries"] = [serialized(d) for d in Delivery.objects.filter(batch=obj).order_by("id")]
    return json_safe(result)


def reply(data, status=200):
    return JsonResponse(json_safe(data), safe=not isinstance(data, list), status=status, json_dumps_params={"ensure_ascii": False})


def apply_fields(obj, payload, resource):
    m2m = {}
    for key in FIELDS[resource]:
        if key not in payload:
            continue
        field = obj._meta.get_field(key)
        value = payload[key]
        if field.many_to_many:
            m2m[key] = value
        elif field.many_to_one or field.one_to_one:
            setattr(obj, field.attname, int(value) if value not in (None, "") else None)
        else:
            setattr(obj, key, field.to_python(value if value != "" or not field.null else None))
    obj.full_clean()
    obj.save()
    for field, ids in m2m.items():
        related = obj._meta.get_field(field).related_model
        if not isinstance(ids, list) or related.objects.filter(pk__in=ids).count() != len(set(ids)):
            raise ValidationError("Один из выбранных элементов не существует.")
        getattr(obj, field).set(ids)
    return obj


@csrf_exempt
def api(request, resource, pk=None, action=None):
    try:
        if request.method not in ("GET", "POST", "PUT", "DELETE"):
            return reply({"error": "Метод не поддерживается."}, 405)
        if request.method == "GET":
            return read(request, resource, pk, action)
        payload = json.loads(request.body or "{}")
        with transaction.atomic():
            lock()
            return mutate(request, resource, pk, action, payload)
    except ObjectDoesNotExist:
        return reply({"error": "Запись не найдена."}, 404)
    except ValidationError as error:
        return reply({"error": " ".join(error.messages)}, 400)
    except (ValueError, TypeError, IntegrityError, KeyError) as error:
        return reply({"error": "Некорректные данные или конфликт записей. " + (str(error) if not isinstance(error, IntegrityError) else "")}, 400)


def read(request, resource, pk, action):
    if resource == "earnings":
        data = earnings.report_data(request.GET.get("month"))
        output_format = request.GET.get("format", "json")
        if output_format == "xlsx":
            response = HttpResponse(earnings.workbook_bytes(data), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response["Content-Disposition"] = f'attachment; filename="earnings-{data["month"]}.xlsx"'
        elif output_format == "json":
            data["delivery"] = earnings.delivery_data(EarningsDelivery.objects.filter(month=data["month"]).first())
            response = reply(data)
        else:
            raise ValidationError("Неизвестный формат отчёта.")
        response["Cache-Control"] = "private, no-store"
        return response
    if resource == "bootstrap":
        Settings.objects.get_or_create(pk=1)
        return reply({**{name: [serialized(x) for x in Model.objects.all()] for name, Model in RESOURCES.items()},
                      "settings": serialized(Settings.objects.get(pk=1)),
                      "employees": [{"id": e.pk, "name": e.display_name, "active": e.is_active} for e in Employee.objects.all()],
                      "organization_list": [{"id": o.pk, "name": o.name, "active": o.is_active} for o in Organization.objects.all()]})
    if resource == "records":
        rows = Record.objects.filter(deleted_at=None).select_related("service", "organization")
        rows = filter_records(rows, request.GET)
        if pk:
            return reply(serialized(rows.get(pk=pk)))
        from django.db.models import Sum
        employee_totals = list(rows.exclude(kind="expense").values("employee_id", "employee_name").annotate(total=Sum("amount")).order_by("employee_name"))
        offset = max(0, int(request.GET.get("offset", 0)))
        limit = min(250, max(1, int(request.GET.get("limit", 50))))
        return reply({"records": [serialized(r) for r in rows[offset:offset + limit]], "count": rows.count(), "limit": limit, "offset": offset,
                      "total": rows.aggregate(total=Sum("amount"))["total"] or "0",
                      "review": rows.filter(Q(review=True) | ~Q(error="")).count(), "employee_totals": employee_totals})
    if resource == "batches":
        if pk:
            return reply(serialized(Batch.objects.get(pk=pk)))
        batches = Batch.objects.all()
        if request.GET.get("month"):
            month_start = date.fromisoformat(request.GET["month"] + "-01")
            month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
            batches = batches.filter(start__lte=month_end, end__gte=month_start)
        if request.GET.get("organization"):
            batches = batches.filter(invoices__organization_id=int(request.GET["organization"])).distinct()
        if request.GET.get("state"):
            if request.GET["state"] not in ("draft", "approved"):
                raise ValidationError("Неизвестное состояние пакета.")
            batches = batches.filter(state=request.GET["state"])
        ordering = request.GET.get("ordering", "-id")
        if ordering not in ("id", "-id"):
            raise ValidationError("Неизвестная сортировка пакетов.")
        return reply([serialized(b) for b in batches.order_by(ordering)[:60]])
    if resource == "invoices" and action == "file":
        invoice = Invoice.objects.get(pk=pk)
        response = HttpResponse(bytes(invoice.artifact), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="invoice-{invoice.pk}-v{invoice.version}.xlsx"'
        response["Cache-Control"] = "private, no-store"
        return response
    if resource == "audit":
        qs = AuditLog.objects.all()
        if request.GET.get("entity_id"):
            entity_id = int(request.GET["entity_id"])
            entity_type = request.GET.get("entity_type", "record")
            condition = Q(entity_type=entity_type, entity_id=entity_id)
            if entity_type == "record":
                record = Record.objects.filter(pk=entity_id).first()
                if record and record.legacy_id:
                    condition |= Q(entity_type=record.legacy_kind, entity_id=record.legacy_id)
            qs = qs.filter(condition)
        return reply([serialized(a) for a in qs.order_by("-created_at", "-id")[:100]])
    if resource in RESOURCES:
        Model = RESOURCES[resource]
        return reply(serialized(Model.objects.get(pk=pk)) if pk else [serialized(x) for x in Model.objects.all()])
    return reply({"error": "Не найдено."}, 404)


def mutate(request, resource, pk, action, payload):
    if resource == "earnings-deliveries" and pk and action == "retry" and request.method == "POST":
        return reply(earnings.delivery_data(earnings.retry(pk, payload.get("confirm_duplicate_risk"))))
    if resource == "batches" and request.method == "DELETE" and pk:
        delete_batch(pk)
        return reply({"deleted": True})
    if resource == "invoices" and request.method == "DELETE" and pk:
        delete_invoice(pk)
        return reply({"deleted": True})
    if resource == "records":
        obj = Record.objects.get(pk=pk) if pk else None
        if request.method == "DELETE" and obj:
            changes = delete_record(obj, allow_frozen=payload.get("correction") is True)
            return reply({"deleted": True, "allocation_changes": changes})
        if request.method not in ("POST", "PUT"):
            return reply({"error": "Метод не поддерживается."}, 405)
        return reply(serialized(save_record(payload, obj, allow_frozen=payload.get("correction") is True)))
    if resource == "recalculate" and request.method == "POST":
        return reply(recalculate(date.fromisoformat(payload["start"]), date.fromisoformat(payload["end"]), payload.get("fingerprint")))
    if resource == "batches" and request.method == "POST":
        if action == "approve":
            return reply(approve(pk, payload["version"]))
        if action == "refresh":
            return reply(serialized(refresh(Batch.objects.get(pk=pk))))
        return reply(serialized(prepare(date.fromisoformat(payload["start"]), date.fromisoformat(payload["end"]), payload["organizations"], replaces=payload.get("replaces"))))
    if resource == "invoices" and request.method == "PUT":
        inv = Invoice.objects.select_related("batch").get(pk=pk)
        if inv.batch.state == "approved":
            raise ValidationError("Создайте новую версию отправленного счёта.")
        if "excluded_ids" in payload:
            raise ValidationError("Все записи учитываются автоматически. Исключение отдельных строк больше не используется.")
        if "adjustments" in payload:
            if not isinstance(payload["adjustments"], list):
                raise ValidationError("Корректировки должны быть списком.")
            adjustments = []
            for item in payload["adjustments"]:
                if not item.get("description", "").strip():
                    raise ValidationError("Для корректировки нужно пояснение.")
                adjustments.append({"description": item["description"].strip(), "amount": str(money(item["amount"]))})
            inv.adjustments = adjustments
        inv.save()
        refresh(inv.batch)
        return reply(serialized(inv.batch))
    if resource == "deliveries" and action == "retry" and request.method == "POST":
        delivery = Delivery.objects.select_related("batch").get(pk=pk)
        if delivery.state not in ("failed", "unknown"):
            raise ValidationError("Повтор возможен только для неуспешной или неопределённой доставки.")
        if delivery.state == "unknown" and payload.get("confirm_duplicate_risk") is not True:
            raise ValidationError("Доставка могла состояться. Подтвердите риск повторного сообщения.")
        if delivery.purpose != "owner" and delivery.version != delivery.batch.version:
            raise ValidationError("Это устаревший пакет. Обновите отчёты.")
        delivery.state, delivery.error = "pending", ""
        delivery.save()
        log_change("delivery", delivery.pk, "retry", actor="web")
        return reply(serialized(delivery))
    if resource == "settings" and request.method == "PUT":
        obj = Settings.objects.get(pk=1)
    elif resource in RESOURCES and resource != "contacts":
        obj = RESOURCES[resource].objects.get(pk=pk) if pk else RESOURCES[resource]()
    else:
        return reply({"error": "Метод не поддерживается."}, 405)
    before = serialized(obj) if obj.pk else None
    if request.method == "DELETE":
        if not hasattr(obj, "active"):
            raise ValidationError("Эту настройку нельзя удалить.")
        obj.active = False
        obj.save()
    else:
        apply_fields(obj, payload, resource)
    log_change(resource, obj.pk, "update" if before else "create", actor="web", before=before, after=serialized(obj))
    return reply(serialized(obj))
