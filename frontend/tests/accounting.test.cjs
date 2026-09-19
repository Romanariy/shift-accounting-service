// Exercise React effects and interactions without a browser or a live database.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const ts = require("typescript");
const React = require("react");
const { create, act } = require("react-test-renderer");

for (const ext of [".ts", ".tsx"]) require.extensions[ext] = (module, filename) => {
  const result = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: filename,
  });
  module._compile(result.outputText, filename);
};
const originalLoad = Module._load;
Module._load = function (name, ...args) {
  if (name === "next/link") return ({ href, children, ...props }) => React.createElement("a", { href, ...props }, children);
  return originalLoad.call(this, name, ...args);
};
const AccountingApp = require("../src/components/AccountingApp").default;
const HistoryDialog = require("../src/components/HistoryDialog").default;
const useNotice = require("../src/components/useNotice").default;
const SharedShiftDetails = require("../src/components/SharedShiftDetails").default;
const RecalculationPreview = require("../src/components/RecalculationPreview").default;
const ServiceRecordEditor = require("../src/components/ServiceRecordEditor").default;
const { serviceRecordPayload } = require("../src/components/ServiceRecordEditor");
const AccountingModal = require("../src/components/AccountingModal").default;
Module._load = originalLoad;

const text = node => typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join("") : node?.children?.map(text).join("") || "";
const button = (root, label) => root.findAllByType("button").find(node => text(node) === label);
const select = (root, label) => root.findAllByType("label").find(node => node.findAllByType("span").some(span => text(span) === label)).findByType("select");
const response = (data, ok = true) => ({ ok, json: async () => data });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const record = { id: 1, date: "2026-09-07", description: "Починил дверь", kind: "oneoff", organization_name: "Фокус", employee_name: "Рамис" };
const historyEntry = { id: 1, created_at: "2026-09-07T05:00:00Z", action: "update", actor: "Рамис", diff: { amount: { from: "100", to: "200" } } };

test("notice lasts 3 seconds; identical messages restart the timer; dismiss and navigation clean up", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let show;
  function Harness({ page }) { const [notice, setNotice] = useNotice(page); show = setNotice; return React.createElement("p", null, notice); }
  let renderer;
  act(() => { renderer = create(React.createElement(Harness, { page: "records" })); });
  t.after(() => act(() => renderer.unmount()));
  act(() => show("Сохранено"));
  act(() => t.mock.timers.tick(2500));
  assert.equal(text(renderer.toJSON()), "Сохранено");
  act(() => show("Сохранено"));
  act(() => t.mock.timers.tick(500));
  assert.equal(text(renderer.toJSON()), "Сохранено");
  act(() => t.mock.timers.tick(2499));
  assert.equal(text(renderer.toJSON()), "Сохранено");
  act(() => t.mock.timers.tick(1));
  assert.equal(text(renderer.toJSON()), "");
  act(() => show("Обновлено"));
  act(() => show(""));
  assert.equal(text(renderer.toJSON()), "");
  act(() => show("Сохранено"));
  act(() => renderer.update(React.createElement(Harness, { page: "services" })));
  assert.equal(text(renderer.toJSON()), "");
});

test("history opens immediately and displays dated before/after values with close controls", async t => {
  const pending = deferred();
  let opened = 0, closed = 0, dismissed = 0, signal;
  t.mock.method(global, "fetch", async (_, options) => { signal = options.signal; return pending.promise; });
  let renderer;
  await act(async () => { renderer = create(React.createElement(HistoryDialog, { record, data: null, onClose: () => dismissed++ }), {
    createNodeMock: element => element.type === "dialog" ? { showModal: () => opened++, close: () => closed++ } : null,
  }); });
  assert.equal(opened, 1);
  assert.match(text(renderer.toJSON()), /Загружаем историю/);
  await act(async () => pending.resolve(response([historyEntry])));
  assert.match(text(renderer.toJSON()), /Починил дверь/);
  assert.match(text(renderer.toJSON()), /БылоСтало/);
  assert.match(text(renderer.toJSON()), /100,00/);
  assert.match(text(renderer.toJSON()), /200,00/);
  assert.match(text(renderer.toJSON()), /10:00:00/); // Yekaterinburg, UTC+5
  act(() => renderer.root.findByProps({ "aria-label": "Закрыть историю" }).props.onClick());
  act(() => renderer.root.findByType("dialog").props.onCancel());
  assert.equal(dismissed, 2);
  act(() => renderer.unmount());
  assert.equal(closed, 1);
  assert.equal(signal.aborted, true);
});

