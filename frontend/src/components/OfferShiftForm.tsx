"use client";
import {useRef,useState} from "react";
import {dateLabel} from "./date-format";
import {newOfferRequestKey} from "./OfferServiceForm";

export type ShiftPart = {id?:number;organization:number;organization_name?:string;start_time:string|null;end_time:string|null;intervals?:unknown[]};
type Row = {organization:string;start_time:string;end_time:string;files:File[]};
export default function OfferShiftForm({organizations,busy,onCancel,onSave,initial}:{
  organizations:{id:number;name:string}[];busy:boolean;onCancel:()=>void;
  initial?:{date:string|null;comment:string;parts?:ShiftPart[]};
  onSave:(data:Record<string,unknown>,files:{organization:number;file:File}[],key:string)=>Promise<void>;
}) {
  const blank=():Row=>({organization:"",start_time:"",end_time:"",files:[]});
  const [rows,setRows]=useState<Row[]>(initial?.parts?.length?initial.parts.map(p=>({organization:String(p.organization),start_time:p.start_time?.slice(0,5)||"",end_time:p.end_time?.slice(0,5)||"",files:[]})):[blank()]);
  const [date,setDate]=useState(initial?.date||""),[comment,setComment]=useState(initial?.comment||""),[error,setError]=useState("");
  const key=useRef<string>("");
  function change(index:number,patch:Partial<Row>){setRows(items=>items.map((r,n)=>n===index?{...r,...patch}:r));}
  return <section className="panel"><h2>{initial?"Исправить студии":"Предложить смену"}</h2><form onSubmit={async e=>{e.preventDefault();setError("");
    if(new Set(rows.map(r=>r.organization)).size!==rows.length){setError("Выберите разные студии.");return;}
    if(rows.some(r=>r.start_time>=r.end_time)){setError("Окончание должно быть позже начала каждой студии.");return;}
    if(rows.reduce((n,r)=>n+r.files.length,0)>10||rows.some(r=>r.files.some(f=>f.size>10*1024*1024))){setError("До 10 фотографий, каждая не более 10 МБ.");return;}
    key.current ||= newOfferRequestKey();
    await onSave({kind:"shift",date,comment,parts:rows.map((r,n)=>({id:initial?.parts?.[n]?.id,organization:Number(r.organization),start_time:r.start_time,end_time:r.end_time,
      intervals:r.start_time===initial?.parts?.[n]?.start_time?.slice(0,5)&&r.end_time===initial?.parts?.[n]?.end_time?.slice(0,5)?initial?.parts?.[n]?.intervals:[]}))},rows.flatMap(r=>r.files.map(file=>({organization:Number(r.organization),file}))),key.current);
  }}>
    <label>Дата<input required type="date" value={date} disabled={busy} onChange={e=>setDate(e.target.value)}/>{date&&<small>{dateLabel(date)}</small>}</label>
    {rows.map((r,n)=><fieldset key={n} disabled={busy}><legend>Студия {n+1}</legend><div className="offer-fields">
      <label>Организация<select required value={r.organization} onChange={e=>change(n,{organization:e.target.value})}><option value="">Выберите</option>{organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
      <label>Начало<input required type="time" value={r.start_time} onChange={e=>change(n,{start_time:e.target.value})}/></label>
      <label>Окончание<input required type="time" value={r.end_time} onChange={e=>change(n,{end_time:e.target.value})}/></label>
      {!initial&&<label>Фотографии студии<input type="file" multiple accept="image/png,image/jpeg,image/webp" onChange={e=>change(n,{files:Array.from(e.target.files||[])})}/></label>}
    </div>{!initial&&n===1&&<button type="button" className="text-button" onClick={()=>setRows(items=>items.slice(0,1))}>Убрать вторую студию</button>}</fieldset>)}
    {!initial&&rows.length===1&&<button type="button" disabled={busy} className="button secondary" onClick={()=>setRows(items=>[...items,blank()])}>Добавить вторую студию</button>}
    <label>Комментарий<textarea value={comment} disabled={busy} onChange={e=>setComment(e.target.value)} maxLength={4000}/></label>
    <p className="muted">Один сотрудник берёт и освобождает всё предложение. Фактическую работу каждой студии вносят в журнал отдельно.</p>
    {error&&<p role="alert">{error}</p>}<div className="actions"><button className="button" disabled={busy}>Сохранить для проверки</button><button type="button" disabled={busy} className="button secondary" onClick={onCancel}>Закрыть</button></div>
  </form></section>;
}
