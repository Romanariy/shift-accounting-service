import { MonthPicker } from "./JournalFilters";
import { dateLabel, dateTimeLabel } from "./date-format";
import {useState} from "react";
import EmployeePayoutDetails from "./EmployeePayouts";

type ReviewRow = {id:number;date:string;organization_name:string;employee_name:string;amount:string;reason:string};
export type EarningsData = {
  month:string;start:string;end:string;generated_at:string;total:string;
  employees:{employee_id:number;employee_name:string;amount:string;received?:string|null;remaining?:string|null;payout_review?:boolean}[];
  payout_tracking?:boolean;payout_review?:boolean;received_total?:string|null;remaining_total?:string|null;
  review:ReviewRow[];unassigned:ReviewRow[];review_total:string;unassigned_total:string;
  delivery:null|{id:number;state:string;recipient:number;attempts:number;error:string;created_at:string};
};
const rub = (value:string) => new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(value));
const date = dateLabel;
const states:Record<string,string> = {pending:"В очереди",sending:"Отправляется",sent:"Отправлено",failed:"Ошибка",unknown:"Нужно проверить доставку"};

export default function EarningsReport({month,maxMonth,onMonthChange,data,enabled,approver,busy,onRetry,onChanged}: {
  month:string;maxMonth:string;onMonthChange:(value:string)=>void;data:EarningsData|null;
  enabled:boolean;approver?:string;busy:boolean;onRetry:(id:number,unknown:boolean)=>void;
  onChanged?:()=>void;
}) {
  const [employee,setEmployee]=useState<{employee_id:number;employee_name:string}|null>(null);
  const current = data?.month===month ? data : null;
  const reviewTable = (rows:ReviewRow[]) => <div className="table-scroll"><table><thead><tr><th>Дата</th><th>Организация</th><th>Исполнитель</th><th className="right">Сумма</th><th>Причина</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td>{date(r.date)}</td><td>{r.organization_name}</td><td>{r.employee_name}</td><td className="right amount">{rub(r.amount)}</td><td>{r.reason}</td></tr>)}</tbody></table></div>;
  return <div className="earnings-report">
    <div className="filters"><MonthPicker value={month} max={maxMonth} onChange={onMonthChange}/><span className="filter-tail">Все организации</span>{current&&<a className="button secondary" href={`/api/shifts/ledger/earnings/?month=${month}&format=xlsx`}>Скачать Excel</a>}</div>
    <section className="panel"><div className="panel-heading"><h2>Ежемесячная отправка</h2></div>
      <p>{enabled?"Включена":"Выключена"} · 1-го числа в 10:00, Екатеринбург · за предыдущий месяц.</p>
      <p>Получатель: {approver || "Не назначен — выберите утверждающего в настройках Telegram."}</p>
      {current?.delivery ? <><p role="status">За {month}: {states[current.delivery.state]||current.delivery.state} · Telegram ID {current.delivery.recipient} · попыток: {current.delivery.attempts}</p>
        {current.delivery.error&&<p className="alert">{current.delivery.error}</p>}
        {["failed","unknown"].includes(current.delivery.state)&&<button className="button secondary" disabled={busy||!enabled||!approver} onClick={()=>onRetry(current.delivery!.id,current.delivery!.state==="unknown")}>Повторить отправку</button>}
        <p className="muted">Отправка использует снимок на момент подготовки. Таблица и скачивание Excel показывают текущие данные журнала.</p>
      </> : <p className="muted">Автоотправка за выбранный месяц ещё не подготовлена.</p>}
    </section>
    {!current ? <p role="status">Загружаем главный отчёт…</p> : <>
      <section className="panel"><div className="panel-heading"><h2>Заработок сотрудников</h2><strong>{rub(current.total)}</strong></div>
        <p className="muted">{date(current.start)} — {date(current.end)} · Выплаты учитываются с 01.10.2026 по месяцу выполнения работ. Отрицательный остаток — переплата.</p>
        {current.employees.length ? <div className="table-scroll"><table><thead><tr><th>Сотрудник</th><th className="right">Заработано</th><th className="right">Получено выплат</th><th className="right">Осталось выплатить</th></tr></thead><tbody>{current.employees.map(r=><tr key={r.employee_id}><td><button className="text-button" onClick={()=>setEmployee(r)}>{r.employee_name}</button></td><td className="right amount">{rub(r.amount)}</td><td className="right amount">{r.received!=null?rub(r.received):"Нет данных"}</td><td className="right amount">{r.remaining!=null?<>{rub(r.remaining)}{Number(r.remaining)<0&&<small>Переплата</small>}</>:r.payout_review?"На проверке":"Нет данных"}</td></tr>)}</tbody><tfoot><tr><th>Итого</th><th className="right">{rub(current.total)}</th><th className="right">{current.received_total!=null?rub(current.received_total):"Нет данных"}</th><th className="right">{current.remaining_total!=null?rub(current.remaining_total):current.payout_review?"На проверке":"Нет данных"}</th></tr></tfoot></table></div> : <p className="empty">За этот месяц нет проверенных начислений сотрудникам.</p>}
      </section>
      <section className="panel"><div className="panel-heading"><h2>На проверку</h2><strong>{rub(current.review_total)}</strong></div><p className="muted">Эти суммы не включены в заработок. Исправьте записи в журнале.</p>{current.review.length ? reviewTable(current.review) : <p>Записей на проверку нет.</p>}</section>
      <section className="panel"><div className="panel-heading"><h2>Начисления без сотрудника</h2><strong>{rub(current.unassigned_total)}</strong></div><p className="muted">Автоматические начисления без исполнителя не входят в заработок сотрудников.</p>{current.unassigned.length ? reviewTable(current.unassigned) : <p>Таких начислений нет.</p>}</section>
      <p className="muted">Сформирован: {dateTimeLabel(current.generated_at)} · Екатеринбург</p>
    </>}
    {employee&&<EmployeePayoutDetails employee={employee} month={month} onClose={()=>setEmployee(null)} onChanged={()=>onChanged?.()}/>}
  </div>;
}