test("history ignores late responses for another record; empty history and errors allow retry", async t => {
  const first = deferred(), second = deferred();
  const calls = [];
  t.mock.method(global, "fetch", (url, options) => { calls.push({ url, signal: options.signal }); return calls.length === 1 ? first.promise : second.promise; });
  let renderer;
  const props = { record, data: null, onClose() {} };
  await act(async () => { renderer = create(React.createElement(HistoryDialog, props)); });
  t.after(() => act(() => renderer.unmount()));
  await act(async () => renderer.update(React.createElement(HistoryDialog, { ...props, record: { ...record, id: 2 } })));
  assert.equal(calls[0].signal.aborted, true);
  await act(async () => second.resolve(response([])));
  await act(async () => first.resolve(response([historyEntry])));
  assert.match(text(renderer.toJSON()), /История этой записи пока пуста/);
  assert.doesNotMatch(text(renderer.toJSON()), /БылоСтало/);
  global.fetch.mock.mockImplementation(async () => response({ error: "Ошибка загрузки" }, false));
  await act(async () => renderer.update(React.createElement(HistoryDialog, { ...props, record: { ...record, id: 3 } })));
  assert.match(text(renderer.toJSON()), /Ошибка загрузки/);
  global.fetch.mock.mockImplementation(async () => response([historyEntry]));
  await act(async () => button(renderer.root, "Повторить загрузку").props.onClick());
  assert.match(text(renderer.toJSON()), /БылоСтало/);
  assert.doesNotMatch(text(renderer.toJSON()), /Ошибка загрузки/);
});

async function mountApp(t, overrides={}, resources={}) {
  const bootstrap = {
    services: [{ id: 1, name: "Сопровождение", aliases: ["сопр"], active: true, input_type: "quantity", frequency: "entry" }],
    rates: Array.from({ length: 60 }, (_, i) => ({ id: i + 1, service: 1, organization: i < 30 ? 1 : 2, calculation: "fixed", price: 100, start: "2026-01-01", active: true })),
    organization_list: [{ id: 1, name: "Фокус", active: true }, { id: 2, name: "Студия", active: false }],
    employees: [{ id: 1, name: "Рамис", active: true }], accruals: [], preferences: [], contacts: [{id:1,name:"Главный",user_id:777}], organizations: [], settings: {approver:1,earnings_enabled:true},
    ...overrides,
  };
  const calls = [];
  t.mock.method(global, "fetch", async (url, options={}) => {
    calls.push(String(url));
    if (resources.request) {const result=resources.request(String(url),options,bootstrap);if(result!==undefined)return response(result);}
    if (url.includes("bootstrap/")) return response(bootstrap);
    if (url.includes("payments/?")||url.includes("invoices/?")) return response(resources.payments||{invoices:[],count:0,open_count:0});
    if (url.includes("earnings/?")) {
      const month = new URL(url, "http://local").searchParams.get("month");
      return response({month,start:month+"-01",end:month+"-12",generated_at:month+"-12T10:00:00+05:00",total:"123.45",employees:[{employee_id:1,employee_name:"Рамис",amount:"123.45"}],review:[],unassigned:[],review_total:"0.00",unassigned_total:"0.00",delivery:{id:1,state:"unknown",recipient:777,attempts:1,error:"Проверьте чат"}});
    }
    if (url.includes("records/")) return response({ records: Array.from({ length: 50 }, (_, i) => ({ ...record, id: i + 1, amount: "100" })), count: 75, total: "7500", review: 0, employee_totals: [] });
    if (url.includes("audit/")) return response([historyEntry]);
    return response([]);
  });
  const oldWindow = global.window, oldDocument = global.document;
  global.window = { addEventListener() {}, removeEventListener() {} };
  global.document = { hidden: false };
  let renderer;
  await act(async () => { renderer = create(React.createElement(AccountingApp)); });
  t.after(() => { act(() => renderer.unmount()); global.window = oldWindow; global.document = oldDocument; });
  return { renderer, calls };
}

