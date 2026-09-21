"use client";
import { dateLabel, dateTimeLabel } from "./date-format";
import OfferServiceForm, { type OfferCatalogService } from "./OfferServiceForm";
/* eslint-disable @next/next/no-img-element -- Private authenticated images must bypass the public image optimizer. */

import { useCallback, useEffect, useRef, useState } from "react";

const base = "/api/shifts/ledger/";
type Option = {id:number;name:string};
type Config = {enabled:boolean;chat_id:number|null;thread_id:number|null;claimed_thread_id?:number|null;released_thread_id?:number|null;coordinator:number|null;auto_publish:boolean;
  services?:OfferCatalogService[];organizations:Option[];employees:Option[];contacts:(Option & {user_id:number})[];workers:{name:string;updated_at:string;detail:string}[]};
type Profile = {id:number;organization:number;rooms:{name:string;aliases:string[]}[];publishers:number[];active:boolean;
  confirmed:boolean;suggestions:string[];processing:boolean;header_url:string|null;error:string};
type Interval = {room?:string;start:string|null;end:string|null;image_index?:number};
type Offer = {id:number;version:number;state:string;state_label:string;organization:number|null;organization_name:string;date:string|null;
  kind:string;service:number|null;service_name:string;input_type:string;units:string|null;amount:string|null;
  update_target:number|null;update_mode:string;update_preview?:{stale:boolean;candidates:Offer[];before:Offer|null;intervals:Interval[]|null}|null;
  start_time:string|null;end_time:string|null;comment:string;sender_name:string;employee_name:string;error:string;duplicate_of:number|null;
  intervals:Interval[];questions:{key:string;label:string;type:string}[];
  images?:{id:number;url:string|null;purged_at:string|null;active?:boolean;recognized:{date?:string;headers?:string[]}}[];
  deliveries?:{id:number;purpose:string;state:string;error:string;recipient:number;message_ids:number[];edit_state:string|null;edit_error:string|null;delete_requested?:boolean;deleted_at?:string|null}[];
  jobs?:{id:number;state:string;attempts:number;elapsed_ms:number|null;error:string}[];
  history?:{id:number;created_at:string;action:string;actor:string;diff:unknown}[]};
const states:Record<string,string> = {collecting:"Получаем фото",processing:"Распознаём",needs_input:"Нужно уточнить",review:"Подтвердить",publishing:"Публикуется",open:"Свободна",claimed:"Занята",completed:"Завершена",expired:"Истекла",cancelled:"Отменена",failed:"Ошибка",duplicate:"Повтор"};
Object.assign(states,{composing:"Заполняется",update_review:"Проверить изменения",applied:"Обновление применено"});
const actions:Record<string,string> = {image_received:"Получено изображение",recognized:"Распознано",clarified:"Данные исправлены",publish_requested:"Запрошена публикация",published:"Опубликовано",claimed:"Сотрудник назначен",released:"Сотрудник снят",closed:"Завершено по времени",cancelled:"Отменено",edited:"Изменено",split:"Альбом разделён",split_created:"Создано из альбома",delivered:"Сообщение доставлено",delivery_unknown:"Доставка не подтверждена",delivery_reconciled:"Доставка проверена",recognition_retry:"Повтор распознавания",recognition_failed:"Ошибка распознавания",images_purged:"Изображения удалены",duplicate_detected:"Найден повтор"};

