from . import service


def button(text, action, offer, extra=""):
    from aiogram.types import InlineKeyboardButton
    return InlineKeyboardButton(text=text, callback_data=f"offer:{action}:{offer.pk}:{offer.version}" + (f":{extra}" if extra else ""))


def describe(offer):
    org = offer.organization.name if offer.organization_id else "Организация не определена"
    day = offer.date.strftime("%d.%m.%Y") if offer.date else "Дата не определена"
    start = offer.start_time.strftime("%H:%M") if offer.start_time else "?"
    end = offer.end_time.strftime("%H:%M") if offer.end_time else "?"
    text = f"Предложение №{offer.pk} · {service.STATE_LABELS.get(offer.state, offer.state)}\n{org}\n{day} · {start}–{end}"
    if offer.employee_name:
        text += f"\nСотрудник: {offer.employee_name}"
    if offer.comment:
        text += "\n\n" + offer.comment[:1800]
    return text


def render(delivery):
    from aiogram.types import InlineKeyboardMarkup, ForceReply
    from apps.shifts.models import Organization
    offer = delivery.offer
    text, rows = describe(offer), []
    editable = offer.state in ("review", "needs_input", "failed") and delivery.version == offer.version
    if delivery.purpose == "topic" and offer.state in ("open", "publishing"):
        rows = [[button("Взять", "claim", offer)]]
    elif delivery.purpose == "assignment" and offer.state == "claimed":
        if delivery.recipient in (offer.assignee_user_id, service.coordinator_id()):
            rows = [[button("Сняться" if delivery.recipient == offer.assignee_user_id else "Снять со смены", "release", offer)]]
    elif delivery.purpose == "question" and editable and not delivery.reply_resolved:
        question = next((q for q in offer.questions if q["key"] == delivery.prompt_key), None)
        labels = {"date": "Укажите дату ДД.ММ.ГГГГ.", "organization": "Выберите организацию.",
            "start_time": "Укажите начало смены ЧЧ:ММ.", "end_time": "Укажите окончание смены ЧЧ:ММ.", "comment": "Введите комментарий. Чтобы убрать его, отправьте —."}
        label = question["label"] if question else labels.get(delivery.prompt_key, "Уточните данные на сайте.")
        text += "\n\n" + label
        if delivery.prompt_key == "organization":
            allowed = service.allowed_organizations(offer.sender_id)
            rows = [[button(org.name, "org", offer, str(org.pk))] for org in Organization.objects.filter(pk__in=allowed)]
        elif delivery.prompt_key == "split":
            text += "\nСайт → Предложения смен → эта заявка → Разделить альбом."
        else:
            text += "\nОтветьте на это сообщение (функция «Ответить»)."
            return text[:4000], ForceReply(selective=True)
    elif delivery.purpose == "edit_menu" and editable:
        rows = [[button(label, "field", offer, field)] for field, label in
                (("date", "Дата"), ("organization", "Организация"), ("start_time", "Начало"), ("end_time", "Окончание"), ("comment", "Комментарий"))]
    elif delivery.purpose == "review" and editable:
        if offer.state == "failed":
            text += "\n\n" + offer.error + " Откройте заявку на сайте."
        elif not offer.questions:
            text += "\n\nЗаписи:\n" + "\n".join(f'{i.get("room", "")} · {i.get("start") or "?"}–{i.get("end") or "?"}' for i in offer.intervals)[:1000]
            text += "\n\nПроверьте все записи по изображениям. Фактическую работу нужно будет внести в журнал отдельно."
            rows = [[button("Опубликовать", "publish", offer)], [button("Изменить", "edit", offer), button("Отменить", "cancel", offer)]]
    if offer.state == "duplicate":
        text += f"\nЭти изображения уже получены: предложение №{offer.duplicate_of_id}."
    return text[:4000], InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
