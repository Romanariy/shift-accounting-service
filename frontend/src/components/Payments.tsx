"use client";

import type { Item } from "./accounting-types";

export type PaymentsData = {invoices:Item[];count:number;open_count:number};
const rub=(value:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(value||0));
const date=(value:string)=>value?value.slice(0,10).split("-").reverse().join("."):"—";
const paidDate=(value:string)=>value?new Intl.DateTimeFormat("ru-RU",{timeZone:"Asia/Yekaterinburg",dateStyle:"short",timeStyle:"short"}).format(new Date(value)):"";
const stateLabels:Record<string,string>={none:"Без подтверждения оплаты",open:"Ожидает оплаты",paid:"Оплачен",superseded:"Заменён новым счётом"};

export function PaymentStatus({invoice,contacts=[]}: {invoice:Item;contacts?:Item[]}) {
  const state=invoice.payment_state||"none";
  const by=invoice.paid_by_name||contacts.find(contact=>contact.user_id===invoice.paid_by)?.name||invoice.paid_by;
  return <div className="payment-status"><span className={`pill ${state==="open"?"warning":["none","superseded"].includes(state)?"neutral":""}`}>{stateLabels[state]||state}</span>{state==="paid"&&<small>{paidDate(invoice.paid_at)} · {by||"Получатель"} · Екатеринбург</small>}{state==="open"&&<small>Закроется для всех получателей после нажатия «Оплатил» в Telegram.</small>}</div>;
}

export default function Payments({data,state,offset,pageSize,loading,error,onState,onOffset,onOpen,contacts}: {data:PaymentsData;state:string;offset:number;pageSize:number;loading:boolean;error:string;onState:(value:string)=>void;onOffset:(value:number)=>void;onOpen:(invoice:Item)=>void;contacts:Item[]}) {
  return <section className="panel payments-panel"><div className="panel-heading"><div><h2>Незакрытые счета: {data.open_count}</h2><small>Все периоды и организации. Данные обновляются автоматически.</small></div><label className="payment-filter"><span>Показать</span><select aria-label="Статус оплаты" value={state} onChange={e=>onState(e.target.value)}><option value="open">Ожидают оплаты</option><option value="paid">Оплаченные</option><option value="all">Все</option></select></label></div>
    {error?<p className="alert" role="alert">{error}</p>:loading?<p className="empty" role="status">Загружаем оплаты…</p>:data.invoices.length?<div className="table-scroll"><table><thead><tr><th>Организация / счёт</th><th>Период</th><th className="right">Сумма</th><th>Оплата</th><th><span className="sr-only">Действия</span></th></tr></thead><tbody>{data.invoices.map(invoice=><tr key={invoice.id}><td><strong>{invoice.organization_name}</strong><small>Счёт №{invoice.id} · версия {invoice.version}</small></td><td>{date(invoice.start)} — {date(invoice.end)}</td><td className="amount right">{rub(invoice.total)}</td><td><PaymentStatus invoice={invoice} contacts={contacts}/></td><td><button className="text-button" onClick={()=>onOpen(invoice)}>Открыть счёт</button><small><a href={`/api/shifts/ledger/invoices/${invoice.id}/file/`}>Скачать Excel</a></small></td></tr>)}</tbody></table></div>:<div className="empty"><h2>{state==="open"?"Нет счетов, ожидающих оплаты":"Нет счетов с этим статусом"}</h2><p>Подтверждение оплаты включается у получателей в настройках каждой организации. Новые подтверждённые счета появятся здесь.</p></div>}
    <div className="pagination"><button className="button secondary compact" disabled={!offset||loading} onClick={()=>onOffset(Math.max(0,offset-pageSize))}>← Назад</button><span>{data.count?offset+1:0}–{Math.min(offset+pageSize,data.count)} из {data.count}</span><button className="button secondary compact" disabled={offset+pageSize>=data.count||loading} onClick={()=>onOffset(offset+pageSize)}>Далее →</button></div>
  </section>;
}
