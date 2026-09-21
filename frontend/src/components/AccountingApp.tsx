"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { dateLabel } from "./date-format";
import JournalFilters, { emptyJournalFilters, FilterToggle, JournalFilterValues, MonthPicker, SortHeading } from "./JournalFilters";
import HistoryDialog from "./HistoryDialog";
import useNotice from "./useNotice";
import SharedShiftDetails from "./SharedShiftDetails";
import RecalculationPreview from "./RecalculationPreview";
import EarningsReport, { EarningsData } from "./EarningsReport";
import Modal from "./AccountingModal";
import ServiceRecordEditor from "./ServiceRecordEditor";
import { PaymentStatus, PaymentsData } from "./Payments";
import InvoicesList, { emptyInvoiceFilters, InvoiceFilters } from "./InvoicesList";
import OffersConsole from "./OffersConsole";
import type { Bootstrap, Editor, Field, Item } from "./accounting-types";

const base = "/api/shifts/ledger/";
const labels: Record<string, string> = {time:"Интервал времени",quantity:"За единицу",amount:"Готовая сумма",mark:"Отметка",entry:"По записи",daily:"Ежедневно",monthly:"Ежемесячно",fixed:"Фиксированная",hourly:"Почасовая",service:"Типовая услуга",oneoff:"Разовая работа",expense:"Расход",draft:"На проверке",approved:"Подтверждён",pending:"В очереди",sending:"Отправляется",sent:"Отправлено",failed:"Ошибка",unknown:"Нужно проверить доставку",cancelled:"Отменено",owner:"Владельцу",preview:"Excel на проверку",approval:"Подтверждение"};
const rub = (v: unknown) => new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB",maximumFractionDigits:2}).format(Number(v || 0));
const isoToday = () => new Intl.DateTimeFormat("sv-SE", {timeZone:"Asia/Yekaterinburg",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
const opts = (values: string[]) => values.map(v => ({value:v,label:labels[v] || v}));
async function api(path: string, method="GET", body?: unknown, legacy=false) {
  const response = await fetch((legacy ? "/api/shifts/" : base)+path, {method,headers:{"Content-Type":"application/json"},body:body === undefined ? undefined : JSON.stringify(body),cache:"no-store"});
  const data = await response.json();
  if (!response.ok) {const error=new Error(data.error || "Не удалось выполнить действие") as Error & {status?:number};error.status=response.status;throw error;}
  return data;
}

function Icon({name}: {name:string}) {
  const paths: Record<string,string> = {overview:"M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",records:"M8 3h12v18H4V3h4 M8 8h8 M8 12h8 M8 16h5",expenses:"M3 7h18v13H3z M3 7V4h15 M15 12h6v4h-6z",invoices:"M6 3h12v18l-3-2-3 2-3-2-3 2z M9 7h6 M9 11h6",services:"M12 3v18 M3 12h18 M5 5l14 14 M19 5L5 19",settings:"M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6"};
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.records}/></svg>;
}

export default function AccountingApp() {
  const [page,setPage] = useState("overview");
  const [data,setData] = useState<Bootstrap|null>(null);
  const [records,setRecords] = useState<Item[]>([]);
  const [summary,setSummary] = useState<any>({count:0,total:0,review:0,employee_totals:[]});
  const [earnings,setEarnings] = useState<EarningsData|null>(null);
  const [earningsMonth,setEarningsMonth] = useState(isoToday().slice(0,7));
  const [batches,setBatches] = useState<Item[]>([]);
  const [selectedBatch,setSelectedBatch] = useState<number|null>(null);
  const [linkedBatch,setLinkedBatch] = useState<Item|null>(null);
  const [selectedInvoice,setSelectedInvoice] = useState<number|null>(null);
  const invoiceDetailRef = useRef<HTMLDivElement>(null);
  const invoiceOpenSequence = useRef(0);
  const revealInvoice = useRef(false);
  useEffect(()=>{
    if(revealInvoice.current&&linkedBatch?.id===selectedBatch&&invoiceDetailRef.current){
      revealInvoice.current=false;
      invoiceDetailRef.current.scrollIntoView({behavior:"smooth",block:"start"});
      invoiceDetailRef.current.focus({preventScroll:true});
    }
  },[linkedBatch,selectedBatch,selectedInvoice]);
  const [month,setMonth] = useState(isoToday().slice(0,7));
  const start = month + "-01";
  const [monthYear, monthNumber] = month.split("-").map(Number);
  const monthEnd = new Date(Date.UTC(monthYear, monthNumber, 0)).toISOString().slice(0,10);
  const end = monthEnd < isoToday() ? monthEnd : isoToday();
  const [org,setOrg] = useState("");
  const [offset,setOffset] = useState(0);
  const [search,setSearch] = useState("");
  const [debouncedSearch,setDebouncedSearch] = useState("");
  const [journalFilters,setJournalFilters] = useState<JournalFilterValues>({...emptyJournalFilters});
  const [filtersOpen,setFiltersOpen] = useState(false);
  const [showArchived,setShowArchived] = useState(false);
  const [payments,setPayments] = useState<PaymentsData>({invoices:[],count:0,open_count:0});
  const [invoiceFilters,setInvoiceFilters] = useState<InvoiceFilters>({...emptyInvoiceFilters});
  const [paymentOffset,setPaymentOffset] = useState(0);
  const [paymentLoading,setPaymentLoading] = useState(false);
  const [paymentError,setPaymentError] = useState("");
  const paymentSequence = useRef(0);
  const [rateFiltersOpen,setRateFiltersOpen] = useState(false);
  const [rateOrganization,setRateOrganization] = useState("");
  const [rateSearch,setRateSearch] = useState("");
  const [ordering,setOrdering] = useState("-date");
  const requestSequence = useRef(0);
  const pageSize = 50;
  useEffect(()=>{const timer=setTimeout(()=>setDebouncedSearch(search),250);return()=>clearTimeout(timer);},[search]);
  const [error,setError] = useState("");
  const [notice,setNotice] = useNotice(page);
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState(false);
  const [editor,setEditor] = useState<Editor|null>(null);
  const [serviceTab,setServiceTab] = useState("services");
  const [settingsTab,setSettingsTab] = useState("organizations");
  const [history,setHistory] = useState<Item|null>(null);
  const [recalc,setRecalc] = useState<any>(null);
  const refreshPayments = useCallback(async(signal?:AbortSignal,background=false)=>{
    const sequence=++paymentSequence.current;
    if(!background)setPaymentLoading(true);
    try {
      const query=new URLSearchParams(page==="invoices"?{...invoiceFilters,offset:String(paymentOffset),limit:String(pageSize)}:{state:"open",limit:"1"});
      const response=await fetch(base+(page==="invoices"?"invoices/?":"payments/?")+query,{signal,cache:"no-store"});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||"Не удалось загрузить оплаты");
      if(signal?.aborted||sequence!==paymentSequence.current)return;
      if(page==="invoices"&&paymentOffset>0&&paymentOffset>=result.count){setPaymentOffset(Math.max(0,Math.floor((result.count-1)/pageSize)*pageSize));return;}
      setPayments({invoices:result.invoices||[],count:result.count||0,open_count:result.open_count||0});setPaymentError("");
    } catch(error) {
      if(!signal?.aborted&&sequence===paymentSequence.current)setPaymentError(error instanceof Error?error.message:"Не удалось загрузить оплаты");
    } finally {if(!signal?.aborted&&sequence===paymentSequence.current)setPaymentLoading(false);}
  },[page,invoiceFilters,paymentOffset]);
  useEffect(()=>{
    const controller=new AbortController();
    refreshPayments(controller.signal);
    const refresh=()=>{if(!document.hidden)refreshPayments(controller.signal,true);};
    const timer=setInterval(refresh,15000);window.addEventListener("focus",refresh);
    return()=>{controller.abort();clearInterval(timer);window.removeEventListener("focus",refresh);};
  },[refreshPayments]);
  const load = useCallback(async()=>{
    const sequence = ++requestSequence.current;
    if(page==="invoices") {
      const [bootstrap,batch]=await Promise.all([api("bootstrap/"),selectedBatch?api(`batches/${selectedBatch}/`).catch(e=>{if(e.status===404)return null;throw e;}):Promise.resolve(null)]);
      if(sequence===requestSequence.current){setData(bootstrap);setLinkedBatch(batch);if(selectedBatch&&!batch){setSelectedBatch(null);setSelectedInvoice(null);}}
      return;
    }
    if(page==="earnings") {
      const [bootstrap,report] = await Promise.all([api("bootstrap/"),api("earnings/?month="+earningsMonth)]);
      if(sequence===requestSequence.current){setData(bootstrap);setEarnings(report);}
      return;
    }
    const query = new URLSearchParams({month,offset:String(offset),limit:String(pageSize),ordering});
    if(org) query.set("organization",org);
    if(page==="expenses") query.set("kind","expense");
    if(page==="records") query.set("kind","work");
    if(page==="records" || page==="expenses") {
      if(debouncedSearch) query.set("q",debouncedSearch);
      Object.entries(journalFilters).forEach(([key,value])=>{if(value && (page!=="expenses" || !["kind","employee","service"].includes(key)))query.set(key,value);});
    }
    const batchQuery = new URLSearchParams();
    const result = await Promise.all([api("bootstrap/"),api("records/?"+query),api("batches/?"+batchQuery)]);
    if(sequence===requestSequence.current){
      if(offset>0 && offset>=result[1].count){setOffset(Math.max(0,Math.floor((result[1].count-1)/pageSize)*pageSize));return;}
      setData(result[0]);setRecords(result[1].records);setSummary(result[1]);setBatches(result[2]);
    }
  },[month,org,offset,page,ordering,journalFilters,debouncedSearch,selectedBatch,earningsMonth]);
  useEffect(()=>{let active=true;setLoading(true);load().then(()=>{if(active)setError("");}).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[load]);
  useEffect(()=>{const refresh=()=>{if(!document.hidden)load().catch(()=>{});};const id=setInterval(refresh,15000);window.addEventListener("focus",refresh);return()=>{clearInterval(id);window.removeEventListener("focus",refresh);};},[load]);
  async function run(fn:()=>Promise<any>, message="Сохранено") {setBusy(true);setError("");try{const result=await fn();await Promise.all([load(),refreshPayments(undefined,true)]);const changes=result?.allocation_changes||[];setNotice(message+(changes.length?". Перераспределены начисления: "+changes.map((c:any)=>`${c.employee_name||"Без исполнителя"} (№${c.id}): ${rub(c.before)} → ${rub(c.after)}`).join("; "):""));return result;}catch(e){setError(e instanceof Error?e.message:"Не удалось выполнить действие");return null;}finally{setBusy(false);}}
  const orgOptions = (data?.organization_list || []).filter(o=>o.active).map(o=>({value:o.id,label:o.name}));
  const serviceOptions = (data?.services || []).filter(o=>o.active).map(o=>({value:o.id,label:o.name}));
  const employees = (data?.employees || []).filter(o=>o.active).map(o=>({value:o.id,label:o.name}));
  const contacts = (data?.contacts || []).map(o=>({value:o.id,label:`${o.name} · ${o.user_id}`}));
  const serviceName = (id:number) => data?.services.find(s=>s.id===id)?.name || "—";
  const orgName = (id:number) => data?.organization_list.find(s=>s.id===id)?.name || "—";
  const optionField = (key:string,label:string,options:Field["options"],required=true):Field=>({key,label,type:"select",options,required});
  const activeField:Field={key:"active",label:"Активно",type:"checkbox"};
  const dateFields:Field[]=[{key:"start",label:"Действует с",type:"date",required:true},{key:"end",label:"Действует до",type:"date"}];
  function editRecord(kind:string,row?:Item) {
    const fields:Field[]=[{key:"date",label:"Дата",type:"date",required:true},optionField("organization","Организация",orgOptions),optionField("employee","Исполнитель",employees,false)];
    if(kind==="service") fields.push(optionField("service","Услуга",serviceOptions.filter(o=>data?.services.find(s=>s.id===o.value)?.frequency==="entry")),{key:"start_time",label:"Начало",type:"time"},{key:"end_time",label:"Конец",type:"time"},{key:"units",label:"Часы / количество",type:"number",hint:"При интервале часы рассчитываются автоматически."});
    fields.push({key:"amount",label:kind==="service"?"Сумма для ввода готовой стоимости":"Сумма, ₽",type:"number",required:kind!=="service"},{key:"description",label:kind==="expense"?"Назначение расхода":kind==="service"?"Комментарий":"Что сделано / комментарий",type:"textarea",hint:kind==="service"?"Изменение комментария не пересчитывает сумму.":undefined,required:kind!=="service"});
    if(row || kind==="service") fields.push({key:"correction",label:"Корректировка подтверждённой работы или смены",type:"checkbox",hint:"Разрешает изменение всех затронутых долей смены. Отправленный Excel сохранится; после исправления создайте новую версию счёта."});
    setEditor({title:row?"Изменить запись":labels[kind],resource:"records",fields,values:row?{...row}:{kind,date:isoToday(),organization:org,units:1,included:true,amount:""}});
  }
  function editService(row?:Item) {setEditor({title:row?"Настройки услуги":"Новая услуга",resource:"services",values:row || {active:true,included:true,input_type:"time",frequency:"entry",sort_order:100},fields:[
    {key:"name",label:"Название",required:true},{key:"aliases",label:"Алиасы через запятую",type:"aliases",hint:"Например: сопр, сопровождения"},optionField("input_type","Как бот читает запись",[{value:"time",label:"Интервал времени"},{value:"quantity",label:"Количество"},{value:"amount",label:"Готовая сумма"},{value:"mark",label:"Отметка о выполнении"}]),optionField("frequency","Когда начислять",opts(["entry","daily","monthly"])),optionField("default_organization","Организация по умолчанию",orgOptions,false),{key:"sheet",label:"Отдельный лист Excel",hint:"Оставьте пустым для основного листа."},{key:"sort_order",label:"Порядок",type:"number"},activeField]});}
  function editRate(row?:Item) {const shared=!!data?.features?.shared_shift_allocation;setEditor({title:row?"Изменить тариф":"Новый тариф",resource:"rates",values:row||{start:isoToday(),calculation:"fixed",active:true,price:0},fields:[optionField("service","Услуга",serviceOptions),optionField("organization","Организация",orgOptions),optionField("calculation","Способ расчёта",opts(["fixed","hourly","quantity","amount"])),{key:"price",label:"Ставка, ₽",type:"number",required:true},{key:"minimum",label:"Минимум, ₽",type:"number",hint:shared?"Для почасовой услуги — один общий минимум на организацию и день; для остальных — за запись.":"В текущем режиме применяется отдельно к каждой записи."},{key:"maximum",label:"Максимум, ₽",type:"number",hint:shared?"Для почасовой услуги — один общий максимум на организацию и день; для остальных — за запись.":"В текущем режиме применяется отдельно к каждой записи."},...dateFields,activeField]});}
  function editAccrual(row?:Item) {setEditor({title:"Автоматическое начисление",resource:"accruals",values:row||{start:isoToday(),active:true},fields:[optionField("service","Услуга",serviceOptions.filter(o=>data?.services.find(s=>s.id===o.value)?.frequency!=="entry")),optionField("organization","Организация",orgOptions),{...optionField("employee","Сотрудник",(data?.employees||[]).filter(e=>e.active||e.id===row?.employee).map(e=>({value:e.id,label:e.name+(e.active?"":" (отключён)")})),false),hint:"Кому записывать новые начисления по этой услуге и организации. Ранее созданные записи не изменятся."},...dateFields,activeField]});}
  function editProfile(organization:Item) {const profile=data?.organizations.find(o=>o.organization===organization.id);setEditor({title:`Счета · ${organization.name}`,resource:"organizations",values:profile||{organization:organization.id,include_expenses:true,monthly:false,recipients:[],payment_recipients:[],excluded_services:[]},fields:[{key:"recipients",label:"Получатели Telegram",type:"recipients",options:contacts,hint:"Сначала получатель должен написать боту /start. Выберите получателей и включите подтверждение оплаты для нужных людей."},{key:"monthly",label:"Готовить счета ежемесячно",type:"checkbox"}]});}
  function editSchedule() {if(!data)return;setEditor({title:"Бот и ежемесячные отчёты",resource:"settings",values:{...data.settings,offer_enabled:data.offer_config?.enabled||false,offer_chat_id:data.offer_config?.chat_id??null,offer_thread_id:data.offer_config?.thread_id??null,offer_claimed_thread_id:data.offer_config?.claimed_thread_id??null,offer_released_thread_id:data.offer_config?.released_thread_id??null},fields:[{key:"earnings_enabled",label:"Отправлять главный отчёт 1-го числа в 10:00 (Екатеринбург)",type:"checkbox",hint:"Общий заработок сотрудников за прошлый месяц получает утверждающий. Независимо от рассылки счетов."},{key:"enabled",label:"Ежемесячная подготовка включена",type:"checkbox"},optionField("approver","Кто подтверждает отправку",contacts,false),{key:"day",label:"День месяца (1–28)",type:"number",required:true},{key:"hour",label:"Час (Екатеринбург)",type:"number",required:true},{key:"minute",label:"Минута",type:"number",required:true},{key:"service_chat",label:"Chat ID группы услуг",type:"number"},{key:"service_thread",label:"ID топика услуг",type:"number"},{key:"expense_chat",label:"Chat ID группы расходов",type:"number"},{key:"expense_thread",label:"ID топика расходов",type:"number"},{key:"offer_chat_id",label:"Chat ID группы предложений",type:"number"},{key:"offer_thread_id",label:"ID топика активных предложений",type:"number"},{key:"offer_claimed_thread_id",label:"ID топика взятых предложений",type:"number"},{key:"offer_released_thread_id",label:"ID топика освобождений и изменений",type:"number",hint:"Сюда приходит уведомление. Сама смена возвращается в активные предложения."},{key:"offer_enabled",label:"Принимать и публиковать предложения смен",type:"checkbox",hint:"Укажите три разных топика, отличных от услуг и расходов. Боту нужны права отправлять фото и удалять сообщения."}]});}
  function editOrganization(row?:Item) {setEditor({title:row?"Организация":"Новая организация",resource:"organizations",legacy:true,values:row||{isActive:true,excelSheet:"Основной"},fields:[{key:"name",label:"Название",required:true},{key:"aliases",label:"Алиасы через запятую",type:"aliases"},{key:"excelSheet",label:"Лист старого отчёта",required:true,hint:"Новые счета используют настройки листов услуг."},{key:"isActive",label:"Активна",type:"checkbox"}]});}
  function editEmployee(row?:Item) {
    const options=(data?.contacts||[]).map(c=>({value:c.user_id,label:`${c.name}${c.username?` · @${c.username}`:""} · ${c.user_id}`}));
    const missing=row?.telegramUserId&&!options.some(c=>c.value===row.telegramUserId);
    if(missing)options.push({value:row!.telegramUserId,label:`${row!.telegramUserId} · ещё не написал боту /start`});
    setEditor({title:row?"Сотрудник":"Новый сотрудник",resource:"employees",legacy:true,values:row||{isActive:true,defaultWorkType:"small_admin",sortOrder:100},fields:[
      {key:"shortName",label:"Короткое имя",required:true},{key:"fullName",label:"Полное имя"},
      {...optionField("telegramUserId","Пользователь Telegram",options,false),hint:"Выберите человека, написавшего боту /start. Чтобы снять привязку, выберите «Не выбрано». При взятой смене сначала снимите сотрудника со смены."},
      {key:"telegramUsername",label:"Telegram username",hint:"При выборе нового пользователя заполнится из контакта бота."},
      {key:"aliases",label:"Алиасы",type:"aliases"},{key:"isActive",label:"Активен",type:"checkbox"}]});
  }
  function telegramLabel(employee:Item) {
    const contact=employee.telegram_contact;
    return contact?`${contact.name}${contact.username?` · @${contact.username}`:""} · ${contact.user_id}`:employee.telegram_user_id?`${employee.telegram_user_id} · попросите написать боту /start`:"Telegram не привязан";
  }
  async function saveEditor(values:Record<string,any>) {if(!editor)return;if(editor.resource==="settings"){const {offer_enabled,offer_chat_id,offer_thread_id,offer_claimed_thread_id,offer_released_thread_id,...rest}=values;values={...rest,offer_config:{enabled:!!offer_enabled,chat_id:offer_chat_id,thread_id:offer_thread_id,claimed_thread_id:offer_claimed_thread_id,released_thread_id:offer_released_thread_id}};}const path=editor.resource+"/"+(values.id && editor.resource!=="settings"?values.id+"/":"");const result=await run(()=>api(path,values.id || editor.resource==="settings"?"PUT":"POST",values,editor.legacy));if(result)setEditor(null);}
  async function prepareBatch(replaces?:Item) {const ids=replaces?.organization_ids || ((page==="invoices"?invoiceFilters.organization:org)?[Number(page==="invoices"?invoiceFilters.organization:org)]:orgOptions.map(o=>o.value));if(!ids.length){setError("Сначала добавьте организацию.");return;}const result=await run(()=>api("batches/","POST",{start:replaces?.start||start,end:replaces?.end||end,organizations:ids,replaces:replaces?.id}),"Отчёты подготовлены. Проверьте их перед отправкой.");if(result){setLinkedBatch(result);setSelectedBatch(result.id);setSelectedInvoice(null);setPage("invoices");}}
  function openBatchEditor() {setEditor({title:"Подготовить отчёты",resource:"batch-draft",values:{start,end,organizations:(page==="invoices"?invoiceFilters.organization:org)?[Number(page==="invoices"?invoiceFilters.organization:org)]:orgOptions.map(o=>o.value)},fields:[{key:"start",label:"Начало периода",type:"date",required:true},{key:"end",label:"Конец периода",type:"date",required:true},{key:"organizations",label:"Организации",type:"multi",options:orgOptions,hint:"Выберите одну или несколько организаций. Для нескольких используйте Ctrl / Cmd."}]});}
  async function approveBatch(batch:Item) {const total=batch.invoices.reduce((s:number,i:Item)=>s+Number(i.total),0);if(!window.confirm(`Отправить счета владельцам?\nПериод: ${dateLabel(batch.start)} — ${dateLabel(batch.end)}\nОрганизаций: ${batch.invoices.length}\nИтого: ${rub(total)}\nПодтверждается версия ${batch.version}.`))return;const result=await run(()=>api(`batches/${batch.id}/approve/`,"POST",{version:batch.version}),"Подтверждение обработано");if(result)setNotice(result.approved?"Счета подтверждены и поставлены в очередь отправки.":result.message);}
  async function removeRecord(row:Item) {if(!window.confirm(`Удалить запись «${row.description || row.service_name}» на ${rub(row.amount)}?\n\nДоли остальных участников почасовой смены будут пересчитаны. Для подтверждённого счёта это корректировка: прежний Excel сохранится, потребуется новая версия счёта.`))return;await run(()=>api(`records/${row.id}/`,"DELETE",{correction:true}),"Запись удалена");}
  async function archiveCatalog(resource:"services"|"rates",row:Item) {
    const title=resource==="services"?`услугу «${row.name}»`:`тариф «${serviceName(row.service)} · ${orgName(row.organization)}»`;
    if(!window.confirm(`Удалить ${title}?\nЭлемент будет убран из действующих настроек. Исторические работы, суммы и счета сохранятся. Восстановить можно через «Показать удалённые и отключённые», включив флажок «Активно».`))return;
    await run(()=>api(`${resource}/${row.id}/`,"DELETE"),resource==="services"?"Услуга удалена. История сохранена.":"Тариф удалён. История сохранена.");
  }
  async function openPaymentInvoice(invoice:Item) {
    const sequence=++invoiceOpenSequence.current;
    setBusy(true);setError("");
    try {const batch=await api(`batches/${invoice.batch}/`);if(sequence!==invoiceOpenSequence.current)return;revealInvoice.current=true;setLinkedBatch(batch);setSelectedBatch(batch.id);setSelectedInvoice(invoice.id);setPage("invoices");}
    catch(error) {if(sequence===invoiceOpenSequence.current)setError(error instanceof Error?error.message:"Не удалось открыть счёт");}
    finally {if(sequence===invoiceOpenSequence.current)setBusy(false);}
  }
  function inspectHistory(row:Item) {setHistory(row);}
  const filtered = records;
  const isJournal = page === "records" || page === "expenses";
  const journalFilterCount = Number(!!org) + Number(!!search.trim()) + Number(ordering !== "-date") + Object.entries(journalFilters).filter(([key,value]) => value && (page !== "expenses" || !["employee","service","kind"].includes(key))).length;
  const organizationFilterOptions = (data?.organization_list || []).map(o => ({value:o.id,label:o.name+(o.active?"":" (архив)")}));
  const rateQuery = rateSearch.trim().toLocaleLowerCase("ru-RU");
  const filteredServices = (data?.services || []).filter(service=>showArchived||service.active);
  const filteredRates = (data?.rates || []).filter(rate => {
    if (!showArchived && (!rate.active || !data?.services.find(service=>service.id===rate.service)?.active)) return false;
    if (rateOrganization && String(rate.organization) !== rateOrganization) return false;
    const service = data?.services.find(s => s.id === rate.service);
    return !rateQuery || [service?.name, ...(service?.aliases || []), orgName(rate.organization)].some(value => String(value || "").toLocaleLowerCase("ru-RU").includes(rateQuery));
  });
  const changeOrdering = (value:string) => {setOrdering(value);setOffset(0);};
  const resetFilters = () => {setJournalFilters({...emptyJournalFilters});setSearch("");setDebouncedSearch("");setOrg("");setOrdering("-date");setOffset(0);};
  async function removeInvoice(invoice:Item) {if(!window.confirm(`Полностью удалить счёт №${invoice.id} организации «${invoice.organization_name}» на ${rub(invoice.total)}?\nСчёт и файл исчезнут с сайта, исходные работы останутся. Уже доставленный файл останется в Telegram.`))return;await run(()=>api(`invoices/${invoice.id}/`,"DELETE"),"Счёт удалён с сайта. Работы сохранены.");}
  async function removeBatch(batch:Item) {if(!window.confirm(`Полностью удалить пакет №${batch.id} и все его счета (${batch.invoices.length})?\nИсходные работы сохранятся. Уже доставленные файлы останутся в Telegram.`))return;const result=await run(()=>api(`batches/${batch.id}/`,"DELETE"),"Пакет и его счета удалены");if(result)setSelectedBatch(null);}
  const selected=linkedBatch?.id===selectedBatch?linkedBatch:null;
  const titles:Record<string,[string,string]>={earnings:["Главный отчёт","Заработок каждого сотрудника по всем организациям."],overview:["Всё сходится.","Работы, расходы и счета — в одном месте."],records:["Журнал работ","Типовые услуги и разовые задачи вашей команды."],expenses:["Расходы","Каждая покупка привязана к своей организации."],invoices:["Счета","Подготовка, отправка и оплата счетов организаций."],services:["Услуги и тарифы","Ваши правила расчёта — для каждой организации."],settings:["Настройки","Организации, команда и подключение Telegram."]};
  const empty = (text:string) => <div className="empty"><span className="empty-symbol">↗</span><h2>Здесь пока нет записей</h2><p>{text}</p></div>;
  titles.offers = ["Предложения смен и услуг", "Планирование по скриншотам YCLIENTS и распределение сотрудников."];
  const table = (rows:Item[]) => <div className="table-scroll"><table><thead><tr>
    <SortHeading field="date" label="Дата" ordering={ordering} onChange={changeOrdering}/>
    <SortHeading field="organization" label="Организация" ordering={ordering} onChange={changeOrdering}/>
    <SortHeading field="service" label="Работа" ordering={ordering} onChange={changeOrdering}/>
    <SortHeading field="employee" label="Исполнитель" ordering={ordering} onChange={changeOrdering}/>
    <SortHeading field="units" label="Объём" ordering={ordering} onChange={changeOrdering}/>
    <SortHeading field="amount" label="Сумма" ordering={ordering} onChange={changeOrdering} right/>
    <th>Статус</th><th><span className="sr-only">Действия</span></th>
  </tr></thead><tbody>{rows.map(r=><tr key={r.id}>
    <td><strong>{dateLabel(r.date)}</strong></td><td>{r.organization_name}</td>
    <td><strong>{r.kind==="service"?r.service_name:r.description}</strong><small>{r.kind==="service"?r.description:labels[r.kind]}</small><SharedShiftDetails group={r.daily_group}/></td>
    <td>{r.employee_name || "—"}</td><td>{["time","quantity"].includes(r.input_type)?r.units:"—"}{r.start_time && <small>{r.start_time.slice(0,5)}–{r.end_time?.slice(0,5)}</small>}</td>
    <td className="right amount">{rub(r.amount)}</td><td><span className={`pill ${r.review || r.error?"warning":""}`}>{r.review || r.error?"Проверить":"Учтено"}</span>{r.error && <small>{r.error}</small>}</td>
    <td><div className="row-actions">
      <span className="action-tooltip" data-tooltip="Изменить запись"><button disabled={busy} onClick={()=>editRecord(r.kind,r)} title="Изменить запись" aria-label="Изменить запись">✎</button></span>
      <span className="action-tooltip" data-tooltip="История изменений"><button disabled={busy} onClick={()=>inspectHistory(r)} title="История изменений" aria-label="История изменений">◷</button></span>
      <span className="action-tooltip" data-tooltip="Удалить запись"><button disabled={busy} onClick={()=>removeRecord(r)} title="Удалить запись" aria-label="Удалить запись">×</button></span>
    </div></td>
  </tr>)}</tbody></table></div>;
  return <div className="accounting"><aside className="sidebar"><Link className="brand" href="/">свод<span>учёт студий</span></Link><nav>{[["overview","Обзор"],["records","Журнал работ"],["expenses","Расходы"],["offers","Предложения смен и услуг"],["invoices","Счета"],["earnings","Главный отчёт"],["services","Услуги и тарифы"],["settings","Настройки"]].map(([id,title])=><div key={id} className={id==="invoices"?"invoice-nav":"nav-item"}><button className={(page===id?"active ":"")+(id==="invoices"?"invoice-nav-label":"")} onClick={()=>{setPage(id);setOffset(0);setSearch("");}}><Icon name={id}/>{title}</button>{id==="invoices"&&<button className="nav-debt-count" aria-label={`Показать неоплаченные счета: ${payments.open_count}`} title="Все неоплаченные счета" onClick={()=>{setPage("invoices");setInvoiceFilters({...emptyInvoiceFilters,payment_state:"open"});setPaymentOffset(0);setSelectedBatch(null);setLinkedBatch(null);}}>{payments.open_count}</button>}</div>)}</nav><div className="sidebar-foot"><span className="status-dot"/>Екатеринбург · UTC+5<br/><span>Работа учтена.<br/>Всё под контролем.</span></div></aside>
    <main><header><span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО <span className="slash">/</span> {page==="overview"?"ОБЗОР":titles[page][0].toUpperCase()}</span><button className="button secondary compact" disabled={loading || busy} onClick={()=>run(load,"Данные обновлены")}>↻ Обновить</button></header>
    <div className="page-title"><div><h1>{titles[page][0]}</h1><p>{titles[page][1]}</p></div><div className="actions">{["overview","records"].includes(page)&&<><button className="button secondary" onClick={()=>editRecord("oneoff")}>Разовая работа</button><button className="button" onClick={()=>editRecord("service")}>+ Записать услугу</button></>}{page==="expenses"&&<button className="button" onClick={()=>editRecord("expense")}>+ Добавить расход</button>}{page==="invoices"&&<button className="button" disabled={busy} onClick={openBatchEditor}>Подготовить отчёты</button>}</div></div>
    {error&&<div role="alert" className="alert">{error}<button className="icon-button" onClick={()=>setError("")} title="Скрыть ошибку" aria-label="Скрыть ошибку">×</button></div>}{notice&&<div role="status" className="notice">{notice}<button className="icon-button" onClick={()=>setNotice("")} title="Скрыть уведомление" aria-label="Скрыть уведомление">×</button></div>}
    {page==="earnings"&&<EarningsReport month={earningsMonth} maxMonth={isoToday().slice(0,7)} onMonthChange={setEarningsMonth} data={earnings} enabled={data?.settings.earnings_enabled!==false} approver={data?.contacts.find(c=>c.id===data.settings.approver)?.name} busy={busy} onRetry={(id,unknown)=>{
      if(unknown&&!window.confirm("Telegram мог уже доставить главный отчёт. Проверьте чат. Повторить с риском дубликата?"))return;
      run(()=>api(`earnings-deliveries/${id}/retry/`,"POST",{confirm_duplicate_risk:unknown}),"Главный отчёт поставлен в очередь отправки");
    }}/>}
    {["overview","records","expenses"].includes(page)&&<div className="filters">
      <MonthPicker value={month} max={isoToday().slice(0,7)} onChange={value=>{setMonth(value);setOffset(0);}}/>
      {isJournal ? <FilterToggle open={filtersOpen} count={journalFilterCount} controls="journal-filters" onClick={()=>setFiltersOpen(open=>!open)}/> : <label><span>Организация</span><select aria-label="Организация" value={org} onChange={e=>{setOrg(e.target.value);setOffset(0);}}><option value="">Все организации</option>{organizationFilterOptions.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></label>}
      <span className="filter-tail">{loading?"Обновляем…":`${summary.count} записей за месяц`}</span>
    </div>}
    {loading&&!data?<div className="panel empty">Загружаем рабочее пространство…</div>:<>
    {page==="overview"&&<><div className="metrics"><div className="metric"><span>Учтено за месяц</span><strong>{rub(summary.total)}</strong><small>Услуги и расходы</small></div><div className="metric"><span>Рабочие записи</span><strong>{summary.count}</strong><small>В выбранном месяце</small></div><div className="metric"><span>Требуют внимания</span><strong className={summary.review?"amber":""}>{summary.review}</strong><small>Проверить перед отправкой</small></div><div className="metric accent"><span>Счета на проверке</span><strong>{batches.filter(b=>b.state==="draft").length}</strong><button onClick={()=>setPage("invoices")}>Открыть счета ↗</button></div></div><section className="panel"><div className="panel-heading"><h2>Последние записи</h2><button className="text-button" onClick={()=>setPage("records")}>Весь журнал →</button></div>{records.length?table(records.slice(0,7)):empty("Добавьте услугу на сайте или отправьте сообщение в настроенный топик бота.")}</section><div className="two-columns"><section className="panel"><div className="panel-heading"><h2>Начисления команде</h2><span className="muted">Без расходов</span></div>{summary.employee_totals.map((e:any,i:number)=><div className="organization-row" key={i}><span className="org-avatar">{(e.employee_name||"?")[0]}</span><strong>{e.employee_name||"Без исполнителя"}</strong><span className="row-total">{rub(e.total)}</span></div>)}{!summary.employee_totals.length&&<p className="empty">Нет начислений за этот месяц.</p>}</section><section className="panel tip-panel"><span className="eyebrow">TELEGRAM → УЧЁТ</span><h2>Одно сообщение.<br/>Несколько услуг.</h2><code>12.04 10:00–14:00 Фокус + 2 сопр</code><p>Смена попадёт в Фокус, сопровождения — в организацию из настроек услуги.</p><button className="text-button" onClick={()=>setPage("services")}>Настроить услуги →</button></section></div></>}
    {["records","expenses"].includes(page)&&<>{filtersOpen&&<JournalFilters organizations={organizationFilterOptions} organization={org} onOrganization={value=>{setOrg(value);setOffset(0);}} filters={journalFilters} employees={(data?.employees||[]).map(e=>({value:e.id,label:e.name+(e.active?"":" (архив)")}))} services={(data?.services||[]).map(e=>({value:e.id,label:e.name}))} expenses={page==="expenses"} search={search} onSearch={value=>{setSearch(value);setOffset(0);}} onChange={value=>{setJournalFilters(value);setOffset(0);}} ordering={ordering} onOrdering={changeOrdering} onReset={resetFilters}/>}<section className="panel"><div className="panel-heading"><h2>{page==="expenses"?"Расходы за месяц":"Работы за месяц"}</h2>{page==="records"&&<button className="button secondary compact" disabled={busy} onClick={async()=>{const result=await run(()=>api("recalculate/","POST",{start,end}),"");if(result)setRecalc(result);}}>Предпросмотр пересчёта</button>}</div>{filtered.length?table(filtered):empty(page==="expenses"?"Добавьте расход или отправьте его в отдельный топик Telegram.":"Добавьте типовую услугу или разовую работу с готовой суммой.")}<div className="pagination"><button className="button secondary compact" disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-pageSize))}>← Назад</button><span>{summary.count?offset+1:0}–{Math.min(offset+pageSize,summary.count)} из {summary.count} · Итого: {rub(summary.total)}</span><button className="button secondary compact" disabled={offset+pageSize>=summary.count} onClick={()=>setOffset(offset+pageSize)}>Далее →</button></div></section></>}
    {page==="offers"&&<OffersConsole onOpenSettings={()=>{setSettingsTab("telegram");setPage("settings");}}/>}
    {page==="invoices"&&<InvoicesList data={payments} filters={invoiceFilters} onFilters={value=>{setInvoiceFilters(value);setPaymentOffset(0);}} organizations={data?.organization_list||[]} offset={paymentOffset} pageSize={pageSize} loading={paymentLoading} error={paymentError} onOffset={setPaymentOffset} onOpen={openPaymentInvoice} contacts={data?.contacts||[]}/>}

    {page==="services"&&<><div className="tabs">{[["services","Услуги"],["rates","Тарифы организаций"],["accruals","Автоначисления"],["preferences","Смена сотрудника"]].map(([key,label])=><button key={key} className={serviceTab===key?"active":""} onClick={()=>setServiceTab(key)}>{label}</button>)}</div>{["services","rates"].includes(serviceTab)&&<label className="archive-toggle"><input type="checkbox" checked={showArchived} onChange={e=>setShowArchived(e.target.checked)}/>Показать удалённые и отключённые</label>}{serviceTab==="rates"&&<>
      <div className="filters"><FilterToggle open={rateFiltersOpen} count={Number(!!rateOrganization)+Number(!!rateQuery)} controls="rate-filters" onClick={()=>setRateFiltersOpen(open=>!open)}/><span className="filter-tail" role="status">Найдено тарифов: {filteredRates.length} из {data?.rates.length||0}</span></div>
      {rateFiltersOpen&&<div className="journal-filters rate-filters" id="rate-filters">
        <label className="journal-search"><span>Поиск тарифов</span><input type="search" value={rateSearch} onChange={e=>setRateSearch(e.target.value)} placeholder="Услуга, алиас или организация…"/></label>
        <label><span>Организация</span><select value={rateOrganization} onChange={e=>setRateOrganization(e.target.value)}><option value="">Все организации</option>{organizationFilterOptions.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
        <button type="button" className="button secondary compact reset-filters" onClick={()=>{setRateSearch("");setRateOrganization("");}}>Сбросить фильтры</button>
      </div>}
    </>}<section className="panel"><div className="panel-heading"><h2>{serviceTab==="services"?"Справочник услуг":serviceTab==="rates"?"Стоимость и периоды":serviceTab==="accruals"?"Регулярные услуги":"Услуга для интервала без названия"}</h2><button className="button compact" onClick={()=>serviceTab==="services"?editService():serviceTab==="rates"?editRate():serviceTab==="accruals"?editAccrual():setEditor({title:"Смена сотрудника",resource:"preferences",values:{},fields:[optionField("employee","Сотрудник",employees),optionField("service","Услуга по умолчанию",serviceOptions)]})}>+ Добавить</button></div><div className="table-scroll"><table><thead><tr>{(serviceTab==="services"?["Услуга","Ввод / начисление","Организация по умолчанию","Лист Excel","Статус",""]:serviceTab==="rates"?["Услуга / организация","Расчёт","Ставка","Ограничения","Период",""]:serviceTab==="accruals"?["Услуга","Организация","Сотрудник","Период","Статус",""]:["Сотрудник","Услуга",""]).map((h,i)=><th key={i}>{h}</th>)}</tr></thead><tbody>
    {serviceTab==="services"&&filteredServices.map(s=><tr key={s.id}><td><strong>{s.name}</strong><small>{s.aliases.join(", ")}</small></td><td>{labels[s.input_type]}<small>{labels[s.frequency]}</small></td><td>{orgName(s.default_organization)}</td><td><span className="pill neutral">{s.sheet||"Основной"}</span></td><td><span className={`pill ${s.active?"":"neutral"}`}>{s.active?"Активна":"В архиве"}</span></td><td><div className="actions"><button className="text-button" onClick={()=>editService(s)}>{s.active?"Настроить":"Восстановить / настроить"}</button>{s.active&&<button className="button danger-outline compact" disabled={busy} onClick={()=>archiveCatalog("services",s)}>Удалить</button>}</div></td></tr>)}
    {serviceTab==="rates"&&filteredRates.map(r=><tr key={r.id}><td><strong>{serviceName(r.service)}</strong><small>{orgName(r.organization)}</small></td><td>{labels[r.calculation]}{!r.active&&<small>Отключён</small>}</td><td className="amount">{rub(r.price)}</td><td>{r.minimum?`от ${rub(r.minimum)}`:"—"}<small>{r.maximum?`до ${rub(r.maximum)}`:""}</small></td><td>{dateLabel(r.start)}<small>{r.end?`до ${dateLabel(r.end)}`:"Без даты окончания"}</small></td><td><div className="actions"><button className="text-button" onClick={()=>editRate(r)}>{r.active?"Изменить":"Восстановить / изменить"}</button>{r.active&&<button className="button danger-outline compact" disabled={busy} onClick={()=>archiveCatalog("rates",r)}>Удалить</button>}</div></td></tr>)}
    {serviceTab==="rates"&&!filteredRates.length&&<tr><td colSpan={6} className="empty">{rateOrganization||rateQuery?"Тарифы не найдены. Измените условия или сбросьте фильтры.":"Тарифов пока нет. Добавьте первый тариф организации."}</td></tr>}
    {serviceTab==="accruals"&&data?.accruals.map(a=><tr key={a.id}><td>{serviceName(a.service)}</td><td>{orgName(a.organization)}</td><td>{data.employees.find(e=>e.id===a.employee)?.name||"Не назначен"}</td><td>{dateLabel(a.start)} — {dateLabel(a.end)}</td><td>{a.active?"Включено":"Отключено"}</td><td><button className="text-button" onClick={()=>editAccrual(a)}>Изменить</button></td></tr>)}
    {serviceTab==="preferences"&&data?.preferences.map(p=><tr key={p.id}><td>{data.employees.find(e=>e.id===p.employee)?.name}</td><td>{serviceName(p.service)}</td><td><button className="text-button" onClick={()=>setEditor({title:"Смена сотрудника",resource:"preferences",values:p,fields:[optionField("employee","Сотрудник",employees),optionField("service","Услуга",serviceOptions)]})}>Изменить</button></td></tr>)}
    </tbody></table></div></section><div className="subtle-note">Разовые работы не добавляются в справочник и всегда попадают на основной лист. Для телефонов задайте фиксированный тариф и ежедневное автоначисление нужной организации.</div></>}
    {page==="invoices"&&selected&&<div className="invoice-detail" ref={invoiceDetailRef} tabIndex={-1}><button className="text-button" onClick={()=>{invoiceOpenSequence.current++;revealInvoice.current=false;setBusy(false);setSelectedBatch(null);setLinkedBatch(null);setSelectedInvoice(null);}}>Закрыть карточку счёта</button><div>{selected&&<><section className="panel"><div className="panel-heading"><div><h2>{selectedInvoice?`Счёт №${selectedInvoice} · `:""}Пакет №{selected.id}</h2><span className="muted">{dateLabel(selected.start)} — {dateLabel(selected.end)}</span></div><div className="actions">{selected.state==="draft"?<><button className="button secondary compact" disabled={busy} onClick={()=>run(()=>api(`batches/${selected.id}/refresh/`,"POST",{}),"Отчёты обновлены")}>Обновить</button><button className="button compact" disabled={busy} onClick={()=>approveBatch(selected)}>Подтвердить отправку пакета · {selected.invoices.length} счетов</button></>:<button className="button secondary compact" disabled={busy} onClick={()=>prepareBatch(selected)}>Новая версия</button>}<button className="button danger-outline compact" disabled={busy} onClick={()=>removeBatch(selected)} title="Удалить пакет и все его счета">Удалить пакет</button></div></div>{selected.invoices.map((inv:Item)=><div className={`invoice-card ${inv.id===selectedInvoice?"selected-invoice":""}`} key={inv.id} id={`invoice-${inv.id}`}><div className="invoice-top"><div><h2>{inv.organization_name}</h2><small>Счёт №{inv.id} · версия {inv.version}{inv.replaces?` · заменяет №${inv.replaces}`:""}</small></div><strong className="invoice-total">{rub(inv.total)}</strong></div><PaymentStatus invoice={inv} contacts={data?.contacts||[]}/><div className="invoice-links"><a className="button secondary compact" href={`${base}invoices/${inv.id}/file/`}>↓ Скачать Excel</a><button className="button danger-outline compact" disabled={busy} onClick={()=>removeInvoice(inv)} title="Полностью удалить этот счёт и его Excel с сайта">Удалить счёт</button><span className="muted">Получатели: {inv.recipients.map((id:number)=>data?.contacts.find(c=>c.user_id===id)?.name||id).join(", ")||"не назначены"}</span></div>{inv.errors.map((err:string,i:number)=><div className="inline-error" key={i}>{err}</div>)}<details><summary>Состав счёта · {inv.lines.length} записей</summary><div className="table-scroll"><table><thead><tr><th>Дата / работа</th><th>Лист</th><th className="right">Сумма</th></tr></thead><tbody>{inv.lines.map((line:Item)=><tr key={line.id}><td>{line.kind==="service"?line.service_name:line.description}<small>{dateLabel(line.date)} · {line.employee_name}</small></td><td>{line.sheet}</td><td className="right">{rub(line.amount)}</td></tr>)}</tbody></table></div></details><div className="adjustments">{inv.adjustments.map((a:any,i:number)=><div key={i}>{a.description}<strong>{rub(a.amount)}</strong>{selected.state==="draft"&&<button className="icon-button" title="Удалить корректировку" aria-label="Удалить корректировку" onClick={()=>run(()=>api(`invoices/${inv.id}/`,"PUT",{adjustments:inv.adjustments.filter((_:any,j:number)=>j!==i)}))}>×</button>}</div>)}{selected.state==="draft"&&<button className="text-button" onClick={()=>setEditor({title:"Доплата или скидка",resource:`adjustment:${inv.id}`,values:{amount:"",description:""},fields:[{key:"description",label:"Пояснение",required:true},{key:"amount",label:"Сумма, ₽ (минус для скидки)",type:"number",required:true}]})}>+ Доплата или скидка</button>}</div></div>)}</section><section className="panel"><div className="panel-heading"><h2>Доставка</h2></div>{selected.deliveries.length?<div className="table-scroll"><table><thead><tr><th>Кому / назначение</th><th>Состояние</th><th/></tr></thead><tbody>{selected.deliveries.map((d:Item)=><tr key={d.id}><td>{data?.contacts.find(c=>c.user_id===d.recipient)?.name||d.recipient}<small>{labels[d.purpose]} · версия {d.version}</small></td><td><span className={`pill ${["failed","unknown"].includes(d.state)?"warning":""}`}>{labels[d.state]}</span>{d.error&&<small>{d.error}</small>}</td><td>{["failed","unknown"].includes(d.state)&&<button className="text-button" disabled={busy} onClick={()=>{if(d.state==="unknown"&&!window.confirm("Telegram мог уже доставить сообщение. Проверили чат и хотите повторить отправку?"))return;run(()=>api(`deliveries/${d.id}/retry/`,"POST",{confirm_duplicate_risk:d.state==="unknown"}),"Повтор поставлен в очередь");}}>Повторить</button>}</td></tr>)}</tbody></table></div>:<p className="empty">Доставка появится после назначения утверждающего или подтверждения на сайте.</p>}</section></>}</div></div>}
    {page==="settings"&&<><div className="tabs">{[["organizations","Организации"],["employees","Команда"],["telegram","Telegram и расписание"]].map(([key,label])=><button key={key} className={settingsTab===key?"active":""} onClick={()=>setSettingsTab(key)}>{label}</button>)}</div>{settingsTab==="organizations"&&<section className="panel"><div className="panel-heading"><h2>Организации и получатели</h2><button className="button compact" onClick={()=>editOrganization()}>+ Организация</button></div>{data?.organization_list.map(o=><div className="organization-row" key={o.id}><span className="org-avatar">{o.name[0]}</span><div><strong>{o.name}</strong><small>{o.active?"Активна":"Отключена"} · {data.organizations.find(p=>p.organization===o.id)?.monthly?"Ежемесячная подготовка":"Ручная подготовка"}</small></div><div className="actions push"><button className="text-button" onClick={async()=>{const row=await run(()=>api(`organizations/${o.id}/`,"GET",undefined,true),"");if(row)editOrganization(row);}}>Изменить</button><button className="button secondary compact" onClick={()=>editProfile(o)}>Получатели и счёт</button></div></div>)}</section>}{settingsTab==="employees"&&<section className="panel"><div className="panel-heading"><h2>Команда</h2><button className="button compact" onClick={()=>editEmployee()}>+ Сотрудник</button></div>{data?.employees.map(e=><div className="organization-row" key={e.id}><span className="org-avatar">{e.name[0]}</span><div><strong>{e.name}</strong><small>{telegramLabel(e)}</small>{e.telegram_conflict&&<small className="inline-error">Конфликт: ID указан у нескольких активных сотрудников</small>}</div><span className="muted">{e.active?"Активен":"Отключён"}</span><button className="text-button push" onClick={async()=>{const row=await run(()=>api(`employees/${e.id}/`,"GET",undefined,true),"");if(row)editEmployee(row);}}>Изменить</button></div>)}</section>}{settingsTab==="telegram"&&<><section className="panel"><div className="panel-heading"><h2>Бот и ежемесячная подготовка</h2><button className="button compact" onClick={editSchedule}>Настроить</button></div><div className="settings-summary"><div><span>Подготовка</span><strong>{data?.settings.enabled?"Включена":"Выключена"}</strong></div><div><span>Когда</span><strong>{data?.settings.day}-го числа, {String(data?.settings.hour).padStart(2,"0")}:{String(data?.settings.minute).padStart(2,"0")}</strong></div><div><span>Подтверждает</span><strong>{data?.contacts.find(c=>c.id===data.settings.approver)?.name||"Не назначен"}</strong></div><div><span>Топик услуг</span><strong>{data?.settings.service_chat||"Не настроен"} / {data?.settings.service_thread||"Вся группа"}</strong></div><div><span>Топик расходов</span><strong>{data?.settings.expense_chat||"Не настроен"} / {data?.settings.expense_thread||"Вся группа"}</strong></div><div><span>Активные предложения</span><strong>{data?.offer_config?.chat_id||"Не настроен"} / {data?.offer_config?.thread_id||"Не настроен"}</strong></div><div><span>Взятые предложения</span><strong>{data?.offer_config?.claimed_thread_id||"Не настроен"}</strong></div><div><span>Освобождения и изменения</span><strong>{data?.offer_config?.released_thread_id||"Не настроен"}</strong></div><div><span>Приём предложений</span><strong>{data?.offer_config?.enabled?"Включён":"Выключен"}</strong></div></div></section><section className="panel"><div className="panel-heading"><h2>Подключённые пользователи</h2><span className="muted">Появляются после /start в личном чате бота</span></div>{data?.contacts.map(c=><div className="organization-row" key={c.id}><span className="org-avatar">{c.name[0]}</span><strong>{c.name}</strong><span className="muted">@{c.username||"—"} · {c.user_id}</span></div>)}{!data?.contacts.length&&<p className="empty">Попросите получателя открыть бота и нажать «Запустить».</p>}</section></>}</>}
    </>}
    {history&&<HistoryDialog key={history.id} record={history} data={data} onClose={()=>setHistory(null)}/>}
    {recalc&&<RecalculationPreview preview={recalc} busy={busy} onClose={()=>setRecalc(null)} onApply={async()=>{const r=await run(()=>api("recalculate/","POST",{start,end,fingerprint:recalc.fingerprint}),"Пересчёт выполнен");if(r)setRecalc(null);}}/>}
    {editor?.resource==="records"&&editor.values.kind==="service"?<ServiceRecordEditor key={editor.resource+String(editor.values.id)} editor={editor} data={data} busy={busy} error={error} onClose={()=>setEditor(null)} onSave={saveEditor}/>:editor&&<Modal key={editor.resource+String(editor.values.id)} editor={editor} busy={busy} error={error} onClose={()=>setEditor(null)} onSave={async values=>{if(editor.resource==="batch-draft"){const result=await run(()=>api("batches/","POST",values));if(result){setLinkedBatch(result);setSelectedBatch(result.id);setSelectedInvoice(null);setEditor(null);}}else if(editor.resource.startsWith("adjustment:")){const id=Number(editor.resource.split(":")[1]);const inv=selected?.invoices.find((i:Item)=>i.id===id);const result=await run(()=>api(`invoices/${id}/`,"PUT",{adjustments:[...(inv?.adjustments||[]),values]}));if(result)setEditor(null);}else await saveEditor(values);}}/>}
    </main></div>;
}
