"""Client booking bonuses. Callers hold the ledger singleton transaction lock."""
import re
from datetime import date, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone

from apps.shifts.audit import json_safe, log_change
from apps.shifts.models import Organization
from .models import BonusClient, BonusRule, ClientBooking, ClientBonusCharge, Record
from . import overlaps


def normalize_phone(value):
    raw = str(value).strip()
    if not re.fullmatch(r"\+?[\d\s().-]+", raw):
        raise ValidationError("Введите полный номер телефона с кодом страны.")
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if raw.startswith("+") and 8 <= len(digits) <= 15 and not digits.startswith("0"):
        return "+" + digits
    raise ValidationError("Введите полный номер телефона: +7… или 8… (11 цифр).")


def client_label(client):
    return client.name or f"Клиент №{client.pk}"


def data(obj):
    result = json_safe(model_to_dict(obj))
    result["id"] = obj.pk
    if isinstance(obj, BonusRule):
        result["clients"] = list(obj.clients.order_by("id").values_list("id", flat=True))
        result["organization_name"] = obj.organization.name
        result["pending"] = result["applied"] != rule_config(obj)
    if isinstance(obj, ClientBooking):
        result["client_name"] = client_label(obj.client)
        result["organization_name"] = obj.organization.name
        charge = ClientBonusCharge.objects.filter(booking=obj).first()
        result["charge"] = json_safe(model_to_dict(charge)) if charge else None
        result["choices"] = assessment(obj)[1]
    return result


def rule_config(rule):
    return {"name": rule.name, "organization": rule.organization_id, "clients": list(rule.clients.order_by("id").values_list("id", flat=True)),
            "amount": str(rule.amount), "start": str(rule.start), "end": str(rule.end) if rule.end else None,
            "active": rule.active, "revision": rule.revision}


def require_revision(obj, payload):
    if type(payload.get("revision")) is not int or payload["revision"] != obj.revision:
        raise ValidationError("Данные изменились. Обновите страницу и повторите действие.")


def source_token():
    from .engine import digest
    return digest({"clients": list(BonusClient.objects.order_by("id").values()),
        "rules": [{**dict(r), "clients": list(BonusRule.objects.get(pk=r["id"]).clients.order_by("id").values_list("id", flat=True))}
                  for r in BonusRule.objects.order_by("id").values()],
        "bookings": list(ClientBooking.objects.order_by("id").values())})


def day_rows(keys):
    from django.db.models import Q
    query = Q(pk__in=[])
    for org, day in keys:
        query |= Q(organization_id=org, date=day)
    return Record.objects.filter(query, kind="service", service__legacy_code__in=overlaps.ADMIN_CODES,
        deleted_at=None).select_related("service", "employee", "organization").order_by("id")


def assignment(row):
    return json_safe([row.pk, row.employee_id, row.organization_id, row.date, row.service_id,
                      row.start_time, row.end_time, row.units])


