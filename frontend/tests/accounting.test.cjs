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

async function mountApp(t, overrides={}) {
  const bootstrap = {
    services: [{ id: 1, name: "Сопровождение", aliases: ["сопр"], active: true, input_type: "quantity", frequency: "entry" }],
    rates: Array.from({ length: 60 }, (_, i) => ({ id: i + 1, service: 1, organization: i < 30 ? 1 : 2, calculation: "fixed", price: 100, start: "2026-01-01", active: true })),
    organization_list: [{ id: 1, name: "Фокус", active: true }, { id: 2, name: "Студия", active: false }],
    employees: [{ id: 1, name: "Рамис", active: true }], accruals: [], preferences: [], contacts: [{id:1,name:"Главный",user_id:777}], organizations: [], settings: {approver:1,earnings_enabled:true},
    ...overrides,
  };
  const calls = [];
  t.mock.method(global, "fetch", async url => {
    calls.push(String(url));
    if (url.includes("bootstrap/")) return response(bootstrap);
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
