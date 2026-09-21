"use client";
import { dateLabel } from "./date-format";
import type { Item } from "./accounting-types";
import { PaymentStatus, type PaymentsData } from "./Payments";

export type InvoiceFilters = {organization:string;start:string;end:string;state:string;payment_state:string};
export const emptyInvoiceFilters:InvoiceFilters = {organization:"",start:"",end:"",state:"all",payment_state:"all"};
const rub=(value:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(value||0));
const day=dateLabel;

function DeliveryState({invoice}:{invoice:Item}) {
  const counts:Record<string,number>=invoice.delivery_counts||{};
  const labels:Record<string,string>={sent:"Доставлено",pending:"В очереди",sending:"Отправляется",failed:"Ошибка",unknown:"Доставка не подтверждена",cancelled:"Отменено"};
  if(invoice.batch_state==="draft") return <span className="muted">После подтверждения пакета</span>;
  if(!Object.keys(counts).length) return <span className="muted">{invoice.recipient_count?"Доставка ещё не создана":"Получатели не назначены"}</span>;
  return <>{Object.entries(counts).map(([state,count])=><small key={state} className={["failed","unknown"].includes(state)?"inline-error":""}>{labels[state]||state}: {count}</small>)}</>;
}

export default function InvoicesList({data,filters,onFilters,organizations,offset,onOffset,pageSize,loading,error,onOpen,contacts}: {
  data:PaymentsData;filters:InvoiceFilters;onFilters:(value:InvoiceFilters)=>void;organizations:Item[];
  offset:number;onOffset:(offset:number)=>void;pageSize:number;loading:boolean;error:string;onOpen:(invoice:Item)=>void;contacts:Item[];
}) {
  return <section className="panel invoices-panel">
    <div className="panel-heading"><div><h2>Счета организаций</h2><small>Подготовка, отправка и оплата в одном списке</small></div>
      <button className="text-button" onClick={()=>onFilters({...emptyInvoiceFilters,payment_state:"open"})}>Ожидают оплаты: {data.open_count}</button></div>
    <div className="invoice-filters">
      <label>Организация<select aria-label="Организация счетов" value={filters.organization} onChange={e=>onFilters({...filters,organization:e.target.value})}><option value="">Все организации</option>{organizations.map(o=><option key={o.id} value={o.id}>{o.name}{o.active?"":" (архив)"}</option>)}</select></label>
      <label>Период с<input aria-label="Начало периода счетов" type="date" value={filters.start} onChange={e=>onFilters({...filters,start:e.target.value})}/></label>
      <label>По<input aria-label="Конец периода счетов" type="date" value={filters.end} onChange={e=>onFilters({...filters,end:e.target.value})}/></label>
      <label>Подготовка<select aria-label="Подготовка счетов" value={filters.state} onChange={e=>onFilters({...filters,state:e.target.value})}><option value="all">Все</option><option value="draft">На проверке</option><option value="approved">Подтверждены</option></select></label>
      <label>Оплата<select aria-label="Статус оплаты" value={filters.payment_state} onChange={e=>onFilters({...filters,payment_state:e.target.value})}><option value="all">Все</option><option value="open">Ожидают оплаты</option><option value="paid">Оплаченные</option><option value="none">Без подтверждения оплаты</option><option value="superseded">Заменены</option></select></label>
      <button className="text-button" onClick={()=>onFilters({...emptyInvoiceFilters})}>Сбросить фильтры</button>
    </div>
    {!filters.start&&!filters.end&&<p className="muted">Все периоды, включая задолженности прошлых месяцев.</p>}
    {error?<p role="alert" className="alert">{error}</p>:loading?<p role="status">Загружаем счета…</p>:data.invoices.length?<div className="table-scroll"><table><thead><tr><th>Организация / счёт</th><th>Период</th><th className="right">Сумма</th><th>Подготовка</th><th>Отправка</th><th>Оплата</th><th>Действия</th></tr></thead><tbody>{data.invoices.map(invoice=><tr key={invoice.id}>
      <td><strong>{invoice.organization_name}</strong><small>Счёт №{invoice.id} · версия {invoice.version}</small><small>Пакет №{invoice.batch}</small></td>
      <td>{day(invoice.start)} — {day(invoice.end)}</td><td className="amount right">{rub(invoice.total)}</td>
      <td><span className={`pill ${invoice.batch_state==="draft"?"warning":""}`}>{invoice.batch_state==="draft"?"На проверке":"Подтверждён"}</span>{invoice.errors?.length>0&&<small className="inline-error">Требует исправления</small>}</td>
      <td><DeliveryState invoice={invoice}/></td><td><PaymentStatus invoice={invoice} contacts={contacts}/></td>
      <td><button className="text-button" onClick={()=>onOpen(invoice)}>Открыть счёт</button><small><a href={`/api/shifts/ledger/invoices/${invoice.id}/file/`}>Скачать Excel</a></small></td>
    </tr>)}</tbody></table></div>:<p className="empty">{filters.payment_state==="open"?"Нет счетов, ожидающих оплаты.":"Нет счетов с такими условиями. Измените фильтры или подготовьте новый пакет."}</p>}
    <div className="pagination"><button className="button secondary compact" disabled={!offset||loading} onClick={()=>onOffset(Math.max(0,offset-pageSize))}>← Назад</button><span>{data.count?offset+1:0}–{Math.min(offset+pageSize,data.count)} из {data.count}</span><button className="button secondary compact" disabled={offset+pageSize>=data.count||loading} onClick={()=>onOffset(offset+pageSize)}>Далее →</button></div>
  </section>;
}