test("earnings report ignores journal filters, selects its own month and exports that month", async t => {
  const { renderer, calls } = await mountApp(t);
  const root = renderer.root;
  await act(async () => button(root, "Журнал работ").props.onClick());
  await act(async () => root.findByProps({ "aria-controls": "journal-filters" }).props.onClick());
  await act(async () => select(root, "Организация").props.onChange({ target: { value: "1" } }));
  await act(async () => button(root, "Главный отчёт").props.onClick());
  assert.match(text(renderer.toJSON()), /Заработок сотрудников/);
  assert.equal(root.findAllByProps({"aria-label":"Организация"}).length, 0);
  const monthPicker = () => root.findByProps({"aria-label":"Месяц журнала"});
  await act(async () => monthPicker().props.onChange({target:{value:"2026-04",validity:{valid:true}}}));
  const url = new URL(calls.filter(url=>url.includes("earnings/?")).at(-1), "http://local");
  assert.deepEqual([...url.searchParams], [["month","2026-04"]]);
  assert.equal(root.findAllByType("a").find(node=>text(node)==="Скачать Excel").props.href,"/api/shifts/ledger/earnings/?month=2026-04&format=xlsx");
  assert.match(text(renderer.toJSON()), /123,45/);
  assert.match(text(renderer.toJSON()), /Начисления без сотрудника/);
  assert.match(text(renderer.toJSON()), /Главный/);
  assert.equal(button(root,"Повторить отправку").props.disabled,false);
  let confirmations=0;
  global.window.confirm = () => {confirmations++;return false;};
  await act(async () => button(root,"Повторить отправку").props.onClick());
  assert.equal(confirmations,1);
  assert.equal(calls.filter(url=>url.includes("earnings-deliveries/")).length,0);
  global.window.confirm = () => true;
  await act(async () => button(root,"Повторить отправку").props.onClick());
  assert.equal(calls.filter(url=>url.includes("earnings-deliveries/1/retry/")).length,1);
});

test("earnings report hides previous month data if the next request fails", async t => {
  const {renderer} = await mountApp(t);
  await act(async () => button(renderer.root,"Главный отчёт").props.onClick());
  global.fetch.mock.mockImplementation(async url=>url.includes("earnings/?") ? response({error:"Ошибка отчёта"},false) : response({}));
  await act(async () => renderer.root.findByProps({"aria-label":"Месяц журнала"}).props.onChange({target:{value:"2026-03",validity:{valid:true}}}));
  assert.match(text(renderer.toJSON()), /Ошибка отчёта/);
  assert.equal(renderer.root.findAllByType("a").filter(node=>text(node)==="Скачать Excel").length,0);
});

test("employee is configured on each accrual, not on the service", async t => {
  const {renderer} = await mountApp(t, {
    services:[{id:1,name:"Телефоны",aliases:[],active:true,input_type:"mark",frequency:"daily"}],
    accruals:[{id:1,service:1,organization:1,employee:1,start:"2026-04-01",active:true}],
  });
  const root = renderer.root;
  await act(async()=>button(root,"Услуги и тарифы").props.onClick());
  await act(async()=>button(root,"Настроить").props.onClick());
  assert.doesNotMatch(text(root.findByType("dialog")),/Сотрудник/);
  await act(async()=>root.findByProps({"aria-label":"Закрыть"}).props.onClick());
  await act(async()=>button(root,"Автоначисления").props.onClick());
  assert.match(text(renderer.toJSON()),/Рамис/);
  await act(async()=>button(root,"Изменить").props.onClick());
  assert.equal(select(root,"Сотрудник").props.value,1);
  await act(async()=>select(root,"Сотрудник").props.onChange({target:{value:"1"}}));
  assert.match(text(root.findByType("dialog")),/Ранее созданные записи не изменятся/);
  await act(async()=>root.findByType("form").props.onSubmit({preventDefault(){}}));
  const request = global.fetch.mock.calls.find(c=>String(c.arguments[0]).endsWith("accruals/1/")&&c.arguments[1]?.method==="PUT");
  assert.ok(request);
  assert.equal(JSON.parse(request.arguments[1].body).employee,"1");
  assert.equal(JSON.parse(request.arguments[1].body).organization,1);
});

test("journal filters collapse without clearing; organization, sorting, month and pagination work together", async t => {
  const { renderer, calls } = await mountApp(t);
  const root = renderer.root;
  await act(async () => button(root, "Журнал работ").props.onClick());
  assert.equal(root.findAllByProps({ id: "journal-filters" }).length, 0);
  const toggle = () => root.findByProps({ "aria-controls": "journal-filters" });
  await act(async () => toggle().props.onClick());
  await act(async () => select(root, "Организация").props.onChange({ target: { value: "1" } }));
  await act(async () => select(root, "Статус").props.onChange({ target: { value: "review" } }));
  assert.match(text(toggle()), /2/);
  await act(async () => toggle().props.onClick());
  assert.equal(root.findAllByProps({ id: "journal-filters" }).length, 0);
  await act(async () => root.findByProps({ title: "Сортировать: сумма" }).props.onClick());
  await act(async () => button(root, "Далее →").props.onClick());
  let query = new URL(calls.filter(url => url.includes("records/?")).at(-1), "http://local").searchParams;
  assert.equal(query.get("organization"), "1");
  assert.equal(query.get("status"), "review");
  assert.equal(query.get("ordering"), "amount");
  assert.equal(query.get("offset"), "50");
  await act(async () => root.findByProps({ "aria-label": "Месяц журнала" }).props.onChange({ target: { value: "2026-04", validity: { valid: true } } }));
  await act(async () => toggle().props.onClick());
  assert.equal(select(root, "Организация").props.value, "1");
  await act(async () => button(root, "Сбросить фильтры").props.onClick());
  query = new URL(calls.filter(url => url.includes("records/?")).at(-1), "http://local").searchParams;
  assert.equal(query.get("month"), "2026-04");
  assert.equal(query.get("offset"), "0");
  assert.equal(query.get("organization"), null);
  assert.equal(query.get("status"), null);
  assert.equal(query.get("ordering"), "-date");
  assert.equal(text(toggle()), "Фильтры↑");
});

