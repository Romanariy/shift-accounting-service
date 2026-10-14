"use client";

import { useEffect, useRef, useState } from "react";
import { dateLabel, dateTimeLabel } from "./date-format";
import type { Bootstrap, Item } from "./accounting-types";

const fields: Record<string, string> = {
  work_date:"Дата работы",cancelled_by:"Кто отменил выплату",cancel_reason:"Причина отмены выплаты",lines:"Оплаченные работы",paid:"Выплачено",remaining:"Осталось выплатить",to_pay:"К выплате",
  charge:"Начисление",snapshot:"Условия начисления",booking:"Запись клиента",record:"Выполненная смена",rule:"Правило",client_name:"Клиент",rule_revision:"Версия правила",
  client_bonus:"Клиентские доплаты",client_bonus_details:"Состав клиентских доплат",client_bonus_review:"Клиентские доплаты требуют проверки",client:"Клиент",clients:"Клиенты правила",phones:"Телефоны",revision:"Ревизия",applied:"Применённые условия",state:"Состояние",rooms:"Залы",sources:"Источники",selected_record:"Получатель",selected_rule:"Выбранное правило",selection_token:"Версия выбора",name:"Название",active:"Включено",start:"Начало действия",end:"Окончание действия",
  payment_state:"Статус оплаты",paid_at:"Дата подтверждения оплаты",paid_by:"Кто подтвердил оплату",payment_revision:"Ревизия оплаты",payment_recipients:"Получатели подтверждения",
  actual_hours:"Фактические часы", overlap_hours:"Пересекающиеся часы", payable_hours:"Оплачиваемые часы", pricing_version:"Версия алгоритма", bonus:"Доплата", bonus_reason:"Причина доплаты", base_amount:"База", overlap_factor:"Коэффициент", calculation_note:"Расчёт", calculation_review:"Расчёт требует проверки", payer:"Плательщик", reason:"Причина вычета",
  id: "Номер записи", kind: "Вид записи", date: "Дата", organization: "Организация", organizationId: "Организация",
  daily_calculation: "Дневной расчёт", calculation_version: "Версия расчёта",
  organization_name: "Организация", employee: "Исполнитель", employeeId: "Исполнитель", employee_name: "Имя исполнителя",
  service: "Услуга", service_name: "Название услуги", workType: "Вид работы", input_type: "Формат ввода",
  description: "Описание", comment: "Комментарий", start_time: "Начало", startTime: "Начало", end_time: "Конец", endTime: "Конец",
  units: "Часы / количество", hours: "Часы", count: "Количество", amount: "Сумма", amountOverride: "Заданная сумма",
  review: "Нужно проверить", error: "Ошибка", included: "Включение в счёт (прежняя настройка)", source: "Источник",
  author: "Автор", raw_text: "Исходное сообщение", deleted: "Удалено", deleted_at: "Дата удаления",
  created_at: "Дата создания", updated_at: "Дата обновления", sheet: "Лист Excel", telegram_chat_id: "Чат Telegram",
  telegram_message_id: "Сообщение Telegram", telegram_part: "Часть сообщения", legacy_kind: "Прежний вид записи",
  legacy_id: "Номер прежней записи", auto_key: "Ключ автоначисления",
};
const values: Record<string, string> = {
  draft:"Ожидает проверки",unconfirmed:"Нужно подтверждение",stale_selection:"Подтвердите выбор заново",no_rule:"Нет действующего правила",needs_interval:"Уточните время",rule_review:"Нужен пересчёт правила",
  confirmed:"Подтверждена",excluded:"Исключена",needs_choice:"Выберите получателя",waiting:"Ожидает смену",applied:"Начислена",
  open:"Ожидает оплаты",paid:"Оплачен",none:"Без подтверждения оплаты",superseded:"Заменён",
  receipt:"Получение оплаты", deduction:"Вычет", service: "Типовая услуга", oneoff: "Разовая работа", expense: "Расход", time: "Интервал времени", quantity: "Количество",
  mark: "Отметка", amount: "Готовая сумма", web: "Сайт", telegram: "Telegram", auto: "Автоначисление",
  demo: "Тестовые данные", import: "Импорт", manual: "Ручной ввод", small_admin: "Малый админ", big_admin: "Большой админ",
};
const actions: Record<string, string> = {confirm:"Подтверждение записи",choose:"Выбор получателя",exclude:"Исключение записи",apply:"Применение правила",ocr_source:"Добавление снимка",recognized:"Распознавание",paid:"Подтверждение оплаты",payment_cancelled:"Отмена подтверждения оплаты",payment_superseded:"Замена счёта",create: "Создание", update: "Изменение", delete: "Удаление", restore: "Восстановление", import: "Импорт"};

function timestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Время не указано" : dateTimeLabel(value);
}