def assessment(booking, rows=None):
    from .engine import digest
    rows = list(day_rows({(booking.organization_id, booking.date)})) if rows is None else rows
    configs = {r.pk: r.applied for r in BonusRule.objects.all() if r.applied and r.applied.get("active")
               and r.applied["organization"] == booking.organization_id and booking.client_id in r.applied["clients"]
               and r.applied["start"] <= str(booking.date) and (not r.applied["end"] or r.applied["end"] >= str(booking.date))}
    span = overlaps.interval(booking)
    candidates = []
    for row in rows:
        own = overlaps.interval(row)
        if row.employee_id and own and span:
            seconds = (min(own[1], span[1]) - max(own[0], span[0])).total_seconds()
            if seconds > 0:
                candidates.append((row, seconds))
    token = digest({"booking": [booking.client_id, booking.organization_id, booking.date, booking.start_time, booking.end_time],
        "client_revision": booking.client.revision, "rules": configs,
        "rows": [[r.pk, r.employee_id, r.start_time, r.end_time, r.units, r.service_id] for r in rows]})
    choices = {"token": token, "rules": [{"id": pk, **c} for pk, c in configs.items()],
        "records": [{"id": r.pk, "employee": r.employee_id, "employee_name": r.employee.display_name,
                     "start_time": r.start_time, "end_time": r.end_time} for r, _ in candidates]}
    if booking.state != "confirmed" or not booking.client.active:
        return {"state": "excluded" if booking.state == "excluded" else "unconfirmed"}, choices
    if not configs:
        return {"state": "no_rule"}, choices
    if not span:
        return {"state": "needs_interval"}, choices
    if not candidates:
        return {"state": "waiting"}, choices
    employees = {r.employee_id for r, _ in candidates}
    selected = next((r for r, _ in candidates if r.pk == booking.selected_record_id), None)
    chosen = configs.get(booking.selected_rule_id)
    if booking.selection_token:
        if booking.selection_token != token or not selected or not chosen:
            return {"state": "stale_selection"}, choices
    elif len(employees) > 1 or len(configs) > 1:
        return {"state": "needs_choice"}, choices
    else:
        selected = sorted(candidates, key=lambda p: (-p[1], p[0].start_time, p[0].pk))[0][0]
        chosen = next(iter(configs.values()))
    rid = booking.selected_rule_id if booking.selection_token else next(iter(configs))
    return {"state": "applied", "record_id": selected.pk, "rule_id": rid, "amount": Decimal(chosen["amount"]),
        "snapshot": {"rule": chosen["name"], "rule_revision": chosen["revision"], "client": client_label(booking.client),
            "booking": booking.pk, "date": str(booking.date), "start": str(booking.start_time)[:5],
            "end": str(booking.end_time)[:5], "amount": chosen["amount"], "work": assignment(selected)}}, choices


def reconcile(keys, rows=None, *, persist=True, refresh_rules=False):
    """Change only the client contribution; preserve tariff and manual bonus amounts."""
    from .engine import money
    keys = set(keys)
    rows = list(day_rows(keys)) if rows is None else rows
    by_id = {r.pk: r for r in rows}
    bookings = ClientBooking.objects.filter(organization_id__in={k[0] for k in keys}, date__in={k[1] for k in keys}).select_related("client")
    totals, details, pending = {}, {}, set()
    for booking in bookings:
        if (booking.organization_id, booking.date) not in keys:
            continue
        peers = [r for r in rows if r.organization_id == booking.organization_id and r.date == booking.date]
        result, choices = assessment(booking, peers)
        old = ClientBonusCharge.objects.filter(booking=booking).first()
        if (not refresh_rules and old and old.amount and old.rule_id and booking.state == "confirmed"
                and old.record_id in {r["id"] for r in choices["records"]}
                and old.snapshot.get("work") == assignment(by_id[old.record_id])
                and (old.snapshot.get("rule_revision") != old.rule.applied.get("revision") or not booking.client.active)):
            result = {"state": "rule_review", "record_id": old.record_id, "rule_id": old.rule_id,
                      "amount": old.amount, "snapshot": old.snapshot}
        if result["state"] in ("needs_choice", "needs_interval", "stale_selection", "rule_review"):
            if result["state"] == "needs_interval":
                pending.update(r.pk for r in peers)
            else:
                pending.update(r["id"] for r in choices["records"])
        if result["state"] == "unconfirmed" and booking.state == "draft" and choices["rules"]:
            pending.update(r["id"] for r in choices["records"])
            if not overlaps.interval(booking):
                pending.update(r.pk for r in peers)
        if result.get("record_id") in by_id:
            pk = result["record_id"]
            totals[pk] = totals.get(pk, Decimal(0)) + result["amount"]
            details.setdefault(pk, []).append(result["snapshot"])
        if persist:
            values = {"record_id": result.get("record_id"), "rule_id": result.get("rule_id"),
                      "amount": result.get("amount", Decimal(0)), "state": result["state"], "snapshot": result.get("snapshot", {})}
            before = json_safe(model_to_dict(old)) if old else None
            if old is None or any(getattr(old, k) != v for k, v in values.items()):
                values["revision"] = old.revision + 1 if old else 1
                charge, _ = ClientBonusCharge.objects.update_or_create(booking=booking, defaults=values)
                log_change("client_charge", charge.pk, "update" if old else "create", actor="system:client-bonus",
                           before=before, after=json_safe(model_to_dict(charge)))
    for row in rows:
        previous = row.client_bonus
        row.client_bonus = money(totals.get(row.pk, 0))
        row.client_bonus_details = details.get(row.pk, [])
        row.client_bonus_review = row.pk in pending
        row.amount = money(row.amount - previous + row.client_bonus)
    return rows


