"use client";
import {FormEvent,useEffect,useState} from "react";
import {dateLabel} from "./date-format";
import HistoryDialog from "./HistoryDialog";
import type {Bootstrap,Item} from "./accounting-types";
const rub=(v:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(v||0));
const today=()=>new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Yekaterinburg"}).format(new Date());

export default function OrganizationReceipts({organization,data,onClose}:{organization:Item;data:Bootstrap|null;onClose:()=>void}) {
  const [start,setStart]=useState(today().slice(0,7)+"-01"),[end,setEnd]=useState(today());
  const [offset,setOffset]=useState(0),[revision,setRevision]=useState(0);
  const [result,setResult]=useState<any>({receipts:[],total:0,count:0}),[draft,setDraft]=useState<any>(null),[history,setHistory]=useState<Item|null>(null);
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false);
  useEffect(()=>{const controller=new AbortController();setLoading(true);setError("");
    const query=new URLSearchParams({organization:String(organization.id),start,end,offset:String(offset)});
    fetch("/api/shifts/ledger/receipts/?"+query,{signal:controller.signal,cache:"no-store"}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error);if(!controller.signal.aborted){if(offset&&offset>=d.count)setOffset(Math.max(0,offset-50));else setResult(d);}}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();
  },[organization.id,start,end,offset,revision]);
  async function write(row:any,remove=false){setBusy(true);setError("");try{
    const r=await fetch(`/api/shifts/ledger/receipts/${row.id?row.id+"/":""}`,{method:remove?"DELETE":row.id?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...row,organization:organization.id})});const d=await r.json();if(!r.ok)throw Error(d.error);setDraft(null);setRevision(v=>v+1);
  }catch(e){setError(e instanceof Error?e.message:"Ошибка сохранения");}finally{setBusy(false);}}
  function submit(e:FormEvent){e.preventDefault();write(draft);}
  return <section className="panel"><div className="panel-heading"><div><h2>{organization.name} · Полученные оплаты и вычеты</h2><p>Деньги, полученные организацией. Не изменяют счета за работу сотрудников.</p></div><button className="button secondary" onClick={onClose}>К организациям</button></div>
    <div className="padded"><div className="filters"><label>С даты<input aria-label="Полученные оплаты с даты" type="date" value={start} onChange={e=>{setStart(e.target.value);setOffset(0);}}/></label><label>По дату<input aria-label="Полученные оплаты по дату" type="date" value={end} onChange={e=>{setEnd(e.target.value);setOffset(0);}}/></label><button className="button" disabled={busy||!organization.active} onClick={()=>setDraft({date:today(),kind:"receipt",payer:"",reason:"",amount:""})}>+ Полученная оплата</button><button className="button secondary" disabled={busy||!organization.active} onClick={()=>setDraft({date:today(),kind:"deduction",payer:"",reason:"",amount:""})}>+ Вычет</button><button className="button secondary" onClick={()=>setRevision(v=>v+1)}>Обновить</button></div>
    {error&&<p role="alert" className="alert">{error}</p>}
    {draft&&<form onSubmit={submit}><h3>{draft.id?"Изменить операцию":draft.kind==="deduction"?"Новый вычет":"Новое поступление"}</h3><div className="form-grid"><label>Дата<input aria-label="Дата поступления" type="date" required value={draft.date} onChange={e=>setDraft({...draft,date:e.target.value})}/></label>{draft.kind==="deduction"?<label>Причина вычета<input aria-label="Причина вычета" required maxLength={500} value={draft.reason} onChange={e=>setDraft({...draft,reason:e.target.value})}/></label>:<label>От кого<input aria-label="Плательщик" required maxLength={200} value={draft.payer} onChange={e=>setDraft({...draft,payer:e.target.value})}/></label>}<label>Сумма, ₽<input aria-label="Сумма поступления" type="number" min="0.01" step="0.01" required value={draft.amount} onChange={e=>setDraft({...draft,amount:e.target.value})}/></label></div><button className="button" disabled={busy}>{draft.kind==="deduction"?"Сохранить вычет":"Сохранить поступление"}</button> <button type="button" className="button secondary" onClick={()=>setDraft(null)}>Отмена</button></form>}
    <p aria-live="polite">{loading?"Загрузка…":`Получено: ${rub(result.received_total)} · Вычтено: ${rub(result.deduction_total)} · Остаток за период: ${rub(result.total)} · ${result.count} записей`}</p>
    <div className="table-scroll"><table><thead><tr><th>Дата</th><th>От кого / причина</th><th>Операция</th><th>Сумма</th><th>Действия</th></tr></thead><tbody>{result.receipts.map((r:Item)=><tr key={r.id}><td>{dateLabel(r.date)}</td><td>{r.kind==="deduction"?r.reason:r.payer}</td><td>{r.kind==="deduction"?"Вычет":"Получено"}</td><td>{r.kind==="deduction"?"−":""}{rub(r.amount)}</td><td><button className="text-button" disabled={busy} onClick={()=>setDraft({...r})}>Изменить</button> <button className="text-button" onClick={()=>setHistory({...r,entity_type:"receipt"})}>История</button> <button className="text-button" disabled={busy} onClick={()=>{if(window.confirm("Удалить эту операцию? История сохранится."))write(r,true);}}>Удалить</button></td></tr>)}</tbody></table></div>
    {!loading&&!result.count&&<p>За выбранный период операций нет.</p>}
    <button className="button secondary" disabled={!offset||loading} onClick={()=>setOffset(v=>Math.max(0,v-50))}>Назад</button> <button className="button secondary" disabled={offset+50>=result.count||loading} onClick={()=>setOffset(v=>v+50)}>Далее</button>
    </div>{history&&<HistoryDialog record={history} data={data} onClose={()=>setHistory(null)}/>}</section>;
}
