const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"),ts=require("typescript"),React=require("react");
const {create,act}=require("react-test-renderer");
for(const ext of [".ts",".tsx"])require.extensions[ext]=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:filename}).outputText,filename);
const Receipts=require("../src/components/OrganizationReceipts").default;
const Editor=require("../src/components/ServiceRecordEditor").default;
const Overlap=require("../src/components/OverlapReview").default;
const Details=require("../src/components/CalculationDetails").default;
const Recalculation=require("../src/components/RecalculationPreview").default;
const text=n=>typeof n==="string"?n:!n?"":Array.isArray(n)?n.map(text).join(""):text(n.children);
const response=d=>({ok:true,json:async()=>d});
const button=(root,label)=>root.findAllByType("button").find(n=>text(n)===label);

test("calculation details omit zero bonus and its reason",()=>{
  let r;
  act(()=>{r=create(React.createElement(Details,{record:{base_amount:"600",bonus:"0.00",bonus_reason:"Старая причина"}}));});
  assert.doesNotMatch(text(r.toJSON()),/доплата|Старая причина/);
  act(()=>r.update(React.createElement(Details,{record:{base_amount:"600",bonus:"500",bonus_reason:"Выход"}})));
  assert.match(text(r.toJSON()),/доплата 500/);
  assert.match(text(r.toJSON()),/Выход/);
  act(()=>r.unmount());
});

test("recalculation shows only changed members and handles no changes",()=>{
  const preview={changes:[{id:2}],errors:[],groups:[{date:"2026-09-29",organization:"Студия",service:"Смена",members:[{id:1,before:"600",after:"600",employee_name:"Без изменений"},{id:2,before:"500",after:"600",employee_name:"Изменён"}]}]};
  const props={preview,busy:false,onClose(){},onApply(){}};
  let r;act(()=>{r=create(React.createElement(Recalculation,props));});
  assert.doesNotMatch(text(r.toJSON()),/Без изменений|Запись №1/);
  assert.match(text(r.toJSON()),/Запись №2/);
  act(()=>r.update(React.createElement(Recalculation,{...props,preview:{...preview,changes:[]}})));
  assert.match(text(r.toJSON()),/Изменений нет/);
  assert.equal(r.root.findAllByType("table").length,0);
  assert.equal(button(r.root,"Применить проверенный пересчёт").props.disabled,true);
  act(()=>r.unmount());
});

test("studio receipt form adds a deduction with a reason and shows separate balances",async t=>{
  const writes=[];
  t.mock.method(global,"fetch",async(url,o={})=>{if(o.method){writes.push(JSON.parse(o.body));return response({id:1});}assert.match(String(url),/organization=7/);return response({receipts:[],count:0,received_total:"1000",deduction_total:"200",total:"800"});});
  let r;await act(async()=>{r=create(React.createElement(Receipts,{organization:{id:7,name:"Студия",active:true},data:null,onClose(){}}));});
  t.after(()=>act(()=>r.unmount()));
  assert.match(text(r.toJSON()),/Вычтено: 200/);assert.match(text(r.toJSON()),/Остаток за период: 800/);
  await act(async()=>button(r.root,"+ Вычет").props.onClick());
  assert.equal(r.root.findAllByProps({"aria-label":"Плательщик"}).length,0);
  await act(async()=>r.root.findByProps({"aria-label":"Причина вычета"}).props.onChange({target:{value:"Возврат"}}));
  await act(async()=>r.root.findByProps({"aria-label":"Сумма поступления"}).props.onChange({target:{value:"200"}}));
  await act(async()=>r.root.findByType("form").props.onSubmit({preventDefault(){}}));
  assert.equal(writes[0].kind,"deduction");assert.equal(writes[0].reason,"Возврат");assert.equal(writes[0].organization,7);
});

test("required service comment blocks preview and bonus submits with preview fingerprint",async t=>{
  t.mock.timers.enable({apis:["setTimeout"]});
  let saved,calls=0;
  t.mock.method(global,"fetch",async()=>{calls++;return response({record:{id:3,amount:"900",base_amount:"800",overlap_factor:"1",bonus:"100",preview_fingerprint:"verified"}});});
  const data={services:[{id:1,name:"Админ",active:true,frequency:"entry",input_type:"time",legacy_code:"small_admin",comment_required:true,comment_hint:"Укажите зал"}],organization_list:[{id:1,name:"Студия",active:true}],employees:[]};
  let r;await act(async()=>{r=create(React.createElement(Editor,{editor:{title:"Смена",values:{service:1,organization:1,date:"2026-09-29",units:4},fields:[]},data,busy:false,error:"",onClose(){},onSave:v=>saved=v}));});t.after(()=>act(()=>r.unmount()));
  await act(async()=>t.mock.timers.tick(250));assert.equal(calls,0);assert.equal(button(r.root,"Сохранить").props.disabled,true);
  assert.equal(r.root.findByProps({"aria-label":"Комментарий"}).props.required,true);assert.match(text(r.toJSON()),/Укажите зал/);
  await act(async()=>r.root.findByProps({"aria-label":"Комментарий"}).props.onChange({target:{value:"Зал 1"}}));
  await act(async()=>r.root.findByProps({"aria-label":"Доплата сверх базы"}).props.onChange({target:{value:"100"}}));
  await act(async()=>r.root.findByProps({"aria-label":"Причина доплаты"}).props.onChange({target:{value:"Срочный выход"}}));
  await act(async()=>t.mock.timers.tick(250));
  await act(async()=>r.root.findByType("form").props.onSubmit({preventDefault(){}}));
  assert.equal(saved.preview_fingerprint,"verified");assert.equal(saved.bonus,"100");assert.equal(saved.description,"Зал 1");
});

test("three studio decision sends selected organization IDs and current fingerprint",async t=>{
  let saved,closed=false;
  t.mock.method(global,"fetch",async(url,o={})=>{if(o.method){saved=JSON.parse(o.body);return response({saved:true});}return response({components:[{employee:4,date:"2026-09-29",fingerprint:"current",organizations:[{id:10,name:"А",hours:8,base:1600},{id:20,name:"Б",hours:4,base:800},{id:30,name:"В",hours:2,base:400}]}]});});
  let r;await act(async()=>{r=create(React.createElement(Overlap,{record:{id:1,employee:4,date:"2026-09-29"},onClose:()=>closed=true,onSaved(){}}));});t.after(()=>act(()=>r.unmount()));
  await act(async()=>r.root.findAllByType("input")[1].props.onChange({target:{checked:true}}));
  await act(async()=>button(r.root,"Подтвердить выбранный расчёт").props.onClick());
  assert.deepEqual(saved.half_organizations,[20]);assert.equal(saved.fingerprint,"current");assert.equal(closed,true);
});