def apply_days(keys, *, correction=False, refresh_rules=False, actor="web:client-bonus"):
    from .engine import record_dict, is_frozen, audit_record, sync_legacy
    rows = list(day_rows(keys))
    before = {r.pk: record_dict(r, include_group=False) for r in rows}
    reconcile(keys, rows, refresh_rules=refresh_rules)
    changes = []
    for row in rows:
        if any(before[row.pk][k] != json_safe(getattr(row, k)) for k in ("client_bonus", "client_bonus_details", "client_bonus_review")):
            # A condition revision is a concurrency guard, not a new financial result.
            semantic = lambda details: [{k: v for k, v in d.items() if k != "rule_revision"} for d in details]
            real_change = (Decimal(before[row.pk]["client_bonus"]) != row.client_bonus
                or before[row.pk]["client_bonus_review"] != row.client_bonus_review
                or semantic(before[row.pk]["client_bonus_details"]) != semantic(row.client_bonus_details))
            if not real_change:
                row.save(update_fields=("client_bonus_details",))
                continue
            if is_frozen(row) and not correction:
                raise ValidationError("Работа включена в подтверждённый счёт. Включите корректировку и выпустите новую версию документа.")
            changes.append({"id": row.pk, "date": str(row.date), "employee_name": row.employee_name, "organization_name": row.organization.name,
                            "before": before[row.pk]["amount"], "after": str(row.amount), "client_bonus": str(row.client_bonus),
                            "details": row.client_bonus_details, "review": row.client_bonus_review})
            row.save()
            audit_record(row, before[row.pk], actor)
            sync_legacy(row)
    return changes


@transaction.atomic
def recalculate(payload):
    from .engine import lock, digest
    lock()
    start, end = date.fromisoformat(payload["start"]), date.fromisoformat(payload["end"])
    if start > end or (end-start).days > 366 or end > timezone.localdate():
        raise ValidationError("Выберите прошедший период не длиннее года.")
    token = digest({"source": overlaps.source_token(), "request": {k: payload.get(k) for k in ("start", "end", "rule", "revision", "correction")}})
    apply = payload.get("fingerprint") is not None
    if apply and token != payload["fingerprint"]:
        raise ValidationError("Данные изменились. Повторите предварительный просмотр.")
    rule = BonusRule.objects.get(pk=payload["rule"]) if payload.get("rule") else None
    if rule:
        require_revision(rule, payload)
        rule.applied = rule_config(rule)
        rule.save(update_fields=("applied",))
    keys = set(ClientBooking.objects.filter(date__range=(start, end)).values_list("organization_id", "date"))
    keys |= set(Record.objects.filter(date__range=(start, end), kind="service", service__legacy_code__in=overlaps.ADMIN_CODES).values_list("organization_id", "date"))
    changes = apply_days(keys, correction=payload.get("correction") is True, refresh_rules=True)
    if apply:
        if rule:
            log_change("bonus_rule", rule.pk, "apply", actor="web", after=rule.applied)
    else:
        transaction.set_rollback(True)
    return {"changes": changes, "fingerprint": token, "applied": apply}


@transaction.atomic
def save_client(payload, pk=None):
    from .engine import lock
    lock()
    obj = BonusClient.objects.get(pk=pk) if pk else BonusClient()
    before = data(obj) if pk else None
    if pk:
        require_revision(obj, payload)
    phones = payload.get("phones", obj.phones)
    if not isinstance(phones, list) or not phones or len(phones) > 30:
        raise ValidationError("Добавьте от 1 до 30 телефонов клиента.")
    phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
    if any(set(phones) & set(other.phones) for other in BonusClient.objects.exclude(pk=pk)):
        raise ValidationError("Телефон уже привязан к другому клиенту.")
    obj.phones, obj.name = phones, str(payload.get("name", obj.name)).strip()
    obj.active = payload.get("active", obj.active)
    if type(obj.active) is not bool:
        raise ValidationError("Некорректное состояние клиента.")
    if pk and all(before[k] == json_safe(getattr(obj, k)) for k in ("name", "phones", "active")):
        return before
    if pk:
        obj.revision += 1
    obj.full_clean(); obj.save()
    log_change("bonus_client", obj.pk, "update" if pk else "create", actor="web", before=before, after=data(obj))
    return data(obj)


