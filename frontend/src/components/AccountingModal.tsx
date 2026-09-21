"use client";
import { dateLabel } from "./date-format";

import { FormEvent, useEffect, useRef, useState } from "react";
import type { Editor } from "./accounting-types";

export default function AccountingModal({editor,onClose,onSave,busy,error}: {editor:Editor;onClose:()=>void;onSave:(values:Record<string,any>)=>void;busy:boolean;error:string}) {
  const ref=useRef<HTMLDialogElement>(null);
  const [values,setValues]=useState(editor.values);
  useEffect(()=>{const node=ref.current;node?.showModal();return()=>node?.close();},[]);
  const change=(key:string,value:any)=>setValues(previous=>({...previous,[key]:value}));
  function recipient(id:number,enabled:boolean) {
    setValues(previous=>({...previous,recipients:enabled?[...(previous.recipients||[]),id]:(previous.recipients||[]).filter((value:number)=>value!==id),payment_recipients:enabled?(previous.payment_recipients||[]):(previous.payment_recipients||[]).filter((value:number)=>value!==id)}));
  }
  function paymentRecipient(id:number,enabled:boolean) {
    setValues(previous=>({...previous,payment_recipients:enabled?[...(previous.payment_recipients||[]),id]:(previous.payment_recipients||[]).filter((value:number)=>value!==id)}));
  }
  function submit(event:FormEvent) {
    event.preventDefault();
    const result={...values};
    editor.fields.forEach(field=>{
      if(field.type==="aliases")result[field.key]=typeof values[field.key]==="string"?values[field.key].split(",").map((value:string)=>value.trim()).filter(Boolean):values[field.key]||[];
      if(field.type==="recipients")result.payment_recipients=(values.payment_recipients||[]).filter((id:number)=>(values.recipients||[]).includes(id));
      if(field.type==="number" && !field.required && (result[field.key]==="" || result[field.key]===undefined))result[field.key]=null;
    });
    onSave(result);
  }
  return <dialog ref={ref} className="edit-dialog" onCancel={onClose}><form onSubmit={submit}>
    <div className="dialog-heading"><div><span className="eyebrow">СВОД / НАСТРОЙКА</span><h2>{editor.title}</h2></div><button type="button" className="icon-button" onClick={onClose} title="Закрыть" aria-label="Закрыть">×</button></div>
    {error&&<div role="alert" className="alert">{error}</div>}
    <div className="form-grid">{editor.fields.map(field=>field.type==="recipients"?<fieldset key={field.key} className="recipient-settings full"><legend>{field.label}</legend><small>{field.hint}</small>{field.options?.length?field.options.map(option=>{
      const id=Number(option.value),selected=(values.recipients||[]).includes(id);
      return <div className="recipient-choice" key={id}><label><input type="checkbox" checked={selected} onChange={e=>recipient(id,e.target.checked)}/><span>{option.label}</span></label>{selected&&<label className="payment-choice"><input type="checkbox" checked={(values.payment_recipients||[]).includes(id)} onChange={e=>paymentRecipient(id,e.target.checked)}/><span>Подтверждение оплаты</span></label>}</div>;
    }):<p>Получатели пока не подключены. Попросите их написать боту /start.</p>}<p>Получатели с флажком «Подтверждение оплаты» увидят кнопку «Оплатил» в Telegram. Счёт будет ожидать оплаты, пока любой из них не подтвердит её. Подтверждение общее для организации; закрытый счёт останется в истории оплат.</p></fieldset>:<label key={field.key} className={field.type==="textarea"||field.type==="multi"?"full":""}>
      <span>{field.label}{field.required&&" *"}</span>
      {field.type==="checkbox"?<input type="checkbox" checked={!!values[field.key]} onChange={e=>change(field.key,e.target.checked)}/>:field.type==="multi"?<select multiple value={(values[field.key]||[]).map(String)} onChange={e=>change(field.key,Array.from(e.target.selectedOptions,option=>Number(option.value)))}>{field.options?.map(option=><option value={option.value} key={option.value}>{option.label}</option>)}</select>:field.type==="select"?<select required={field.required} value={values[field.key]??""} onChange={e=>change(field.key,e.target.value)}><option value="">Не выбрано</option>{field.options?.map(option=><option value={option.value} key={option.value}>{option.label}</option>)}</select>:field.type==="textarea"?<textarea required={field.required} value={values[field.key]||""} onChange={e=>change(field.key,e.target.value)} rows={3}/>:<input required={field.required} type={field.type==="aliases"?"text":field.type||"text"} step={field.type==="number"?"any":undefined} value={field.type==="aliases"&&Array.isArray(values[field.key])?values[field.key].join(", "):values[field.key]??""} onChange={e=>change(field.key,e.target.value)}/>}
      {field.type==="date"&&values[field.key]&&<small>{dateLabel(values[field.key])}</small>}{field.hint&&<small>{field.hint}</small>}
    </label>)}</div>
    <footer><button type="button" className="button secondary" onClick={onClose}>Отмена</button><button className="button" disabled={busy}>{busy?"Сохраняем…":"Сохранить"}</button></footer>
  </form></dialog>;
}
