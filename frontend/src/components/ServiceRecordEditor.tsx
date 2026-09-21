"use client";
import { dateLabel } from "./date-format";

import { FormEvent, useEffect, useRef, useState } from "react";
import SharedShiftDetails from "./SharedShiftDetails";
import type { Bootstrap, Editor, Item } from "./accounting-types";

const rub = (value: unknown) => new Intl.NumberFormat("ru-RU", {style:"currency",currency:"RUB",maximumFractionDigits:2}).format(Number(value || 0));
const timeValue = (value: unknown) => value ? String(value).slice(0,5) : "";
const optionalId = (value: unknown) => value === "" || value == null ? null : Number(value);

// Edits are patches: displaying a saved amount must never submit it as an override.
export function serviceRecordPayload(values: Record<string,any>, original: Record<string,any>, service?: Item) {
  const mode = service?.input_type || original.input_type;
  const normalized: Record<string,any> = {
    kind:"service",date:values.date,organization:optionalId(values.organization),employee:optionalId(values.employee),
    service:optionalId(values.service),description:values.description || "",
  };
  if (mode === "time") {
    normalized.start_time = timeValue(values.start_time) || null;
    normalized.end_time = timeValue(values.end_time) || null;
    if (!normalized.start_time && !normalized.end_time) normalized.units = values.units === "" || values.units == null ? null : values.units;
  } else if (mode === "quantity") normalized.units = values.units === "" || values.units == null ? null : values.units;
  if (mode === "amount") normalized.amount = values.amount;
  if (original.id && Number(values.service) !== Number(original.service)) {
    if (mode !== "time") {normalized.start_time = null;normalized.end_time = null;}
    if (!["time","quantity"].includes(mode)) normalized.units = 1;
  }
  if (!original.id) return {...normalized,correction:!!values.correction};
  const payload: Record<string,any> = {id:original.id,correction:!!values.correction};
  for (const [key,value] of Object.entries(normalized)) {
    let old = original[key];
    if (["start_time","end_time"].includes(key)) old = timeValue(old) || null;
    if (["organization","employee","service"].includes(key)) old = optionalId(old);
    if (key === "description") old = old || "";
    if (["units","amount"].includes(key) && value !== null && value !== "" && Number(value) === Number(old)) continue;
    if (value !== old) payload[key] = value;
  }
  return payload;
}