@transaction.atomic
def save_rule(payload, pk=None):
    from .engine import lock, money
    lock()
    obj = BonusRule.objects.get(pk=pk) if pk else BonusRule()
    before = data(obj) if pk else None
    if pk:
        require_revision(obj, payload)
    clients = payload.get("clients", list(obj.clients.values_list("pk", flat=True)) if pk else [])
    wanted_active = obj._meta.get_field("active").to_python(payload.get("active", obj.active))
    if (not isinstance(clients, list) or not clients or any(type(i) is not int for i in clients) or len(set(clients)) != len(clients)
            or BonusClient.objects.filter(pk__in=clients).count() != len(clients)
            or wanted_active and BonusClient.objects.filter(pk__in=clients, active=True).count() != len(clients)):
        raise ValidationError("Выберите одного или нескольких активных клиентов.")
    for field in ("name", "start", "end", "active"):
        if field in payload:
            value = payload[field] if field == "active" else payload[field] or None
            setattr(obj, field, obj._meta.get_field(field).to_python(value))
    obj.name = str(obj.name or "").strip()
    obj.organization_id = int(payload.get("organization", obj.organization_id))
    obj.amount = money(payload.get("amount", obj.amount))
    if obj.amount <= 0 or not obj.name.strip() or obj.end and obj.end < obj.start or type(obj.active) is not bool:
        raise ValidationError("Укажите название, положительную сумму и корректный период.")
    if not Organization.objects.filter(pk=obj.organization_id, is_active=True).exists():
        raise ValidationError("Выберите активную студию.")
    if pk and sorted(clients) == before["clients"] and all(before[k] == json_safe(getattr(obj, k if k != "organization" else "organization_id"))
            for k in ("name", "organization", "amount", "start", "end", "active")):
        return before
    if pk:
        obj.revision += 1
    obj.full_clean(); obj.save(); obj.clients.set(clients)
    log_change("bonus_rule", obj.pk, "update" if pk else "create", actor="web", before=before, after=data(obj))
    return data(obj)


