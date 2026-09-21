"use client";
import { useState } from "react";
import { dateLabel } from "./date-format";

export type OfferCatalogService = {id:number;name:string;input_type:string;active:boolean;deleted_at?:string|null;default_organization?:number|null};
type Values = {service?:number|null;service_name?:string;input_type?:string;organization?:number|null;date?:string|null;start_time?:string|null;end_time?:string|null;units?:string|null;amount?:string|null;comment?:string};
export default function OfferServiceForm({services,organizations,busy,initial,onSave,onCancel}:{services:OfferCatalogService[];organizations:{id:number;name:string}[];busy:boolean;initial?:Values;onSave:(data:Record<string,unknown>,files:File[],key:string)=>Promise<void>;onCancel:()=>void}) {
  const [source,setSource]=useState(initial?.service?"catalog":initial?"free":"catalog");
  const [values,setValues]=useState({service:String(initial?.service||""),service_name:initial?.service_name||"",input_type:initial?.input_type||"time",organization:String(initial?.organization||""),date:initial?.date||"",start_time:initial?.start_time?.slice(0,5)||"",end_time:initial?.end_time?.slice(0,5)||"",units:initial?.units||"",amount:initial?.amount||"",comment:initial?.comment||""});
  const [files,setFiles]=useState<File[]>([]),[fileError,setFileError]=useState("");
  const [requestKey] = useState(()=>globalThis.crypto.randomUUID());
  const selected=services.find(s=>String(s.id)===values.service);
  const mode=initial?.input_type||(source==="catalog"?selected?.input_type:values.input_type);
  const interval=!!(values.start_time||values.end_time);
  const change=(key:string,value:string)=>setValues(v=>({...v,[key]:value}));
  return <section className="panel"><h2>{initial?"Исправить услугу":"Предложить услугу"}</h2><form onSubmit={e=>{e.preventDefault();if(fileError)return;void onSave({...values,service:source==="catalog"?Number(values.service):null,input_type:mode,organization:Number(values.organization),start_time:mode==="time"?values.start_time:null,end_time:mode==="time"?values.end_time:null},files,requestKey);}}>
    <div className="offer-fields">
      {!initial&&<label>Источник<select value={source} onChange={e=>setSource(e.target.value)}><option value="catalog">Из справочника</option><option value="free">Свободная услуга</option></select></label>}
      {source==="catalog"?<label>Услуга<select required disabled={!!initial} value={values.service} onChange={e=>{const service=services.find(s=>String(s.id)===e.target.value);setValues(v=>({...v,service:e.target.value,organization:v.organization||String(service?.default_organization||""),start_time:"",end_time:"",units:"",amount:""}));}}><option value="">Выберите услугу</option>{services.filter(s=>(s.active&&!s.deleted_at)||s.id===initial?.service).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>:<><label>Название услуги<input required maxLength={160} disabled={!!initial} value={values.service_name} onChange={e=>change("service_name",e.target.value)}/></label><label>Формат<select disabled={!!initial} value={values.input_type} onChange={e=>change("input_type",e.target.value)}><option value="time">Время или часы</option><option value="quantity">Количество</option><option value="amount">Готовая сумма</option><option value="mark">Отметка</option></select></label></>}
      <label>Организация<select required value={values.organization} onChange={e=>change("organization",e.target.value)}><option value="">Выберите</option>{organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
      <label>Дата<input type="date" required value={values.date} onChange={e=>change("date",e.target.value)}/>{values.date&&<small>{dateLabel(values.date)}</small>}</label>
      {mode==="time"&&<><label>Начало<input type="time" required={interval} value={values.start_time} onChange={e=>change("start_time",e.target.value)}/></label><label>Окончание<input type="time" required={interval} value={values.end_time} onChange={e=>change("end_time",e.target.value)}/></label></>}
      {(mode==="quantity"||(mode==="time"&&!interval))&&<label>{mode==="time"?"Часы":"Количество"}<input type="number" min="0.01" step="0.01" required value={values.units} onChange={e=>change("units",e.target.value)}/></label>}
      {mode==="amount"&&<label>Готовая сумма, ₽<input type="number" min="0" step="0.01" required value={values.amount} onChange={e=>change("amount",e.target.value)}/></label>}
    </div><label>Комментарий<textarea maxLength={4000} value={values.comment} onChange={e=>change("comment",e.target.value)}/></label>
    {!initial&&<label>Фотографии (необязательно)<input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={e=>{const next=Array.from(e.target.files||[]);setFileError(next.length>10||next.some(f=>f.size>10*1024*1024)?"До 10 изображений, каждое не более 10 МБ.":"");setFiles(next);}}/></label>}
    {fileError&&<p role="alert">{fileError}</p>}<p className="muted">{interval?"Завершение по окончании интервала.":"Без точного времени предложение закроется в конце указанного дня."} Начисления и фактическая работа вносятся отдельно.</p>
    <div className="actions"><button className="button" disabled={busy||!!fileError}>{busy?"Сохраняем…":initial?"Сохранить":"Создать для проверки"}</button><button type="button" className="button secondary" disabled={busy} onClick={onCancel}>Закрыть</button></div>
  </form></section>;
}
