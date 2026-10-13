"use client";
import {useCallback,useEffect,useRef,useState,FormEvent} from "react";
import {dateLabel} from "./date-format";
import HistoryDialog from "./HistoryDialog";
import type {Bootstrap,Item} from "./accounting-types";
const base="/api/shifts/ledger/";
const today=()=>new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Yekaterinburg"}).format(new Date());
const rub=(v:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(v||0));
const statuses:Record<string,string>={unconfirmed:"Проверьте совпадение на фото",needs_interval:"Уточните время записи в предложении смены",needs_choice:"Выберите получателя и правило",stale_selection:"Подтвердите выбор заново",rule_review:"Нужен пересчёт правила"};
async function api(path:string,method="GET",payload?:unknown){
  const r=await fetch(base+path,{method,cache:"no-store",headers:{"Content-Type":"application/json"},body:payload===undefined?undefined:JSON.stringify(payload)});
  const d=await r.json();if(!r.ok)throw Error(d.error||"Не удалось выполнить действие");return d;
}
export function clientPayload(draft:Item){return {...draft,phones:String(draft.phone_text||"").split(/[\n;,]+/).map(v=>v.trim()).filter(Boolean)};}
export function rulePayload(draft:Item){
  const entries=(draft.entries||[]).map(clientPayload);
  return {...draft,organization:Number(draft.organization),clients:(draft.clients||[]).map(Number),client_entries:entries};
}
async function catalog(resource:string){
  const items:Item[]=[];let page;
  do{page=await api(`${resource}/?limit=100&offset=${items.length}`);items.push(...page.items);}while(items.length<page.count&&page.items.length);
  return items;
}