@transaction.atomic
def save_booking(payload, pk=None, action=None):
    from .engine import lock
    lock()
    obj = ClientBooking.objects.select_related("client", "organization").get(pk=pk) if pk else ClientBooking()
    before = data(obj) if pk else None
    keys = {(obj.organization_id, obj.date)} if pk else set()
    if pk:
        require_revision(obj, payload)
    if not action:
        for field in ("client", "organization"):
            if field in payload:
                setattr(obj, field+"_id", int(payload[field]))
        if "date" in payload:
            obj.date = date.fromisoformat(payload["date"])
        for field in ("start_time", "end_time"):
            if field in payload:
                setattr(obj, field, time.fromisoformat(payload[field]) if payload[field] else None)
        obj.rooms = payload.get("rooms", obj.rooms)
        if not isinstance(obj.rooms, list) or len(obj.rooms) > 30 or any(not isinstance(v, str) or len(v)>160 for v in obj.rooms):
            raise ValidationError("Некорректные названия залов.")
        if not obj.client.active or not obj.organization.is_active:
            raise ValidationError("Клиент или студия отключены.")
        obj.selection_token = ""; obj.selected_record_id = obj.selected_rule_id = None
        # Exact duplicates, including multiple halls, merge rather than create charges twice.
        duplicate = ClientBooking.objects.filter(client_id=obj.client_id, organization_id=obj.organization_id, date=obj.date,
            start_time=obj.start_time, end_time=obj.end_time).exclude(pk=pk).first() if obj.start_time and obj.end_time else None
        if duplicate:
            if pk:
                raise ValidationError("Такая запись клиента уже существует. Откройте её вместо создания повтора.")
            rooms = sorted(set(duplicate.rooms) | set(obj.rooms))
            if rooms != duplicate.rooms:
                previous = data(duplicate)
                duplicate.rooms = rooms; duplicate.revision += 1; duplicate.save()
                log_change("client_booking", duplicate.pk, "update", actor="web", before=previous, after=data(duplicate))
            return {**data(duplicate), "duplicate": True}
    elif action == "confirm":
        if not obj.start_time or not obj.end_time:
            raise ValidationError("Уточните начало и конец клиентской записи.")
        if not obj.client.active or not obj.organization.is_active:
            raise ValidationError("Клиент или студия отключены.")
        ambiguous = {s.get("card") for s in obj.sources if s.get("ambiguous_client") and s.get("card")}
        if ambiguous:
            peers = ClientBooking.objects.filter(organization_id=obj.organization_id, date=obj.date).exclude(pk=obj.pk)
            for peer in peers:
                if ambiguous & {s.get("card") for s in peer.sources}:
                    if peer.state == "confirmed":
                        raise ValidationError("Для этой карточки уже подтверждён другой клиент. Сначала исключите ошибочную запись.")
                    if peer.state == "draft":
                        previous = data(peer)
                        peer.state, peer.reason = "excluded", "При проверке карточки выбран другой клиент."
                        peer.revision += 1; peer.save()
                        log_change("client_booking", peer.pk, "exclude", actor="web", before=previous, after=data(peer))
        obj.state, obj.reason = "confirmed", ""
    elif action == "exclude":
        reason = str(payload.get("reason", "")).strip()
        if not reason or len(reason) > 500:
            raise ValidationError("Укажите причину исключения (до 500 символов).")
        obj.state, obj.reason = "excluded", reason
    elif action == "restore":
        obj.state, obj.reason = "draft", ""
    elif action == "choose":
        _, choices = assessment(obj)
        if choices["token"] != payload.get("token"):
            raise ValidationError("Смены или правила изменились. Обновите выбор.")
        record, rule = payload.get("record"), payload.get("rule")
        if record not in {r["id"] for r in choices["records"]} or rule not in {r["id"] for r in choices["rules"]} or obj.state != "confirmed":
            raise ValidationError("Выберите подходящую выполненную смену и правило.")
        obj.selected_record_id, obj.selected_rule_id, obj.selection_token = record, rule, choices["token"]
    else:
        raise ValidationError("Неизвестное действие.")
    if action not in ("exclude", "restore") and (bool(obj.start_time) != bool(obj.end_time) or obj.start_time and obj.end_time <= obj.start_time):
        raise ValidationError("Укажите корректный интервал клиентской записи в пределах дня.")
    if pk:
        obj.revision += 1
    obj.full_clean(); obj.save()
    keys.add((obj.organization_id, obj.date))
    changes = apply_days(keys, correction=payload.get("correction") is True)
    log_change("client_booking", obj.pk, action or ("update" if pk else "create"), actor="web", before=before, after=data(obj))
    return {**data(obj), "changes": changes}


def listing(resource, params, pk=None):
    Model = {"bonus-clients": BonusClient, "bonus-rules": BonusRule, "client-bookings": ClientBooking}[resource]
    if pk:
        return data(Model.objects.get(pk=pk))
    rows = Model.objects.order_by("-id")
    if resource != "bonus-clients":
        for key in ("organization", "client") if resource == "client-bookings" else ("organization",):
            if params.get(key):
                rows = rows.filter(**{key+"_id": int(params[key])})
    if resource == "client-bookings":
        for key, lookup in (("start", "date__gte"), ("end", "date__lte"), ("state", "state")):
            if params.get(key):
                rows = rows.filter(**{lookup: params[key]})
    offset, limit = max(0, int(params.get("offset", 0))), min(100, max(1, int(params.get("limit", 30))))
    return {"items": [data(r) for r in rows[offset:offset+limit]], "count": rows.count(), "offset": offset, "limit": limit}
