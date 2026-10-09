"use client";
import {useEffect,useRef,useState} from "react";
import {dateLabel} from "./date-format";
import type {Item} from "./accounting-types";

export default function OverlapReview({record,onClose,onSaved}:{record:Item;onClose:()=>void;onSaved:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [components,setComponents]=useState<Item[]>([]),[halves,setHalves]=useState<Record<string,number[]>>({});
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[correction,setCorrection]=useState(false);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>node?.close();},[]);
  useEffect(()=>{const controller=new AbortController();setLoading(true);setError("");
    fetch(`/api/shifts/ledger/overlap-decisions/?employee=${record.employee}&date=${record.date}`,{signal:controller.signal,cache:"no-store"}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error);if(!controller.signal.aborted){setComponents(d.components);setHalves(Object.fromEntries(d.components.map((c:Item)=>[c.fingerprint,c.half_organizations||[]])));}}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});return()=>controller.abort();
  },[record.employee,record.date,revision]);
  async function save(component:Item){setBusy(true);setError("");try{
    const response=await fetch("/api/shifts/ledger/overlap-decisions/",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...component,half_organizations:halves[component.fingerprint]||[],correction})});
    const result=await response.json();if(!response.ok)throw Error(result.error);onSaved();onClose();
  }catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить");}finally{setBusy(false);}}
  return <dialog ref={dialog} className="edit-dialog" onCancel={onClose}><div className="dialog-heading"><h2>Пересечение студий · {dateLabel(record.date)}</h2><button className="icon-button" onClick={onClose} aria-label="Закрыть">×</button></div><div className="padded">
    {error&&<p role="alert" className="alert">{error}</p>}{loading?<p>Загрузка…</p>:<>
    <p>{record.employee_name}. Отметьте студии, пересекающиеся часы которых оплачиваются наполовину. Можно выбрать одну, обе или ни одной. Фиксированные тарифы и доплаты сохраняются полностью.</p>
    {!components.length&&<p>Нет расчётов, требующих выбора. Если не хватает времени смены, укажите его в карточке записи.</p>}
    {components.map(c=><section key={c.fingerprint}>{c.organizations.map((o:Item)=><label className="organization-row" key={o.id}><input type="checkbox" checked={(halves[c.fingerprint]||[]).includes(o.id)} onChange={e=>setHalves(prev=>({...prev,[c.fingerprint]:e.target.checked?[...(prev[c.fingerprint]||[]),o.id]:(prev[c.fingerprint]||[]).filter(id=>id!==o.id)}))}/><span>{o.name} · {o.hours} ч · база {o.base} ₽ — половинная оплата пересекающихся часов</span></label>)}<button className="button" disabled={busy} onClick={()=>save(c)}>Подтвердить выбранный расчёт</button></section>)}
    <label><input type="checkbox" checked={correction} onChange={e=>setCorrection(e.target.checked)}/> Корректировка подтверждённых работ</label></>}
    <button className="button secondary" disabled={busy||loading} onClick={()=>setRevision(v=>v+1)}>Обновить расчёт</button>
  </div></dialog>;
}