test("history buttons for the first and last journal rows open a dialog without reloading the journal", async t => {
  const { renderer, calls } = await mountApp(t);
  const root = renderer.root;
  await act(async () => button(root, "Журнал работ").props.onClick());
  const recordCalls = calls.filter(url => url.includes("records/?")).length;
  for (const index of [0, 49]) {
    await act(async () => root.findAllByProps({ "aria-label": "История изменений" })[index].props.onClick());
    assert.equal(root.findAllByType("dialog").length, 1);
    assert.ok(calls.at(-1).endsWith(`entity_id=${index + 1}`));
    await act(async () => root.findByProps({ "aria-label": "Закрыть историю" }).props.onClick());
  }
  assert.equal(calls.filter(url => url.includes("records/?")).length, recordCalls);
});

test("tariff filters cover all rates, match Russian aliases and organizations, and stay independent of the journal", async t => {
  const { renderer } = await mountApp(t);
  const root = renderer.root;
  await act(async () => button(root, "Журнал работ").props.onClick());
  await act(async () => root.findByProps({ "aria-controls": "journal-filters" }).props.onClick());
  await act(async () => select(root, "Организация").props.onChange({ target: { value: "1" } }));
  await act(async () => button(root, "Услуги и тарифы").props.onClick());
  await act(async () => button(root, "Тарифы организаций").props.onClick());
  assert.match(text(renderer.toJSON()), /Найдено тарифов: 60 из 60/);
  assert.equal(root.findAllByProps({ id: "rate-filters" }).length, 0);
  const toggle = () => root.findByProps({ "aria-controls": "rate-filters" });
  await act(async () => toggle().props.onClick());
  await act(async () => root.findByProps({ type: "search" }).props.onChange({ target: { value: " СОПР " } }));
  await act(async () => select(root, "Организация").props.onChange({ target: { value: "2" } }));
  assert.match(text(renderer.toJSON()), /Найдено тарифов: 30 из 60/);
  await act(async () => toggle().props.onClick());
  assert.match(text(renderer.toJSON()), /Найдено тарифов: 30 из 60/);
  await act(async () => toggle().props.onClick());
  await act(async () => root.findByProps({ type: "search" }).props.onChange({ target: { value: "ФОКУС" } }));
  assert.match(text(renderer.toJSON()), /Тарифы не найдены/);
  await act(async () => select(root, "Организация").props.onChange({ target: { value: "" } }));
  assert.match(text(renderer.toJSON()), /Найдено тарифов: 30 из 60/);
  await act(async () => button(root, "Сбросить фильтры").props.onClick());
  assert.match(text(renderer.toJSON()), /Найдено тарифов: 60 из 60/);
  await act(async () => button(root, "Журнал работ").props.onClick());
  assert.equal(select(root, "Организация").props.value, "1");
});

test("errors remain visible after the success-notification timeout", async t => {
  const { renderer } = await mountApp(t);
  t.mock.timers.enable({ apis: ["setTimeout"] });
  global.fetch.mock.mockImplementation(async () => response({ error: "Нет связи с сервером" }, false));
  await act(async () => button(renderer.root, "↻ Обновить").props.onClick());
  assert.match(text(renderer.toJSON()), /Нет связи с сервером/);
  act(() => t.mock.timers.tick(4000));
  assert.match(text(renderer.toJSON()), /Нет связи с сервером/);
  act(() => renderer.root.findByProps({ "aria-label": "Скрыть ошибку" }).props.onClick());
  assert.doesNotMatch(text(renderer.toJSON()), /Нет связи с сервером/);
});

test("long history keeps every returned event inside the scrollable dialog content", async t => {
  t.mock.method(global, "fetch", async () => response(Array.from({ length: 100 }, (_, i) => ({ ...historyEntry, id: 100 - i }))));
  let renderer;
  await act(async () => { renderer = create(React.createElement(HistoryDialog, { record, data: null, onClose() {} })); });
  t.after(() => act(() => renderer.unmount()));
  assert.equal(renderer.root.findByProps({ className: "history-content" }).findAllByType("article").length, 100);
  assert.match(text(renderer.toJSON()), /Показаны последние 100 событий/);
});

