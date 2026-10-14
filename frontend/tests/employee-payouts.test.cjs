const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript'),React=require('react');
const {create,act}=require('react-test-renderer');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:filename}).outputText,filename);
const {PayoutDialog,PayoutStatus,default:Details}=require('../src/components/EmployeePayouts');
const {default:Report}=require('../src/components/EarningsReport');
const text=n=>typeof n==='string'?n:!n?'':Array.isArray(n)?n.map(text).join(''):text(n.children);
const reply=d=>({ok:true,json:async()=>d});
const draft={token:'server-preview',amount:'2500',lines:[{record:1,employee_name:'Анна',organization_name:'Студия',date:'2026-10-05',service_name:'Смена',earned:'2000',paid:'0',to_pay:'2000'},{record:2,service_name:'Уборка',earned:'500',paid:'0',to_pay:'500'}]};
test('payout confirmation previews saved work and sends only server token, no editable money',async()=>{
  const original=global.fetch;let r,calls=[],saved=0,closed=0;
  global.fetch=async(url,opts)=>{calls.push(JSON.parse(opts.body));return reply(calls.length===1?draft:{id:9});};
  try{await act(async()=>{r=create(React.createElement(PayoutDialog,{selector:{employee:4,organization:2,date:'2026-10-05'},onClose:()=>closed++,onSaved:()=>saved++}));});
    assert.match(text(r.toJSON()),/Анна.*Студия.*Смена.*Уборка/s);
    await act(async()=>r.root.findAllByType('button').find(b=>text(b.toJSON?b.toJSON():{children:b.props.children})==='Подтвердить выплату').props.onClick());
    assert.deepEqual(calls[1],{action:'confirm',token:'server-preview'});assert.equal(saved,1);assert.equal(closed,1);
  }finally{act(()=>r?.unmount());global.fetch=original;}
});
test('stale payout clears confirm and requires a new preview',async()=>{
  const original=global.fetch;let r,count=0;
  global.fetch=async()=>++count===1?reply(draft):({ok:false,json:async()=>({error:'Работы изменились'})});
  try{await act(async()=>{r=create(React.createElement(PayoutDialog,{selector:{record:1},onClose(){},onSaved(){}}));});
    await act(async()=>r.root.findAllByType('button').find(b=>b.props.children==='Подтвердить выплату').props.onClick());
    assert.match(text(r.toJSON()),/Работы изменились/);assert.equal(r.root.findAllByType('button').find(b=>b.props.children==='Подтвердить выплату').props.disabled,true);
    assert.ok(r.root.findAllByType('button').some(b=>b.props.children==='Обновить предпросмотр'));
  }finally{act(()=>r?.unmount());global.fetch=original;}
});
test('old periods show unknown, overpayment is explicit and not hidden as zero',()=>{
  let r;act(()=>{r=create(React.createElement(PayoutStatus,{record:{id:1,payout_tracking:false}}));});assert.match(text(r.toJSON()),/нет данных/);
  act(()=>r.update(React.createElement(PayoutStatus,{record:{id:1,payout_tracking:true,payout_paid:'2000',payout_remaining:'-100'}})));assert.match(text(r.toJSON()),/Переплата.*100/);act(()=>r.unmount());
});
test('payout details filter records on server and reject empty cancellation reason',async()=>{
  const original=global.fetch;let r,urls=[],cancelled=[];
  const payment={id:9,revision:1,amount:'2000',organization_name:'Студия',work_date:'2026-10-05',paid_at:'2026-10-05T12:00:00Z',author:'web',lines:[{snapshot:{service_name:'Смена'}}]};
  global.fetch=async(url,opts)=>{urls.push(url);if(opts.method==='POST'){cancelled.push(JSON.parse(opts.body));return reply({});}return reply(url.includes('details=1')?{records:[],count:0}:{payouts:[payment],count:1});};
  try{await act(async()=>{r=create(React.createElement(Details,{employee:{employee_id:4,employee_name:'Анна'},month:'2026-10',onClose(){},onChanged(){}}));});
    await act(async()=>r.root.findByType('select').props.onChange({target:{value:'unpaid'}}));assert.ok(urls.some(u=>u.includes('state=unpaid')));
    act(()=>r.root.findAllByType('button').find(b=>b.props.children==='Отменить выплату').props.onClick());
    const button=()=>r.root.findAllByType('button').find(b=>b.props.children==='Отменить подтверждение выплаты');assert.equal(button().props.disabled,true);
    act(()=>r.root.findByType('textarea').props.onChange({target:{value:'Ошибка отметки'}}));await act(async()=>button().props.onClick());
    assert.deepEqual(cancelled[0],{reason:'Ошибка отметки',revision:1});
  }finally{act(()=>r?.unmount());global.fetch=original;}
});
test('main report shows three totals and opens employee payment details',()=>{
  let r;const data={month:'2026-10',start:'2026-10-01',end:'2026-10-14',generated_at:'2026-10-14T00:00:00Z',total:'3000',received_total:'2000',remaining_total:'1000',employees:[{employee_id:4,employee_name:'Анна',amount:'3000',received:'2000',remaining:'1000'}],review:[],unassigned:[],review_total:'0',unassigned_total:'0',delivery:null};
  act(()=>{r=create(React.createElement(Report,{month:'2026-10',maxMonth:'2026-10',onMonthChange(){},data,enabled:false,busy:false,onRetry(){}}));});
  assert.match(text(r.toJSON()),/ЗаработаноПолучено выплатОсталось выплатить/);
  const pending={...data,payout_review:true,remaining_total:null,employees:[{...data.employees[0],remaining:null,payout_review:true}]};
  act(()=>r.update(React.createElement(Report,{month:'2026-10',maxMonth:'2026-10',onMonthChange(){},data:pending,enabled:false,busy:false,onRetry(){}})));
  assert.match(text(r.toJSON()),/На проверке/);assert.doesNotMatch(text(r.toJSON()),/Переплата/);act(()=>r.unmount());
});