export default function ClientBonusesConsole({data,onChanged}:{data:Bootstrap|null;onChanged:()=>Promise<void>}){
  const [clients,setClients]=useState<Item[]>([]),[rules,setRules]=useState<Item[]>([]),[questions,setQuestions]=useState<any>({items:[],count:0});
  const [org,setOrg]=useState(""),[start,setStart]=useState(today().slice(0,7)+"-01"),[end,setEnd]=useState(today()),[offset,setOffset]=useState(0);
  const [draft,setDraft]=useState<any>(null),[preview,setPreview]=useState<any>(null),[choice,setChoice]=useState<any>(null),[reason,setReason]=useState<any>(null);
  const [history,setHistory]=useState<Item|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[correction,setCorrection]=useState(false);
  const sequence=useRef(0);
  const invalidateLoad=useCallback(()=>{sequence.current++;},[]);
  const load=useCallback(async()=>{
    const current=++sequence.current;
    const query=new URLSearchParams({review:"1",offset:String(offset),start,end,...(org?{organization:org}:{})});
    const [c,r,q]=await Promise.all([catalog("bonus-clients"),catalog("bonus-rules"),api("client-bookings/?"+query)]);
    if(current===sequence.current){setClients(c);setRules(r);setQuestions(q);}
  },[org,start,end,offset]);
  useEffect(()=>{let active=true;load().catch(e=>{if(active)setError(e.message);});return()=>{active=false;invalidateLoad();};},[load,invalidateLoad]);
  async function run(fn:()=>Promise<any>,message="Сохранено"){
    setBusy(true);setError("");setNotice("");try{const result=await fn();await load();await onChanged();setNotice(message);return result;}
    catch(e){setError(e instanceof Error?e.message:"Ошибка сохранения");return null;}finally{setBusy(false);}
  }
  const clientName=(id:number)=>clients.find(c=>c.id===id)?.name||`Клиент №${id}`;
  const organizations=data?.organization_list.filter(o=>o.active)||[];
  function edit(row?:Item){
    setPreview(null);setError("");
    setDraft(row?{...row,entries:row.clients.map((id:number)=>{const c=clients.find(c=>c.id===id);return c?{...c,phone_text:c.phones.join("\n")}:{id,name:"",phone_text:"",active:true};})}:
      {name:"",organization:org,amount:"200",start:today(),end:"",active:true,clients:[],entries:[{name:"",phone_text:"",active:true}]});
  }
  const set=(key:string,value:unknown)=>setDraft({...draft,[key]:value});
  function setClient(index:number,key:string,value:unknown){set("entries",draft.entries.map((c:Item,n:number)=>n===index?{...c,[key]:value}:c));}
  function removeClient(index:number){const id=draft.entries[index].id;setDraft({...draft,clients:draft.clients.filter((v:number)=>v!==id),entries:draft.entries.filter((_:Item,n:number)=>n!==index)});}
  async function save(e:FormEvent){
    e.preventDefault();
    if(await run(()=>api("bonus-rules/"+(draft.id?draft.id+"/":""),draft.id?"PUT":"POST",rulePayload(draft)),draft.id?"Настройки сохранены. Изменение условий примените через предпросмотр.":"Правило действует. Клиенты распознаются автоматически по фотографиям предложений."))setDraft(null);
  }
  async function review(rule?:Item){
    const request={start,end,correction,...(rule?{rule:rule.id,revision:rule.revision}:{})};
    const result=await run(()=>api("bonus-recalculate/","POST",request),"Предпросмотр готов");if(result)setPreview({...result,request});
  }
  function action(row:Item,name:string,extra:any={}){return run(()=>api(`client-bookings/${row.id}/${name}/`,"POST",{revision:row.revision,correction,...extra}),"Проверка доплаты сохранена");}
  return <section className="panel client-bonuses-panel">
    <div className="panel-heading"><div><h2>Правила доплат</h2><p>Клиенты распознаются автоматически на фотографиях предложений смен. В работе показывается, за что начислена доплата.</p></div><button className="button secondary" disabled={busy} onClick={()=>run(load,"Данные обновлены")}>Обновить</button></div>
    <div className="padded">
      {error&&<p className="alert" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
      <div className="filters"><label>Студия<select aria-label="Студия доплат" value={org} onChange={e=>{setOrg(e.target.value);setOffset(0);}}><option value="">Все студии</option>{organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
        <label>С даты<input aria-label="Доплаты с даты" type="date" value={start} onChange={e=>{setStart(e.target.value);setOffset(0);setPreview(null);}}/></label><label>По дату<input aria-label="Доплаты по дату" type="date" value={end} onChange={e=>{setEnd(e.target.value);setOffset(0);setPreview(null);}}/></label>
        <label><input type="checkbox" checked={correction} onChange={e=>{setCorrection(e.target.checked);setPreview(null);}}/>Корректировка подтверждённых работ</label></div>
      <p className="muted">Сохранённые фотографии также обрабатываются автоматически. Подтверждённые счета сохраняются: изменения в них доступны через предпросмотр корректировки.</p>
      {!draft&&<button className="button" disabled={busy} onClick={()=>edit()}>+ Правило</button>}
      {draft&&<form onSubmit={save}><h3>{draft.id?"Изменить":"Добавить"} правило</h3><div className="form-grid">
        <label>Название<input aria-label="Название" maxLength={160} required value={draft.name} onChange={e=>set("name",e.target.value)}/></label>
        <label>Студия<select aria-label="Студия правила" required value={draft.organization||""} onChange={e=>set("organization",e.target.value)}><option value="">Выберите студию</option>{organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
        <label>Доплата за запись, ₽<input aria-label="Сумма правила" required type="number" step="0.01" min="0.01" value={draft.amount} onChange={e=>set("amount",e.target.value)}/></label>
        <label>Начало действия<input aria-label="Начало правила" required type="date" value={draft.start} onChange={e=>set("start",e.target.value)}/></label>
        <label>Окончание действия<input aria-label="Окончание правила" type="date" value={draft.end||""} onChange={e=>set("end",e.target.value)}/></label>
        <label><input type="checkbox" checked={draft.active} onChange={e=>set("active",e.target.checked)}/>Включено</label>
      </div>
      <fieldset className="bonus-rule-clients"><legend>Клиенты правила — можно добавить несколько</legend>
        {draft.entries.map((c:Item,index:number)=><div key={index} className="panel padded"><div className="form-grid">
          <label>Имя клиента (необязательно)<input aria-label={`Имя клиента ${index+1}`} maxLength={160} value={c.name} onChange={e=>setClient(index,"name",e.target.value)}/></label>
          <label>Телефоны, по одному на строку<textarea aria-label={`Телефоны клиента ${index+1}`} required value={c.phone_text} onChange={e=>setClient(index,"phone_text",e.target.value)}/></label>
          <label><input aria-label={`Клиент правила: ${c.name||index+1}`} type="checkbox" checked={c.active} onChange={e=>setClient(index,"active",e.target.checked)}/>Клиент активен</label>
        </div><button type="button" className="text-button" onClick={()=>removeClient(index)}>Убрать клиента из правила</button></div>)}
        <button type="button" className="button secondary" onClick={()=>set("entries",[...draft.entries,{name:"",phone_text:"",active:true}])}>Добавить клиента</button>
        <label>Добавить уже сохранённого клиента<select aria-label="Сохранённый клиент" value="" onChange={e=>{const c=clients.find(c=>c.id===Number(e.target.value));if(c)setDraft({...draft,clients:[...draft.clients,c.id],entries:[...draft.entries,{...c,phone_text:c.phones.join("\n")}]});}}><option value="">Выберите клиента</option>{clients.filter(c=>!draft.entries.some((entry:Item)=>entry.id===c.id)).map(c=><option key={c.id} value={c.id}>{clientName(c.id)} · {c.phones.join(", ")}</option>)}</select></label>
        <small>8… и +7… распознаются как один номер. Телефоны нужны только для совпадения на фото.</small>
      </fieldset><div className="form-actions"><button className="button" disabled={busy}>Сохранить</button> <button type="button" className="button secondary" onClick={()=>setDraft(null)}>Отмена</button></div></form>}
      {!draft&&<div className="table-scroll"><table><thead><tr><th>Правило / студия</th><th>Клиенты</th><th>Сумма / период</th><th>Состояние</th><th>Действия</th></tr></thead><tbody>{rules.filter(r=>!org||String(r.organization)===org).map(r=><tr key={r.id}><td>{r.name}<small>{r.organization_name}</small></td><td>{r.clients.map((id:number)=>clientName(id)).join(", ")}</td><td>{rub(r.amount)}<small>{dateLabel(r.start)} — {r.end?dateLabel(r.end):"Без окончания"}</small></td><td>{r.pending?"Изменения не применены":r.applied?.active?"Действует":"Выключено"}</td><td><div className="table-actions"><button className="text-button" disabled={busy} onClick={()=>edit(r)}>Изменить</button> <button className="text-button" disabled={busy} onClick={()=>review(r)}>Проверить и применить</button> <button className="text-button" onClick={()=>setHistory({...r,entity_type:"bonus_rule",client_labels:Object.fromEntries(clients.map(c=>[c.id,clientName(c.id)]))})}>История</button></div></td></tr>)}</tbody></table></div>}
      {!!questions.count&&<section><h3>Требуют проверки · {questions.count}</h3><p>Здесь только сомнительные совпадения и выбор между несколькими получателями. Остальные доплаты начисляются автоматически.</p>{questions.items.map((b:Item)=><article key={b.id}><strong>{dateLabel(b.date)} · {b.organization_name} · {b.client_name}</strong><p>{statuses[b.charge?.state]} · {b.start_time?.slice(0,5)||"?"}–{b.end_time?.slice(0,5)||"?"}</p>
        {b.sources?.filter((s:Item)=>s.image_id).map((s:Item,i:number)=><a key={i} href={`${base}offer-images/${s.image_id}/`} target="_blank" rel="noreferrer">Фото дня </a>)}
        <div className="table-actions">{b.state==="draft"&&b.start_time&&b.end_time&&<button className="text-button" disabled={busy} onClick={()=>action(b,"confirm")}>Подтвердить совпадение на фото</button>}
        {b.state==="confirmed"&&b.choices.records.length>0&&b.choices.rules.length>0&&<button className="text-button" disabled={busy} onClick={()=>setChoice({booking:b,record:String(b.selected_record||b.choices.records[0].id),rule:String(b.selected_rule||b.choices.rules[0].id)})}>Выбрать получателя</button>}
        <button className="text-button" disabled={busy} onClick={()=>setReason({booking:b,text:""})}>Не начислять</button></div>
      </article>)}<div className="form-actions"><button className="button secondary" disabled={busy||!offset} onClick={()=>setOffset(v=>Math.max(0,v-30))}>Назад</button> <button className="button secondary" disabled={busy||offset+30>=questions.count} onClick={()=>setOffset(v=>v+30)}>Далее</button></div></section>}
      <button className="button secondary" disabled={busy} onClick={()=>review()}>Предпросмотр пересчёта доплат</button>
      {choice&&<form onSubmit={async e=>{e.preventDefault();if(await action(choice.booking,"choose",{token:choice.booking.choices.token,record:Number(choice.record),rule:Number(choice.rule)}))setChoice(null);}}><h3>Получатель доплаты · {choice.booking.client_name}</h3><label>Выполненная смена<select aria-label="Получатель доплаты" value={choice.record} onChange={e=>setChoice({...choice,record:e.target.value})}>{choice.booking.choices.records.map((r:Item)=><option key={r.id} value={r.id}>{r.employee_name} · {r.start_time.slice(0,5)}–{r.end_time.slice(0,5)} · №{r.id}</option>)}</select></label><label>Правило<select aria-label="Выбор правила" value={choice.rule} onChange={e=>setChoice({...choice,rule:e.target.value})}>{choice.booking.choices.rules.map((r:Item)=><option key={r.id} value={r.id}>{r.name} · {rub(r.amount)}</option>)}</select></label><div className="form-actions"><button className="button" disabled={busy}>Подтвердить выбор</button> <button type="button" className="button secondary" onClick={()=>setChoice(null)}>Отмена</button></div></form>}
      {reason&&<form onSubmit={async e=>{e.preventDefault();if(await action(reason.booking,"exclude",{reason:reason.text}))setReason(null);}}><h3>Не начислять доплату · {reason.booking.client_name}</h3><label>Причина<input required maxLength={500} value={reason.text} onChange={e=>setReason({...reason,text:e.target.value})}/></label><div className="form-actions"><button className="button" disabled={busy}>Сохранить</button> <button type="button" className="button secondary" onClick={()=>setReason(null)}>Отмена</button></div></form>}
      {preview&&<section className="panel"><div className="panel-heading"><h3>Предпросмотр клиентских доплат · {preview.changes.length} изменений</h3></div><div className="padded"><p>Проверяется выбранный период по всем студиям. Показаны только реальные изменения.</p>{!preview.changes.length&&<p>Стоимость работ не изменится. Условия выбранного правила можно активировать.</p>}{preview.changes.map((c:Item)=><div key={c.id}><strong>Запись №{c.id} · {dateLabel(c.date)} · {c.organization_name} · {c.employee_name}</strong><p>{rub(c.before)} → {rub(c.after)}{c.review&&" · требуется выбор"}</p>{c.details.map((d:Item,i:number)=><small key={i}>{d.rule} · {d.client} · {d.start}–{d.end} · {rub(d.amount)}<br/></small>)}</div>)}<div className="form-actions"><button className="button" disabled={busy} onClick={async()=>{if(await run(()=>api("bonus-recalculate/","POST",{...preview.request,fingerprint:preview.fingerprint}),"Пересчёт применён"))setPreview(null);}}>Применить проверенный пересчёт</button> <button className="button secondary" onClick={()=>setPreview(null)}>Закрыть</button></div></div></section>}
    </div>{history&&<HistoryDialog record={history} data={data} onClose={()=>setHistory(null)}/>}</section>;
}