test("shared shift shows all participants and their cap allocation; one participant has no badge", () => {
  const group = { id:1, hours:"6", total:"1500", price:"300", minimum:"600", maximum:"1500", members:[
    {id:1,employee_name:"Рамис",units:"4",amount:"1000"}, {id:2,employee_name:"Ольга",units:"2",amount:"500"},
  ] };
  let renderer;
  act(() => { renderer = create(React.createElement(SharedShiftDetails, {group})); });
  assert.equal(renderer.root.findAllByType("details").length, 1);
  assert.match(text(renderer.toJSON()), /Разделённая смена/);
  assert.match(text(renderer.toJSON()), /Рамис/);
  assert.match(text(renderer.toJSON()), /Ольга/);
  assert.match(text(renderer.toJSON()), /1\s000,00/);
  assert.match(text(renderer.toJSON()), /500,00/);
  act(() => renderer.update(React.createElement(SharedShiftDetails, {group:{...group,members:group.members.slice(0,1)}})));
  assert.equal(renderer.toJSON(), null);
  act(() => renderer.unmount());
});

test("recalculation preview displays old and new shares and blocks applying errors", () => {
  const preview = { changes:[{id:1,before:"1200",after:"1000"}], errors:[], groups:[{
    date:"2026-04-12",organization:"Фокус",service:"Смена",hourly:true,hours:"6",total:"1500",
    members:[{id:1,employee_name:"Рамис",hours:"4",before:"1200",after:"1000"}],
  }], skipped:[{date:"2026-04-13",organization:"Фокус",service:"Смена",reason:"Есть запись в подтверждённом счёте"}] };
  let renderer, applied = 0;
  const props = {preview,busy:false,onClose(){},onApply(){applied++;}};
  act(() => { renderer = create(React.createElement(RecalculationPreview, props)); });
  assert.match(text(renderer.toJSON()), /БылоСтало/);
  assert.match(text(renderer.toJSON()), /1\s200,00/);
  assert.match(text(renderer.toJSON()), /1\s000,00/);
  assert.match(text(renderer.toJSON()), /подтверждённом счёте/);
  act(() => button(renderer.root, "Применить проверенный пересчёт").props.onClick());
  assert.equal(applied, 1);
  act(() => renderer.update(React.createElement(RecalculationPreview, {...props,preview:{...preview,errors:["Нет тарифа"]}})));
  assert.equal(button(renderer.root, "Применить проверенный пересчёт").props.disabled, true);
  act(() => renderer.unmount());
});

const serviceFormData={
  services:[{id:1,name:"Малый админ",active:true,frequency:"entry",input_type:"time"},{id:2,name:"Сопровождение",active:true,frequency:"entry",input_type:"quantity"},{id:3,name:"Телефоны",active:true,frequency:"entry",input_type:"mark"},{id:4,name:"Договорная",active:true,frequency:"entry",input_type:"amount"}],
  organization_list:[{id:1,name:"Фокус",active:true}],employees:[{id:1,name:"Рамис",active:true}],
};
const serviceEditor=(values={})=>({resource:"records",title:"Типовая услуга",fields:[],values:{kind:"service",date:"2026-09-15",organization:1,service:1,units:1,amount:"",...values}});

test("service interval preview calculates displayed hours and cost, rejects stale responses and submits no blank amount", async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  const first=deferred(),second=deferred(),calls=[];
  t.mock.method(global,"fetch",(url,options)=>{calls.push({url,...options});return calls.length===1?first.promise:second.promise;});
  let renderer,saved;
  await act(async()=>{renderer=create(React.createElement(ServiceRecordEditor,{editor:serviceEditor(),data:serviceFormData,busy:false,error:"",onClose(){},onSave(value){saved=value;}}));});
  t.after(()=>act(()=>renderer.unmount()));
  await act(async()=>t.mock.timers.tick(250));
  await act(async()=>renderer.root.findByProps({"aria-label":"Начало"}).props.onChange({target:{value:"10:30"}}));
  assert.equal(calls[0].signal.aborted,true);
  assert.equal(button(renderer.root,"Сохранить").props.disabled,true);
  await act(async()=>renderer.root.findByProps({"aria-label":"Конец"}).props.onChange({target:{value:"14:30"}}));
  await act(async()=>t.mock.timers.tick(250));
  assert.equal(calls.length,2);
  assert.equal(JSON.parse(calls[1].body).start_time,"10:30");
  assert.equal("amount" in JSON.parse(calls[1].body),false);
  assert.equal("units" in JSON.parse(calls[1].body),false);
  await act(async()=>second.resolve(response({record:{units:"4.00",amount:"1200.00"}})));
  await act(async()=>first.resolve(response({record:{units:"1.00",amount:"300.00"}})));
  assert.match(text(renderer.toJSON()),/1\s200,00/);
  const hours=renderer.root.findByProps({"aria-label":"Часы"});
  assert.equal(hours.props.value,"4.00");assert.equal(hours.props.readOnly,true);
  assert.equal(button(renderer.root,"Сохранить").props.disabled,false);
  await act(async()=>renderer.root.findByType("form").props.onSubmit({preventDefault(){}}));
  assert.deepEqual(saved,JSON.parse(calls[1].body));
  assert.equal("amount" in saved,false);
});