export default function HistoryDialog({record, data, onClose}: {record:Item;data:Bootstrap|null;onClose:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [entries, setEntries] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError(""); setEntries([]);
    async function load() {
      try {
        const response = await fetch(`/api/shifts/ledger/audit/?entity_type=${record.entity_type||"record"}&entity_id=${record.id}`, {cache:"no-store", signal:controller.signal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Не удалось загрузить историю");
        if (active) setEntries(result);
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Не удалось загрузить историю");
      } finally { if (active) setLoading(false); }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [record.id, record.entity_type, retry]);

  function format(key: string, value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "Да" : "Нет";
    if(key==="clients"&&Array.isArray(value))return value.map(id=>record.client_labels?.[id]||`Клиент №${id}`).join(", ");
    if(key==="client")return record.client_name||`Клиент №${value}`;
    if(key==="charge"&&typeof value==="object"){
      const charge=value as Item,s=charge.snapshot;
      return `${values[charge.state]||charge.state} · ${format("amount",charge.amount)}${s?.rule?` · ${s.rule} · ${s.client} · ${s.start}–${s.end}`:""}`;
    }
    if(key==="snapshot"&&typeof value==="object"){
      const s=value as Item;return s.rule?`${s.rule} · ${s.client} · ${s.start}–${s.end} · ${format("amount",s.amount)}`:"—";
    }
    if(key==="client_bonus_details"&&Array.isArray(value))return value.map(d=>format("snapshot",d)).join("; ")||"—";
    if(["bonus","base_amount","client_bonus"].includes(key))return format("amount",value);
    if (key === "paid_by") return data?.contacts.find(c=>c.user_id===Number(value))?.name || `Telegram ID ${value}`;
    if (key === "payment_state") return values[String(value)] || String(value);
    if (["organization", "organizationId"].includes(key)) return data?.organization_list.find(o => o.id === Number(value))?.name || `№${value}`;
    if (["employee", "employeeId"].includes(key)) return data?.employees.find(e => e.id === Number(value))?.name || `№${value}`;
    if (key === "service") return data?.services.find(s => s.id === Number(value))?.name || `№${value}`;
    if (["amount", "amountOverride"].includes(key) && Number.isFinite(Number(value))) return new Intl.NumberFormat("ru-RU", {style:"currency",currency:"RUB"}).format(Number(value));
    if (["date","work_date"].includes(key) && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) return dateLabel(String(value));
    if(key==="lines"&&Array.isArray(value))return value.map(l=>`№${l.record} · ${l.snapshot?.service_name||"Работа"} · ${format("amount",l.amount)}`).join("; ");
    if (key.endsWith("_at")) return timestamp(String(value));
    if (["kind", "source", "input_type", "workType", "state"].includes(key)) return values[String(value)] || String(value);
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  return <dialog ref={dialog} className="edit-dialog history-dialog" onCancel={onClose} aria-labelledby="history-title" aria-describedby="history-record">
    <div className="dialog-heading"><div><span className="eyebrow">{record.entity_type==="invoice"?"СЧЁТ":({bonus_client:"КЛИЕНТ",bonus_rule:"ПРАВИЛО ДОПЛАТЫ",client_booking:"ЗАПИСЬ КЛИЕНТА"} as Record<string,string>)[record.entity_type]||"ЖУРНАЛ / ЗАПИСЬ"} №{record.id}</span><h2 id="history-title">{record.entity_type==="invoice"?"История оплаты":"История изменений"}</h2><p id="history-record">{record.entity_type==="invoice"?record.organization_name:record.entity_type?.startsWith("bonus_")?record.name:record.entity_type==="client_booking"?<>{record.client_name} · {dateLabel(record.date)} · {record.organization_name}</>:<>{format("date", record.date)} · {record.kind === "service" ? record.service_name : record.description}<small>{record.organization_name} · {record.employee_name || record.author || "Без исполнителя"}</small></>}</p></div><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть историю" title="Закрыть историю">×</button></div>
    <div className="history-content" aria-busy={loading}>
      {loading ? <p className="empty" role="status">Загружаем историю…</p> : error ? <div className="padded"><p className="alert" role="alert">{error}</p><button type="button" className="button secondary" onClick={() => setRetry(n => n + 1)}>Повторить загрузку</button></div> : entries.length ? <>
        <p className="history-timezone">Время Екатеринбурга · Последние изменения сверху</p>
        {entries.map(entry => <article className="history-item" key={entry.id}>
          <div className="history-meta"><strong>{entry.actor?.endsWith(":shared-shift") ? "Перераспределение смены" : actions[entry.action] || entry.action}</strong><time dateTime={entry.created_at}>{timestamp(entry.created_at)}</time></div>
          <p className="history-author">{entry.actor==="web"?"Администратор сайта":entry.actor?.startsWith("telegram:")?(data?.contacts.find(c=>c.user_id===Number(entry.actor.split(":")[1]))?.name||entry.actor):entry.actor || "Автор не указан"}</p>
          {Object.keys(entry.diff || {}).length ? <div className="table-scroll"><table className="history-diff"><thead><tr><th>Поле</th><th>Было</th><th>Стало</th></tr></thead><tbody>{Object.entries(entry.diff as Record<string, {from:unknown;to:unknown}>).filter(([key])=>!["choices","selection_token"].includes(key)).map(([key, change]) => <tr key={key}><th scope="row">{key==="reason"&&record.entity_type==="client_booking"?"Причина исключения":record.entity_type==="invoice"&&key==="reason"?"Причина отмены":fields[key] || key}</th><td>{format(key, change.from)}</td><td>{format(key, change.to)}</td></tr>)}</tbody></table></div> : <p className="muted">Изменения полей не зафиксированы.</p>}
        </article>)}
        {entries.length === 100 && <p className="history-timezone">Показаны последние 100 событий.</p>}
      </> : <p className="empty">История этой записи пока пуста.</p>}
    </div>
  </dialog>;
}