export default function ServiceRecordEditor({editor,data,busy,error,onClose,onSave}: {editor:Editor;data:Bootstrap|null;busy:boolean;error:string;onClose:()=>void;onSave:(values:Record<string,any>)=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [values,setValues] = useState<Record<string,any>>({...editor.values,start_time:timeValue(editor.values.start_time),end_time:timeValue(editor.values.end_time)});
  const [preview,setPreview] = useState<any>(null);
  const [previewError,setPreviewError] = useState("");
  const [previewKey,setPreviewKey] = useState("");
  const sequence = useRef(0);
  const service = data?.services.find(item=>item.id===Number(values.service));
  const mode = service?.input_type || editor.values.input_type;
  const payload = serviceRecordPayload(values,editor.values,service);
  const serialized = JSON.stringify(payload);
  const hasInterval = !!(values.start_time || values.end_time);
  const incomplete = !values.date || !values.organization || !values.service ||
    (mode==="time" && hasInterval && (!values.start_time || !values.end_time)) ||
    (["time","quantity"].includes(mode) && !hasInterval && !(Number(values.units)>0)) ||
    (mode==="amount" && (values.amount==="" || values.amount==null));
  const ready = !incomplete && previewKey===serialized && !!preview;

  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>{node?.close();};},[]);
  useEffect(()=>{
    const current=++sequence.current;
    const controller=new AbortController();
    setPreview(null);setPreviewError("");setPreviewKey("");
    if(incomplete)return()=>controller.abort();
    const timer=setTimeout(async()=>{
      try {
        const response=await fetch("/api/shifts/ledger/record-preview/",{method:"POST",headers:{"Content-Type":"application/json"},body:serialized,signal:controller.signal,cache:"no-store"});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error || "Не удалось рассчитать стоимость");
        if(controller.signal.aborted || current!==sequence.current)return;
        setPreview(result.record);setPreviewKey(serialized);
      } catch(error) {
        if(!controller.signal.aborted && current===sequence.current)setPreviewError(error instanceof Error?error.message:"Не удалось рассчитать стоимость");
      }
    },250);
    return()=>{clearTimeout(timer);controller.abort();};
  },[serialized,incomplete]);

  function change(key:string,value:any) {
    setValues(previous=>{
      const next={...previous,[key]:value};
      if(key==="service") {
        const selected=data?.services.find(item=>item.id===Number(value));
        next.start_time="";next.end_time="";next.units=1;next.amount="";
        if(!next.organization && selected?.default_organization)next.organization=selected.default_organization;
      }
      return next;
    });
  }
  function submit(event:FormEvent) {event.preventDefault();if(ready&&!busy)onSave(payload);}
  const organizations=(data?.organization_list||[]).filter(item=>item.active||item.id===Number(editor.values.organization));
  const employees=(data?.employees||[]).filter(item=>item.active||item.id===Number(editor.values.employee));
  const services=(data?.services||[]).filter(item=>(item.active && item.frequency==="entry")||item.id===Number(editor.values.service));
  return <dialog ref={dialog} className="edit-dialog" onCancel={onClose}><form onSubmit={submit}>
    <div className="dialog-heading"><div><span className="eyebrow">СВОД / ЖУРНАЛ</span><h2>{editor.title}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть">×</button></div>
    {error&&<div role="alert" className="alert">{error}</div>}
    <div className="form-grid">
      <label><span>Дата *</span><input aria-label="Дата" type="date" required value={values.date||""} onChange={e=>change("date",e.target.value)}/>{values.date&&<small>{dateLabel(values.date)}</small>}</label>
      <label><span>Организация *</span><select aria-label="Организация услуги" required value={values.organization||""} onChange={e=>change("organization",e.target.value)}><option value="">Не выбрано</option>{organizations.map(item=><option key={item.id} value={item.id}>{item.name}{!item.active&&" (архив)"}</option>)}</select></label>
      <label><span>Исполнитель</span><select aria-label="Исполнитель" value={values.employee||""} onChange={e=>change("employee",e.target.value)}><option value="">Без исполнителя</option>{employees.map(item=><option key={item.id} value={item.id}>{item.name}{!item.active&&" (архив)"}</option>)}</select></label>
      <label><span>Услуга *</span><select aria-label="Услуга" required value={values.service||""} onChange={e=>change("service",e.target.value)}><option value="">Не выбрано</option>{services.map(item=><option key={item.id} value={item.id}>{item.name}{!item.active&&" (архив)"}</option>)}</select></label>
      {mode==="time"&&<>
        <label><span>Начало</span><input aria-label="Начало" type="time" value={values.start_time||""} onChange={e=>change("start_time",e.target.value)}/></label>
        <label><span>Конец</span><input aria-label="Конец" type="time" value={values.end_time||""} onChange={e=>change("end_time",e.target.value)}/></label>
      </>}
      {["time","quantity"].includes(mode)&&<label><span>{mode==="time"?"Часы":"Количество *"}</span><input aria-label={mode==="time"?"Часы":"Количество"} type="number" step="any" min="0.01" required={!hasInterval} readOnly={mode==="time"&&hasInterval} value={mode==="time"&&hasInterval?(ready?preview.units:""):values.units??""} onChange={e=>change("units",e.target.value)}/><small>{mode==="time"?(hasInterval?"Часы рассчитываются по интервалу, в том числе через полночь.":"Укажите интервал или введите часы вручную."):"Стоимость рассчитывается по тарифу организации."}</small></label>}
      {mode==="amount"&&<label><span>Готовая сумма, ₽ *</span><input aria-label="Готовая сумма, ₽" type="number" step="any" required value={values.amount??""} onChange={e=>change("amount",e.target.value)}/></label>}
      {service&&<div className="service-preview full" aria-live="polite" aria-busy={!incomplete&&!ready&&!previewError}>
        <span>Стоимость по тарифу</span>
        {previewError?<p role="alert" className="inline-error">{previewError}</p>:ready?<><strong>{rub(preview.amount)}</strong>{preview.daily_group&&<small>Итого за смену: {rub(preview.daily_group.total)} · {preview.daily_group.hours} ч. Доля записи учитывает общий минимум и максимум.</small>}<SharedShiftDetails group={preview.daily_group}/>{(preview.allocation_changes||[]).filter((item:Item)=>item.id!==editor.values.id).map((item:Item)=><small key={item.id}>{item.employee_name||"Без исполнителя"}: {rub(item.before)} → {rub(item.after)}</small>)}</>:<p>{incomplete?"Заполните организацию, услугу и объём работы для расчёта.":"Рассчитываем…"}</p>}
      </div>}
      <label className="full"><span>Комментарий</span><textarea aria-label="Комментарий" value={values.description||""} onChange={e=>change("description",e.target.value)} rows={3}/><small>Изменение комментария сохраняет ранее рассчитанную сумму.</small></label>
      <label className="full"><span>Корректировка подтверждённой работы или смены</span><input type="checkbox" checked={!!values.correction} onChange={e=>change("correction",e.target.checked)}/><small>Разрешает изменение всех затронутых долей смены. Отправленный Excel сохранится; после исправления создайте новую версию счёта.</small></label>
    </div><footer><button type="button" className="button secondary" onClick={onClose}>Отмена</button><button className="button" disabled={busy||!ready}>{busy?"Сохраняем…":"Сохранить"}</button></footer>
  </form></dialog>;
}