test("service form displays only relevant inputs, previews fixed services and blocks saving calculation errors",async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  let fail=false,latest;
  t.mock.method(global,"fetch",async(_,options)=>{latest=JSON.parse(options.body);return fail?response({error:"Нет действующего тарифа"},false):response({record:{amount:"600",units:latest.units||"1"}});});
  let renderer;
  await act(async()=>{renderer=create(React.createElement(ServiceRecordEditor,{editor:serviceEditor({service:4,amount:"750"}),data:serviceFormData,busy:false,error:"",onClose(){},onSave(){}}));});
  t.after(()=>act(()=>renderer.unmount()));
  assert.equal(renderer.root.findAllByProps({"aria-label":"Готовая сумма, ₽"}).length,1);
  assert.equal(renderer.root.findAllByProps({"aria-label":"Начало"}).length,0);
  await act(async()=>renderer.root.findByProps({"aria-label":"Услуга"}).props.onChange({target:{value:"2"}}));
  assert.equal(renderer.root.findAllByProps({"aria-label":"Готовая сумма, ₽"}).length,0);
  await act(async()=>renderer.root.findByProps({"aria-label":"Количество"}).props.onChange({target:{value:"3"}}));
  await act(async()=>t.mock.timers.tick(250));
  assert.equal(latest.units,"3");assert.equal("amount" in latest,false);
  await act(async()=>renderer.root.findByProps({"aria-label":"Услуга"}).props.onChange({target:{value:"3"}}));
  await act(async()=>t.mock.timers.tick(250));
  assert.equal(renderer.root.findAllByProps({"aria-label":"Количество"}).length,0);
  assert.equal("amount" in latest,false);assert.equal("units" in latest,false);
  assert.match(text(renderer.toJSON()),/600,00/);
  fail=true;
  await act(async()=>renderer.root.findByProps({"aria-label":"Дата"}).props.onChange({target:{value:"2026-09-16"}}));
  assert.equal(button(renderer.root,"Сохранить").props.disabled,true);
  await act(async()=>t.mock.timers.tick(250));
  assert.match(text(renderer.toJSON()),/Нет действующего тарифа/);
  assert.equal(button(renderer.root,"Сохранить").props.disabled,true);
});

test("comment-only service edits preserve saved hours and amount by sending a patch",async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  const original={id:31,kind:"service",date:"2026-09-15",organization:1,employee:1,service:1,start_time:"10:30:00",end_time:"14:30:00",units:"4.00",amount:"777.00",description:"Было"};
  const bodies=[];
  t.mock.method(global,"fetch",async(_,options)=>{bodies.push(JSON.parse(options.body));return response({record:{...original}});});
  let renderer,saved;
  await act(async()=>{renderer=create(React.createElement(ServiceRecordEditor,{editor:serviceEditor(original),data:serviceFormData,busy:false,error:"",onClose(){},onSave(value){saved=value;}}));});
  t.after(()=>act(()=>renderer.unmount()));
  await act(async()=>t.mock.timers.tick(250));
  assert.deepEqual(bodies[0],{id:31,correction:false});
  await act(async()=>renderer.root.findByProps({"aria-label":"Комментарий"}).props.onChange({target:{value:"Стало"}}));
  await act(async()=>t.mock.timers.tick(250));
  assert.match(text(renderer.toJSON()),/777,00/);
  await act(async()=>renderer.root.findByType("form").props.onSubmit({preventDefault(){}}));
  assert.deepEqual(saved,{id:31,correction:false,description:"Стало"});
  const quantity=serviceFormData.services[1];
  const changed=serviceRecordPayload({...original,service:2,units:"2",amount:""},original,quantity);
  assert.deepEqual(changed,{id:31,correction:false,service:2,units:"2",start_time:null,end_time:null});
});

