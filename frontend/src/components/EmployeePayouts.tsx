"use client";
import {useEffect, useRef, useState} from "react";
import {dateLabel, dateTimeLabel} from "./date-format";
import type {Item} from "./accounting-types";

const rub=(v:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(v||0));
const states:Record<string,string>={unpaid:"Не оплачено",partial:"Частично оплачено",paid:"Оплачено",overpaid:"Переплата",unknown:"Нет данных"};
export async function payoutApi(path:string,body?:unknown){
  const response=await fetch("/api/shifts/ledger/employee-payouts/"+path,{method:body===undefined?"GET":"POST",headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),cache:"no-store"});
  const data=await response.json();if(!response.ok)throw Error(data.error||"Не удалось обновить выплаты");return data;
}
export function PayoutStatus({record}:{record:Item}){
  return record.kind==="expense"?null:<small>{record.payout_tracking?<>Выплачено {rub(record.payout_paid)} · {Number(record.payout_remaining)<0?`Переплата ${rub(Math.abs(Number(record.payout_remaining)))}`:`Осталось ${rub(record.payout_remaining)}`}</>:"Выплаты: нет данных до 01.10.2026"}</small>;
}
export function PayoutDialog({selector,onClose,onSaved}:{selector:Record<string,unknown>;onClose:()=>void;onSaved:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [draft,setDraft]=useState<any>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  const key=JSON.stringify(selector);
  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>node?.close();},[]);
  useEffect(()=>{let active=true;setDraft(null);setError("");payoutApi("",{...JSON.parse(key),action:"preview"}).then(d=>{if(active)setDraft(d);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[key,reload]);
  async function confirm(){setBusy(true);setError("");try{await payoutApi("",{action:"confirm",token:draft.token});onSaved();onClose();}catch(e){setError(e instanceof Error?e.message:"Ошибка выплаты");setDraft(null);}finally{setBusy(false);}}
  const first=draft?.lines[0];
  return <dialog ref={dialog} className="edit-dialog" onCancel={e=>{if(busy)e.preventDefault();else onClose();}}><div className="dialog-heading"><h2>Подтвердить выплату сотруднику</h2><button className="icon-button" aria-label="Закрыть" disabled={busy} onClick={onClose}>×</button></div><div className="padded">
    {error&&<p role="alert" className="alert">{error}</p>}
    {draft?<><p><strong>{first.employee_name}</strong> · {first.organization_name} · {dateLabel(first.date)}</p><div className="table-scroll"><table><thead><tr><th>Работа</th><th>Начислено</th><th>Выплачено</th><th>К выплате</th></tr></thead><tbody>{draft.lines.map((r:any)=><tr key={r.record}><td>{r.service_name}{r.description&&r.description!==r.service_name&&<small>{r.description}</small>}</td><td>{rub(r.earned)}</td><td>{rub(r.paid)}</td><td>{rub(r.to_pay)}</td></tr>)}</tbody></table></div><p>Передано сотруднику: <strong>{rub(draft.amount)}</strong>. Сохранится фактическая выплата текущим временем.</p></>:!error&&<p role="status">Подготавливаем состав выплаты…</p>}
    <div className="form-actions"><button className="button" disabled={busy||!draft} onClick={confirm}>Подтвердить выплату</button>{!draft&&error&&<button className="button secondary" onClick={()=>setReload(v=>v+1)}>Обновить предпросмотр</button>}<button className="button secondary" disabled={busy} onClick={onClose}>Закрыть</button></div>
  </div></dialog>;
}

export default function EmployeePayoutDetails({employee,month,onClose,onChanged}:{employee:{employee_id:number;employee_name:string};month:string;onClose:()=>void;onChanged:()=>void}){
  const [state,setState]=useState(""),[offset,setOffset]=useState(0),[historyOffset,setHistoryOffset]=useState(0),[revision,setRevision]=useState(0);
  const [data,setData]=useState<any>(null),[history,setHistory]=useState<any>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const [selector,setSelector]=useState<Record<string,unknown>|null>(null),[cancel,setCancel]=useState<any>(null),[reason,setReason]=useState("");
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>node?.close();},[]);
  useEffect(()=>{let active=true;setData(null);setError("");const q=new URLSearchParams({employee:String(employee.employee_id),month,state,details:"1",offset:String(offset)});Promise.all([payoutApi("?"+q),payoutApi("?"+new URLSearchParams({employee:String(employee.employee_id),month,limit:"50",offset:String(historyOffset)}))]).then(([d,h])=>{if(active){setData(d);setHistory(h);}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[employee.employee_id,month,state,offset,historyOffset,revision]);
  function changed(){setRevision(v=>v+1);onChanged();}
  async function cancelPayment(){setBusy(true);setError("");try{await payoutApi(`${cancel.id}/cancel/`,{reason,revision:cancel.revision});setCancel(null);setReason("");changed();}catch(e){setError(e instanceof Error?e.message:"Не удалось отменить выплату");}finally{setBusy(false);}}
  return <dialog ref={dialog} className="edit-dialog" onCancel={e=>{if(busy)e.preventDefault();else onClose();}}><div className="dialog-heading"><h2>Выплаты · {employee.employee_name} · {month}</h2><button className="icon-button" aria-label="Закрыть" disabled={busy} onClick={onClose}>×</button></div><div className="padded">
    <label>Состояние выплаты<select value={state} onChange={e=>{setState(e.target.value);setOffset(0);}}><option value="">Все</option>{Object.entries(states).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
    {error&&<p className="alert" role="alert">{error}</p>}
    {!data?<p role="status">Загрузка…</p>:<><div className="table-scroll"><table><thead><tr><th>Дата / студия</th><th>Работа</th><th>Начислено / выплачено / остаток</th><th>Действия</th></tr></thead><tbody>{data.records.map((r:Item)=><tr key={r.id}><td>{dateLabel(r.date)}<small>{r.organization_name}</small></td><td>{r.service_name}<small>{r.description}</small><small>{states[r.payout_state]}</small></td><td>{rub(r.amount)}<PayoutStatus record={r}/></td><td><div className="table-actions">{r.payout_tracking&&Number(r.payout_remaining)>0&&<><button className="text-button" onClick={()=>setSelector({record:r.id})}>Оплатить услугу</button>{r.organization&&<button className="text-button" onClick={()=>setSelector({employee:employee.employee_id,organization:r.organization,date:r.date})}>Оплатить день</button>}</>}</div></td></tr>)}</tbody></table></div>{!data.count&&<p>Работ с выбранным состоянием нет.</p>}<div className="form-actions"><button className="button secondary compact" disabled={!offset} onClick={()=>setOffset(v=>Math.max(0,v-50))}>← Назад</button><span>{data.count} записей</span><button className="button secondary compact" disabled={offset+50>=data.count} onClick={()=>setOffset(v=>v+50)}>Далее →</button></div></>}
    <h3>История выплат</h3>{history?.payouts.map((p:any)=><div className="panel padded" key={p.id}><p>№{p.id} · {rub(p.amount)} · {p.organization_name} · работы {dateLabel(p.work_date)}</p><p className="muted">{dateTimeLabel(p.paid_at)} · {p.author==="web"?"Администратор сайта":p.author}</p><p>{p.lines.map((l:any)=>l.snapshot.service_name).join(", ")}</p>{p.cancelled_at?<p>Отменена {dateTimeLabel(p.cancelled_at)}: {p.cancel_reason}</p>:<button className="text-button" disabled={busy} onClick={()=>{setCancel(p);setReason("");}}>Отменить выплату</button>}</div>)}
    {history&&!history.count&&<p>Выплат пока нет.</p>}{history&&<div className="form-actions"><button className="button secondary compact" disabled={!historyOffset} onClick={()=>setHistoryOffset(v=>Math.max(0,v-50))}>← Предыдущие выплаты</button><span>{history.count} выплат</span><button className="button secondary compact" disabled={historyOffset+50>=history.count} onClick={()=>setHistoryOffset(v=>v+50)}>Следующие выплаты →</button></div>}
    {cancel&&<fieldset><legend>Отменить всю выплату №{cancel.id}: {rub(cancel.amount)}</legend><label>Причина<textarea value={reason} maxLength={500} onChange={e=>setReason(e.target.value)}/></label><div className="form-actions"><button className="button" disabled={busy||!reason.trim()} onClick={cancelPayment}>Отменить подтверждение выплаты</button><button className="button secondary" disabled={busy} onClick={()=>setCancel(null)}>Назад</button></div></fieldset>}
  </div>{selector&&<PayoutDialog selector={selector} onClose={()=>setSelector(null)} onSaved={changed}/>}</dialog>;
}