async function api<T>(path:string, data?:unknown):Promise<T> {
  const form = data instanceof FormData;
  const response = await fetch(base + path, {method:data === undefined ? "GET" : "POST", cache:"no-store",
    headers:data === undefined || form ? undefined : {"Content-Type":"application/json"},
    body:data === undefined ? undefined : form ? data : JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Не удалось выполнить действие");
  return result;
}
const time = (value:string|null) => value?.slice(0,5) || "?";
const day = (value:string|null) => dateLabel(value, "Дата не определена");
const intervalText = (items:Interval[]) => items.map(i=>`${i.start||"?"}–${i.end||"?"}`).join(", ");
export function parseIntervals(value:string):Interval[] {
  const parts = value.split(/[,;\n]+/).filter(p=>p.trim());
  if (!parts.length) throw new Error("Укажите хотя бы одну запись.");
  return parts.map(part=>{
    const found = part.trim().match(/^(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})$/);
    if (!found) throw new Error("Формат записей: 13:00–16:00, 16:30–20:00.");
    return {start:found[1].padStart(5,"0"),end:found[2].padStart(5,"0")};
  });
}

export default function OffersConsole({onOpenSettings}:{onOpenSettings?:()=>void}) {
  const [tab,setTab] = useState("offers");
  const [creating,setCreating] = useState(false),[editingService,setEditingService]=useState(false);
  const [config,setConfig] = useState<Config|null>(null);
  const [profiles,setProfiles] = useState<Profile[]>([]);
  const [items,setItems] = useState<Offer[]>([]);
  const [selected,setSelected] = useState<number|null>(null);
  const [detail,setDetail] = useState<Offer|null>(null);
  const [filter,setFilter] = useState({state:"",organization:"",date:"",kind:"",service:"",employee:"",date_from:"",date_to:"",search:"",ordering:"-created_at"});
  const [offset,setOffset] = useState(0), [count,setCount] = useState(0);
  const [error,setError] = useState(""), [notice,setNotice] = useState(""), [busy,setBusy] = useState(false);
  const requestSequence = useRef(0);
  const invalidate = useCallback(()=>{requestSequence.current++;},[]);
  const reload = useCallback(async()=>{
    const sequence = ++requestSequence.current;
    const [c,p,list,d] = await Promise.all([api<Config>("offer-config/"),api<{items:Profile[]}>("offer-profiles/"),
      api<{items:Offer[];count:number}>("offers/?"+new URLSearchParams({...filter,section:tab==="cancelled"?"cancelled":"main",offset:String(offset)})),
      selected ? api<Offer>(`offers/${selected}/`) : Promise.resolve(null)]);
    if (sequence !== requestSequence.current) return;
    setConfig(c);setProfiles(p.items);setItems(list.items);setCount(list.count);setDetail(d);
  },[filter,offset,selected,tab]);
  useEffect(()=>{let active=true; const load=()=>reload().catch(e=>{if(active)setError(e.message);});void load(); const timer=setInterval(load,5000);return()=>{active=false;invalidate();clearInterval(timer);};},[reload,invalidate]);
  const run = async (operation:()=>Promise<unknown>, message="Сохранено")=>{
    setBusy(true);setError("");setNotice("");
    try {await operation();await reload();setNotice(message);} catch(e){setError(e instanceof Error ? e.message : "Ошибка");} finally{setBusy(false);}
  };
  const act = (action:string,data:Record<string,unknown>={})=> run(()=>api(`offers/${detail!.id}/${action}/`,{version:detail!.version,...data}));
  const switchList = (value:string)=>{setTab(value);setOffset(0);setFilter(f=>({...f,state:""}));setSelected(null);setDetail(null);setEditingService(false);setCreating(false);};
  return <div className="offers-console">
    <div className="tabs"><button className={tab==="offers"?"active":""} onClick={()=>switchList("offers")}>Предложения</button><button className={tab==="cancelled"?"active":""} onClick={()=>switchList("cancelled")}>Отменённые</button><button className={tab==="profiles"?"active":""} onClick={()=>setTab("profiles")}>Залы и разрешения</button><button className={tab==="config"?"active":""} onClick={()=>setTab("config")}>Обработка</button></div>
    {error&&<div role="alert" className="alert">{error}</div>}{notice&&<div role="status" className="notice">{notice}</div>}
    {!config&&<p>Загружаем предложения…</p>}
    {config&&["offers","cancelled"].includes(tab)&&<>
      {tab==="offers"&&<button className="button" onClick={()=>setCreating(true)}>+ Предложить услугу</button>}
      {creating&&<OfferServiceForm services={config.services||[]} organizations={config.organizations} busy={busy} onCancel={()=>setCreating(false)} onSave={(data,files,key)=>run(async()=>{
        let offer=await api<Offer>("offers/",{...data,request_key:key});
        for(let i=0;i<files.length;i++){const form=new FormData();form.append("image",files[i]);form.append("version",String(offer.version));form.append("message_id",String(-i-1));offer=await api<Offer>(`offers/${offer.id}/image/`,form);}
        setCreating(false);setSelected(offer.id);setDetail(offer);
      },"Предложение сохранено. Проверьте и подтвердите публикацию.")}/>}
      <p className="muted">Для смены отправьте скриншот или альбом боту. Для другой услуги используйте кнопку выше или команду /offer. Предложения не начисляют оплату: фактическую работу вносят в журнал отдельно.</p>
      <div className="filters"><label>Статус<select value={filter.state} onChange={e=>{setFilter({...filter,state:e.target.value});setOffset(0);}}><option value="">Все</option>{Object.entries(states).filter(([v])=>tab==="cancelled"?v==="cancelled":v!=="cancelled").map(([v,n])=><option key={v} value={v}>{n}</option>)}</select></label>
        <label>Организация<select value={filter.organization} onChange={e=>{setFilter({...filter,organization:e.target.value});setOffset(0);}}><option value="">Все</option>{config.organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
        <label>Тип<select value={filter.kind} onChange={e=>{setFilter({...filter,kind:e.target.value});setOffset(0);}}><option value="">Все</option><option value="shift">Смены</option><option value="service">Услуги</option></select></label>
        <label>Услуга<select value={filter.service} onChange={e=>{setFilter({...filter,service:e.target.value});setOffset(0);}}><option value="">Все</option><option value="free">Свободные услуги</option>{config.services?.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Название<input type="search" value={filter.search} onChange={e=>{setFilter({...filter,search:e.target.value});setOffset(0);}}/></label>
        <label>Сотрудник<select value={filter.employee} onChange={e=>{setFilter({...filter,employee:e.target.value});setOffset(0);}}><option value="">Все</option>{config.employees.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
        <label>С даты<input type="date" value={filter.date_from} onChange={e=>{setFilter({...filter,date_from:e.target.value});setOffset(0);}}/></label>
        <label>По дату<input type="date" value={filter.date_to} onChange={e=>{setFilter({...filter,date_to:e.target.value});setOffset(0);}}/></label>
        <label>Сортировка<select value={filter.ordering} onChange={e=>{setFilter({...filter,ordering:e.target.value});setOffset(0);}}><option value="-created_at">Сначала новые</option><option value="created_at">Сначала старые</option><option value="date">Дата: по возрастанию</option><option value="-date">Дата: по убыванию</option><option value="service_name">Название: А–Я</option><option value="-service_name">Название: Я–А</option></select></label><button className="button secondary" onClick={()=>void run(reload,"")}>Обновить</button></div>
      <section className="panel"><div className="table-scroll"><table><thead><tr><th>Предложение</th><th>Организация</th><th>Услуга</th><th>Время / объём</th><th>Статус</th><th>Сотрудник</th></tr></thead><tbody>{items.map(o=><tr key={o.id}><td><button className="text-button" onClick={()=>{setSelected(o.id);setDetail(null);setEditingService(false);}}>№{o.id} · {day(o.date)}</button><small>{o.sender_name}</small></td><td>{o.organization_name||"Нужно уточнить"}</td><td>{o.kind==="service"?o.service_name:"Смена"}</td><td>{o.start_time?`${time(o.start_time)}–${time(o.end_time)}`:"В течение дня"}{o.units&&<small>{o.units} {o.input_type==="time"?"ч.":"ед."}</small>}{o.amount!==null&&o.amount!==undefined&&<small>{o.amount} ₽</small>}</td><td>{o.state_label}</td><td>{o.employee_name||"—"}</td></tr>)}</tbody></table></div>{!items.length&&<p className="empty">Предложений с такими условиями пока нет.</p>}
        <div className="actions"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Назад</button><span>{count ? offset+1 : 0}–{Math.min(count,offset+50)} из {count}</span><button disabled={offset+50>=count} onClick={()=>setOffset(offset+50)}>Далее</button></div></section>
      {selected&&!detail&&<p>Открываем заявку №{selected}…</p>}
      {detail&&<section className="panel offer-detail"><div className="panel-heading"><h2>№{detail.id} · {detail.state_label}</h2><button className="text-button" onClick={()=>{setSelected(null);setDetail(null);}}>Закрыть</button></div>
        <p><strong>{detail.organization_name||"Организация не определена"} · {day(detail.date)} · {detail.start_time?`${time(detail.start_time)}–${time(detail.end_time)}`:"В течение дня"}</strong></p>{detail.comment&&<p className="offer-comment">{detail.comment}</p>}{detail.employee_name&&<p>Сотрудник: {detail.employee_name}</p>}{detail.error&&<p role="alert">{detail.error}</p>}
        <p><strong>{detail.kind==="service"?detail.service_name:"Смена"}</strong>{detail.units&&` · ${detail.units} ${detail.input_type==="time"?"ч.":"ед."}`}{detail.amount!==null&&detail.amount!==undefined&&` · ${detail.amount} ₽`}</p><div className="offer-images">{detail.images?.map(i=><figure key={i.id}>{i.url?<a href={i.url} target="_blank" rel="noreferrer"><img src={i.url} alt={`Скриншот ${i.id}`} loading="lazy"/></a>:<p>Изображение удалено по сроку хранения</p>}<figcaption>Фото {i.id}{i.active===false?" · прежняя версия":""}{i.recognized.date?` · ${day(i.recognized.date)}`:""}<br/>{i.recognized.headers?.join(" · ")}</figcaption></figure>)}</div>
        {detail.kind!=="service"&&<><h3>Найденные записи</h3>{detail.intervals.length?<ol>{detail.intervals.map((i,n)=><li key={n}>{i.room||"Зал"}: {i.start||"?"}–{i.end||"?"}</li>)}</ol>:<p>Записи пока не распознаны.</p>}</>}
        {detail.questions.length>0&&<div className="offer-questions"><h3>Нужно уточнить</h3>{detail.questions.map(q=>q.type==="split"?<p key={q.key}>{q.label}</p>:<Question key={`${detail.version}:${q.key}`} question={q} config={config} busy={busy} onAnswer={value=>act("answer",{answers:{[q.key]:value}})}/>)}</div>}
        {detail.questions.some(q=>q.type==="split")&&<SplitForm key={detail.id} images={detail.images||[]} busy={busy} onSplit={groups=>act("split",{groups})}/>}
        {detail.kind!=="service"&&["review","needs_input","failed","open","claimed"].includes(detail.state)&&<EditForm key={`${detail.id}:${detail.version}`} offer={detail} config={config} busy={busy} onSave={fields=>act(["open","claimed"].includes(detail.state)?"edit":"answer",{[["open","claimed"].includes(detail.state)?"fields":"answers"]:fields})}/>}
        {detail.kind==="service"&&["review","open"].includes(detail.state)&&<button className="button secondary" onClick={()=>setEditingService(true)}>Исправить услугу</button>}
        {editingService&&detail.kind==="service"&&<OfferServiceForm key={detail.id} initial={detail} services={config.services||[]} organizations={config.organizations} busy={busy} onCancel={()=>setEditingService(false)} onSave={fields=>run(async()=>{await api(`offers/${detail.id}/edit/`,{version:detail.version,fields});setEditingService(false);})}/>}
        {detail.state==="update_review"&&detail.update_preview&&<section className="offer-questions"><h3>Изменения расписания</h3><label>Обновляемое предложение<select value={detail.update_target||""} disabled={busy} onChange={e=>void act("update-choose",{target:Number(e.target.value)})}><option value="">Выберите</option>{detail.update_preview.candidates.map(o=><option key={o.id} value={o.id}>№{o.id} · {time(o.start_time)}–{time(o.end_time)} · {o.employee_name||"Без сотрудника"}</option>)}</select></label>
          {detail.update_preview.before&&<><h4>Прежние записи</h4><IntervalList items={detail.update_preview.before.intervals}/><h4>На новом снимке</h4><IntervalList items={detail.intervals}/><p>Дополнение сохраняет прежние записи. Замена использует только записи нового снимка.</p><div className="actions"><button disabled={busy} className="button secondary" onClick={()=>void act("update-choose",{target:detail.update_target,mode:"merge"})}>Дополнить</button><button disabled={busy} className="button secondary" onClick={()=>void act("update-choose",{target:detail.update_target,mode:"replace"})}>Заменить</button></div></>}
          {detail.update_preview.stale&&<p role="alert">Предложение изменилось. Снова выберите способ обновления для актуального сравнения.</p>}
          {detail.update_preview.intervals&&<><h4>После подтверждения ({detail.update_mode==="merge"?"дополнение":"замена"})</h4><IntervalList items={detail.update_preview.intervals}/><button className="button" disabled={busy||detail.update_preview.stale} onClick={()=>void act("update-apply")}>Подтвердить обновление</button></>}
        </section>}
        <div className="actions">
          {detail.state==="review"&&!detail.questions.length&&<button className="button" disabled={busy} onClick={()=>void act("publish")}>Подтвердить и опубликовать</button>}
          {detail.state==="claimed"&&<button className="button secondary" disabled={busy} onClick={()=>void act("release")}>Снять сотрудника</button>}
          {detail.kind!=="service"&&["review","needs_input","failed"].includes(detail.state)&&<button className="button secondary" disabled={busy} onClick={()=>void act("retry")}>Распознать заново</button>}
          {!["completed","expired","cancelled","duplicate","applied"].includes(detail.state)&&<button className="text-button" disabled={busy} onClick={()=>void act("cancel")}>Отменить предложение</button>}
        </div>
        {detail.state==="open"&&<AssignForm config={config} busy={busy} onAssign={employee=>act("assign",{employee})}/>}
        {detail.deliveries?.some(d=>["unknown","failed"].includes(d.state))&&<div className="offer-questions"><h3>Проверка доставки</h3>{detail.deliveries.filter(d=>["unknown","failed"].includes(d.state)).map(d=><DeliveryForm key={d.id} delivery={d} busy={busy} onResolve={(resolution,message_ids)=>act("delivery",{delivery:d.id,resolution,message_ids})}/>)}</div>}
        {detail.deliveries?.filter(d=>d.edit_state==="failed").map(d=><div className="offer-questions" key={`edit:${d.id}`}><p>Не удалось {d.delete_requested?"удалить":"обновить"} сообщение №{d.id} в Telegram. {d.edit_error}</p><button disabled={busy} onClick={()=>void act("delivery",{delivery:d.id,resolution:"retry_edit"})}>{d.delete_requested?"Повторить удаление":"Доступ восстановлен, повторить обновление"}</button></div>)}
        <details><summary>Обработка и история изменений</summary>{detail.jobs?.map(j=><p key={j.id}>Распознавание №{j.id}: {j.state}, попыток {j.attempts}{j.elapsed_ms!==null?`, ${(j.elapsed_ms/1000).toFixed(2)} с`:""}. {j.error}</p>)}{detail.history?.map(h=><div className="offer-history" key={h.id}><strong>{actions[h.action]||h.action}</strong> · {dateTimeLabel(h.created_at)} · {h.actor}<details><summary>Изменённые поля</summary><pre>{JSON.stringify(h.diff,null,2)}</pre></details></div>)}</details>
      </section>}
    </>}
    {config&&tab==="profiles"&&<><p>Загрузите один эталон, затем подтвердите названия и сокращения залов. Флажок у контакта разрешает присылать предложения этой организации.</p>{config.organizations.map(org=><ProfileForm key={org.id} org={org} profile={profiles.find(p=>p.organization===org.id)} config={config} busy={busy} onSave={data=>run(()=>api(`offer-profiles/${org.id}/`,data))} onUpload={file=>{const form=new FormData();form.append("image",file);return run(()=>api(`offer-profiles/${org.id}/sample/`,form),"Эталон принят. После обработки проверьте найденные названия.");}}/>)}</>}
    {config&&tab==="config"&&<ProcessingStatus config={config} onOpenSettings={onOpenSettings}/>}
  </div>;
}

function Question({question,config,busy,onAnswer}:{question:Offer["questions"][number];config:Config;busy:boolean;onAnswer:(value:unknown)=>Promise<void>}) {
  const [value,setValue]=useState(""),[error,setError]=useState("");
  return <form className="offer-form" onSubmit={e=>{e.preventDefault();try{setError("");void onAnswer(question.type==="intervals"?parseIntervals(value):value);}catch(e){setError((e as Error).message);}}}>
    <label>{question.label}{question.type==="organization"?<select required value={value} onChange={e=>setValue(e.target.value)}><option value="">Выберите</option>{config.organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select>:<input required type={["date","time"].includes(question.type)?question.type:"text"} value={value} onChange={e=>setValue(e.target.value)}/>}</label><button className="button secondary" disabled={busy}>Ответить</button>{error&&<p role="alert">{error}</p>}</form>;
}
function EditForm({offer,config,busy,onSave}:{offer:Offer;config:Config;busy:boolean;onSave:(data:Record<string,unknown>)=>Promise<void>}) {
  const [fields,setFields]=useState({date:offer.date||"",organization:String(offer.organization||""),start_time:offer.start_time?.slice(0,5)||"",end_time:offer.end_time?.slice(0,5)||"",comment:offer.comment});
  const [intervals,setIntervals]=useState(intervalText(offer.intervals)),[error,setError]=useState("");
  const published=["open","claimed"].includes(offer.state);
  return <details><summary>Исправить данные</summary><form onSubmit={e=>{e.preventDefault();try{setError("");const data:Record<string,unknown>={};for(const [key,value] of Object.entries(fields)){const previous=key==="organization"?String(offer.organization||""):key.includes("time")?(offer[key as "start_time"]?.slice(0,5)||""):offer[key as "date"|"comment"]||"";if(value!==previous)data[key]=value;}if(!published&&intervals!==intervalText(offer.intervals))data.intervals=parseIntervals(intervals);if(Object.keys(data).length)void onSave(data);}catch(e){setError((e as Error).message);}}}>
    <div className="offer-fields">{(["date","organization","start_time","end_time"] as const).map(key=><label key={key}>{{date:"Дата",organization:"Организация",start_time:"Начало",end_time:"Окончание"}[key]}{key==="organization"?<select disabled={offer.state==="claimed"} value={fields[key]} onChange={e=>setFields({...fields,[key]:e.target.value})}><option value="">Выберите</option>{config.organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select>:<input disabled={offer.state==="claimed"} type={key==="date"?"date":"time"} value={fields[key]} onChange={e=>setFields({...fields,[key]:e.target.value})}/>}</label>)}</div>
    {!published&&<label>Интервалы каждой записи<textarea value={intervals} onChange={e=>setIntervals(e.target.value)}/><small>После изменения записей общий диапазон пересчитается.</small></label>}<label>Комментарий<textarea value={fields.comment} onChange={e=>setFields({...fields,comment:e.target.value})}/></label>{error&&<p role="alert">{error}</p>}<button className="button secondary" disabled={busy}>Сохранить исправления</button></form></details>;
}
function AssignForm({config,busy,onAssign}:{config:Config;busy:boolean;onAssign:(id:number)=>Promise<void>}) {
  const [id,setId]=useState("");return <form className="offer-form" onSubmit={e=>{e.preventDefault();void onAssign(Number(id));}}><label>Назначить сотрудника<select required value={id} onChange={e=>setId(e.target.value)}><option value="">Выберите</option>{config.employees.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select></label><button className="button secondary" disabled={busy}>Назначить</button></form>;
}
function SplitForm({images,busy,onSplit}:{images:NonNullable<Offer["images"]>;busy:boolean;onSplit:(groups:number[][])=>Promise<void>}) {
  const [groups,setGroups]=useState<Record<number,string>>({});return <form onSubmit={e=>{e.preventDefault();const map:Record<string,number[]>={};for(const image of images){const group=groups[image.id]||"1";(map[group]||=[]).push(image.id);}void onSplit(Object.values(map));}}><h3>Разделить альбом</h3><p>Назначьте одинаковый номер группы изображениям одной даты и организации.</p><div className="offer-fields">{images.map(i=><label key={i.id}>Фото {i.id}<select value={groups[i.id]||"1"} onChange={e=>setGroups({...groups,[i.id]:e.target.value})}>{images.map((_,n)=><option key={n} value={n+1}>Группа {n+1}</option>)}</select></label>)}</div><button className="button secondary" disabled={busy}>Создать отдельные предложения</button></form>;
}
function DeliveryForm({delivery,busy,onResolve}:{delivery:NonNullable<Offer["deliveries"]>[number];busy:boolean;onResolve:(resolution:string,ids:number[])=>Promise<void>}) {
  const [ids,setIds]=useState("");return <div className="offer-history"><p>Сообщение №{delivery.id} · {delivery.purpose} · получатель {delivery.recipient}<br/>{delivery.error}</p>{delivery.state==="unknown"&&<label>ID найденных сообщений (через запятую)<input value={ids} onChange={e=>setIds(e.target.value)}/></label>}<div className="actions">{delivery.state==="unknown"&&<button disabled={busy||!ids.trim()} onClick={()=>void onResolve("sent",ids.split(/[,\s]+/).filter(Boolean).map(Number))}>Сообщения найдены в Telegram</button>}<button disabled={busy} onClick={()=>void onResolve("not_sent",[])}>{delivery.state==="unknown"?"Проверил: сообщений нет, отправить":"Права исправлены, повторить"}</button></div></div>;
}
function ProfileForm({org,profile,config,busy,onSave,onUpload}:{org:Option;profile?:Profile;config:Config;busy:boolean;onSave:(data:unknown)=>Promise<void>;onUpload:(file:File)=>Promise<void>}) {
  const [rooms,setRooms]=useState("");const [publishers,setPublishers]=useState<number[]>([]);const [confirmed,setConfirmed]=useState(false);const [active,setActive]=useState(true);
  const signature=JSON.stringify(profile?.rooms), publisherSignature=JSON.stringify(profile?.publishers);
  useEffect(()=>{const values:Profile["rooms"]=JSON.parse(signature||"[]");setRooms(values.map(r=>[r.name,...r.aliases].join(" | ")).join("\n"));setConfirmed(profile?.confirmed||false);setActive(profile?.active??true);setPublishers(JSON.parse(publisherSignature||"[]"));},[signature,publisherSignature,profile?.confirmed,profile?.active]);
  return <section className="panel"><h2>{org.name}</h2><label>Эталонный скриншот<input disabled={busy||profile?.processing} type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>{const file=e.target.files?.[0];if(file)void onUpload(file);e.target.value="";}}/></label>{profile?.processing&&<p role="status">Ищем названия залов…</p>}{profile?.error&&<p role="alert">{profile.error}</p>}{profile?.header_url&&<img className="offer-header" src={profile.header_url} alt={`Заголовок эталона ${org.name}`}/>}
    {!!profile?.suggestions.length&&<p>Найдено: {profile.suggestions.join(" · ")} <button className="text-button" onClick={()=>{setRooms(profile.suggestions.join("\n"));setConfirmed(false);}}>Использовать и исправить</button></p>}
    <form onSubmit={e=>{e.preventDefault();void onSave({rooms:rooms.split("\n").filter(r=>r.trim()).map(line=>{const [name,...aliases]=line.split("|").map(s=>s.trim()).filter(Boolean);return {name,aliases};}),publishers,confirmed,active});}}>
      <label>Залы и сокращения<textarea rows={5} value={rooms} onChange={e=>{setRooms(e.target.value);setConfirmed(false);}} placeholder={"БОХО\nМодерн\nINLIGHT\nСтол визажный | Стол ви…"}/><small>Один зал на строку. После | укажите подтверждённые сокращения.</small></label>
      <label className="offer-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>Названия и сокращения проверены</label><label className="offer-check"><input type="checkbox" checked={active} onChange={e=>setActive(e.target.checked)}/>Использовать профиль</label>
      <h3>Кто может присылать предложения</h3><p className="muted">Главный утверждающий счетов имеет доступ ко всем активным организациям.</p>{config.contacts.map(c=><label className="offer-check" key={c.id}><input type="checkbox" checked={publishers.includes(c.id)} onChange={e=>setPublishers(e.target.checked?[...publishers,c.id]:publishers.filter(id=>id!==c.id))}/>{c.name} · {c.user_id}</label>)}<button className="button secondary" disabled={busy}>Сохранить профиль</button>
    </form></section>;
}
function ProcessingStatus({config,onOpenSettings}:{config:Config;onOpenSettings?:()=>void}) {
  return <section className="panel"><h2>Обработка предложений</h2>
    <p>Приём предложений {config.enabled?"включён":"выключен"}. Группа, топики активных и взятых смен и освобождений и изменений настраиваются вместе с остальными маршрутами Telegram.</p>
    <button className="button secondary" onClick={onOpenSettings}>Открыть настройки Telegram</button>
    <p>Главный администратор: {config.contacts.find(c=>c.user_id===config.coordinator)?.name||"Не выбран. Назначьте утверждающего счетов в настройках."}</p>
    <p>{config.auto_publish?"Автопубликация разрешена проверенным отчётом качества.":"Перед публикацией требуется подтверждение отправителя. Точность выше 99% пока не подтверждена независимой выборкой."}</p>
    {(!config.claimed_thread_id||!config.released_thread_id)&&<p className="offer-questions">Укажите топики взятых смен и освобождений и изменений в настройках Telegram.</p>}<h3>Фоновая обработка</h3>{["recognition","offers"].map(name=>{const worker=config.workers.find(w=>w.name===name);return <p key={name}>{name==="recognition"?"Распознавание":"Публикации и завершение"}: {worker&&Date.now()-Date.parse(worker.updated_at)<180000?"работает":"нет свежего сигнала"}{worker?` · ${dateTimeLabel(worker.updated_at)}`:""}</p>;})}
  </section>;
}

function IntervalList({items}:{items:Interval[]}) {return <ul>{items.map((i,n)=><li key={n}>{i.room||"Зал"}: {i.start||"?"}–{i.end||"?"}</li>)}</ul>;}