test("recipient payment opt-in belongs to the organization and is removed when its recipient is unchecked",async t=>{
  const editor={title:"Счета · Фокус",resource:"organizations",values:{organization:1,recipients:[1,2],payment_recipients:[1]},fields:[{key:"recipients",label:"Получатели Telegram",type:"recipients",options:[{value:1,label:"Анна"},{value:2,label:"Борис"}]}]};
  let renderer,saved;
  await act(async()=>{renderer=create(React.createElement(AccountingModal,{editor,busy:false,error:"",onClose(){},onSave(value){saved=value;}}));});
  t.after(()=>act(()=>renderer.unmount()));
  let choices=renderer.root.findAllByProps({className:"recipient-choice"});
  assert.equal(choices[0].findAllByType("input")[1].props.checked,true);
  assert.equal(choices[1].findAllByType("input")[1].props.checked,false);
  await act(async()=>choices[1].findAllByType("input")[1].props.onChange({target:{checked:true}}));
  await act(async()=>choices[0].findAllByType("input")[0].props.onChange({target:{checked:false}}));
  await act(async()=>renderer.root.findByType("form").props.onSubmit({preventDefault(){}}));
  assert.deepEqual(saved.recipients,[2]);assert.deepEqual(saved.payment_recipients,[2]);assert.equal(saved.organization,1);
  assert.match(text(renderer.toJSON()),/Подтверждение общее для организации/);
});

test("unified invoices keep old debts, refresh payment and preserve filters when opening and closing",async t=>{
  const invoice={id:91,batch:17,organization:1,organization_name:"Фокус",start:"2026-04-01",end:"2026-04-30",version:1,total:"1200",payment_state:"open"};
  let paid=false;
  const {renderer,calls}=await mountApp(t,{}, {request(url){
    if(url.includes("payments/?")||url.includes("invoices/?")){
      const params=new URL(url,"http://local").searchParams; const state=params.get("payment_state")||params.get("state");
      const show=(state==="open"&&!paid)||(state==="paid"&&paid)||state==="all";
      return {open_count:paid?0:1,count:show?1:0,invoices:show?[{...invoice,payment_state:paid?"paid":"open",paid_at:paid?"2026-09-15T10:00:00+05:00":null,paid_by_name:paid?"Анна":""}]:[]};
    }
    if(url.endsWith("batches/17/"))return {id:17,start:invoice.start,end:invoice.end,version:1,state:"approved",invoices:[{...invoice,recipients:[777],errors:[],lines:[],adjustments:[]}],deliveries:[]};
  }});
  const root=renderer.root;
  assert.equal(root.findByProps({"aria-label":"Показать неоплаченные счета: 1"}).children[0],"1");
  await act(async()=>root.findByProps({"aria-label":"Месяц журнала"}).props.onChange({target:{value:"2026-08",validity:{valid:true}}}));
  await act(async()=>root.findByProps({"aria-label":"Показать неоплаченные счета: 1"}).props.onClick());
  assert.match(text(renderer.toJSON()),/01.04.2026 — 30.04.2026/);
  assert.equal(root.findAllByProps({"aria-label":"Месяц журнала"}).length,0);
  const query=new URL(calls.filter(url=>url.includes("invoices/?")).at(-1),"http://local").searchParams;
  assert.equal(query.has("month"),false); assert.equal(query.get("start"),""); assert.equal(query.get("end"),""); assert.equal(query.get("payment_state"),"open");
  paid=true;
  await act(async()=>button(root,"↻ Обновить").props.onClick());
  assert.match(text(renderer.toJSON()),/Нет счетов, ожидающих оплаты/);
  assert.equal(root.findByProps({"aria-label":"Показать неоплаченные счета: 0"}).children[0],"0");
  await act(async()=>root.findByProps({"aria-label":"Статус оплаты"}).props.onChange({target:{value:"paid"}}));
  assert.match(text(renderer.toJSON()),/Оплачен/);assert.match(text(renderer.toJSON()),/Анна/);
  await act(async()=>button(root,"Открыть счёт").props.onClick());
  assert.match(text(renderer.toJSON()),/Пакет №17/);
  assert.equal(root.findAllByProps({"aria-label":"Месяц журнала"}).length,0);
  await act(async()=>button(root,"Закрыть карточку счёта").props.onClick());
  assert.equal(root.findByProps({"aria-label":"Статус оплаты"}).props.value,"paid");
});

test("single invoices entry defaults to all periods and separates draft, delivery and payment",async t=>{
  const invoice={id:5,batch:2,organization:1,organization_name:"Фокус",start:"2024-01-01",end:"2024-01-31",version:1,total:"100",batch_state:"draft",payment_state:"none",errors:[]};
  const {renderer,calls}=await mountApp(t,{}, {payments:{invoices:[invoice],count:1,open_count:0}});
  const root=renderer.root;
  assert.equal(button(root,"Оплаты"),undefined);
  await act(async()=>button(root,"Счета").props.onClick());
  assert.equal(root.findByProps({"aria-label":"Статус оплаты"}).props.value,"all");
  assert.match(text(renderer.toJSON()),/На проверке/); assert.match(text(renderer.toJSON()),/Без подтверждения оплаты/);
  assert.equal(new URL(calls.filter(u=>u.includes("invoices/?")).at(-1),"http://local").searchParams.has("month"),false);
});

test("Telegram settings save proposal route with existing settings in one request",async t=>{
  let saved;
  const {renderer}=await mountApp(t,{offer_config:{enabled:true,chat_id:-1001,thread_id:30}}, {request(url,options,bootstrap){
    if(url.endsWith("ledger/settings/")&&options.method==="PUT"){saved=JSON.parse(options.body);bootstrap.offer_config=saved.offer_config;return saved;}
  }});
  const root=renderer.root;
  await act(async()=>button(root,"Настройки").props.onClick());
  await act(async()=>button(root,"Telegram и расписание").props.onClick());
  await act(async()=>button(root,"Настроить").props.onClick());
  const modal=root.findByType(AccountingModal);
  assert.equal(modal.props.editor.values.offer_thread_id,30);
  assert.ok(modal.props.editor.fields.some(f=>f.key==="service_thread"));
  assert.ok(modal.props.editor.fields.some(f=>f.key==="offer_chat_id"));
  await act(async()=>modal.props.onSave({...modal.props.editor.values,offer_thread_id:40}));
  assert.deepEqual(saved.offer_config,{enabled:true,chat_id:-1001,thread_id:40});
  assert.equal(saved.approver,1); assert.equal(Object.hasOwn(saved,"offer_thread_id"),false);
});

test("team selects Telegram user ID rather than contact database ID and preserves an old unknown binding",async t=>{
  let saved;
  const {renderer}=await mountApp(t,{employees:[{id:1,name:"Рамис",active:true,telegram_user_id:888}],contacts:[{id:1,name:"Контакт бота",username:"person",user_id:777}]}, {request(url,options){
    if(url.endsWith("shifts/employees/1/")){
      if(options.method==="PUT"){saved=JSON.parse(options.body);return saved;}
      return {id:1,shortName:"Рамис",fullName:"",telegramUserId:888,isActive:true,aliases:[]};
    }
  }});
  const root=renderer.root;
  await act(async()=>button(root,"Настройки").props.onClick());
  await act(async()=>button(root,"Команда").props.onClick());
  assert.match(text(renderer.toJSON()),/888 · попросите написать боту/);
  await act(async()=>button(root,"Изменить").props.onClick());
  const modal=root.findByType(AccountingModal),field=modal.props.editor.fields.find(f=>f.key==="telegramUserId");
  assert.equal(field.type,"select"); assert.deepEqual(field.options.map(o=>o.value),[777,888]);
  assert.match(field.options[0].label,/@person/);
  await act(async()=>modal.props.onSave({...modal.props.editor.values,telegramUserId:"777"}));
  assert.equal(saved.telegramUserId,"777");assert.equal(saved.shortName,"Рамис");
});

test("service and tariff delete actions require confirmation and preserve access through the archive filter",async t=>{
  const {renderer,calls}=await mountApp(t,{}, {request(url,options,bootstrap){
    const match=url.match(/\/(services|rates)\/(\d+)\/$/);
    if(match&&options.method==="DELETE"){bootstrap[match[1]].find(item=>item.id===Number(match[2])).active=false;return {deleted:true};}
  }});
  const root=renderer.root;
  await act(async()=>button(root,"Услуги и тарифы").props.onClick());
  let prompt;
  global.window.confirm=value=>{prompt=value;return false;};
  await act(async()=>button(root,"Удалить").props.onClick());
  assert.match(prompt,/Исторические работы, суммы и счета сохранятся/);
  assert.equal(calls.some(url=>url.endsWith("services/1/")),false);
  global.window.confirm=()=>true;
  await act(async()=>button(root,"Удалить").props.onClick());
  assert.equal(button(root,"Настроить"),undefined);
  await act(async()=>button(root,"Тарифы организаций").props.onClick());
  assert.match(text(renderer.toJSON()),/Найдено тарифов: 0 из 60/);
  await act(async()=>root.findByProps({className:"archive-toggle"}).findByType("input").props.onChange({target:{checked:true}}));
  assert.match(text(renderer.toJSON()),/Найдено тарифов: 60 из 60/);
  await act(async()=>button(root,"Удалить").props.onClick());
  assert.ok(calls.some(url=>url.endsWith("rates/1/")));
  assert.ok(button(root,"Восстановить / изменить"));
  await act(async()=>button(root,"Услуги").props.onClick());
  assert.ok(button(root,"Восстановить / настроить"));
});
