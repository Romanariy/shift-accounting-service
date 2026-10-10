const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),ts=require('typescript'),React=require('react');
const {create,act}=require('react-test-renderer');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:filename}).outputText,filename);
const {default:Dialog,InvoicePaymentActions}=require('../src/components/InvoicePaymentActions');
const {PaymentStatus}=require('../src/components/Payments');
const text=n=>typeof n==='string'?n:!n?'':Array.isArray(n)?n.map(text).join(''):text(n.children);
const invoice={id:7,organization_name:'Студия',version:2,payment_revision:4,total:'1250',payment_state:'open',payment_action_allowed:true};
test('payment buttons respect eligibility, status and busy state',()=>{
  let r,action;
  const props={invoice,busy:false,onAction:(i,cancel)=>action=[i.id,cancel]};
  act(()=>{r=create(React.createElement(InvoicePaymentActions,props));});
  act(()=>r.root.findByType('button').props.onClick());assert.deepEqual(action,[7,false]);
  act(()=>r.update(React.createElement(InvoicePaymentActions,{...props,invoice:{...invoice,payment_state:'paid'},busy:true})));
  assert.match(text(r.toJSON()),/Отменить подтверждение/);assert.equal(r.root.findByType('button').props.disabled,true);
  act(()=>r.update(React.createElement(InvoicePaymentActions,{...props,invoice:{...invoice,payment_action_allowed:false}})));
  assert.equal(r.toJSON(),null);act(()=>r.unmount());
});
test('confirm dialog shows complete invoice context and sends exact revision',async()=>{
  let r,saved,closed=0;
  act(()=>{r=create(React.createElement(Dialog,{invoice,cancel:false,onClose:()=>closed++,onSave:async p=>{saved=p;}}));});
  assert.match(text(r.toJSON()),/Студия.*Счёт №7.*версия 2.*1.*250.*всех получателей/s);
  await act(async()=>r.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.deepEqual(saved,{version:2,payment_revision:4});assert.equal(closed,1);act(()=>r.unmount());
});
test('cancel requires reason, exposes stale errors and preserves input',async()=>{
  let r,calls=0,payload;
  act(()=>{r=create(React.createElement(Dialog,{invoice:{...invoice,payment_state:'paid'},cancel:true,onClose(){},onSave:async p=>{calls++;payload=p;throw Error('Состояние оплаты изменилось');}}));});
  const submit=()=>r.root.findByType('form').props.onSubmit({preventDefault(){}});
  await act(async()=>submit());assert.equal(calls,0);
  act(()=>r.root.findByType('textarea').props.onChange({target:{value:' Ошибка сверки '}}));
  await act(async()=>submit());assert.deepEqual(payload,{version:2,payment_revision:4,reason:'Ошибка сверки'});
  assert.match(text(r.toJSON()),/Состояние оплаты изменилось/);
  assert.equal(r.root.findByType('textarea').props.value,' Ошибка сверки ');act(()=>r.unmount());
});
test('payment status distinguishes website administrator from Telegram recipient',()=>{
  let r;act(()=>{r=create(React.createElement(PaymentStatus,{invoice:{...invoice,payment_state:'paid',paid_at:'2026-10-10T13:00:00Z',paid_by:null}}));});
  assert.match(text(r.toJSON()),/Администратор сайта/);
  act(()=>r.update(React.createElement(PaymentStatus,{invoice:{...invoice,payment_state:'paid',paid_at:'2026-10-10T13:00:00Z',paid_by:12},contacts:[{user_id:12,name:'Получатель'}]})));
  assert.match(text(r.toJSON()),/Получатель/);assert.doesNotMatch(text(r.toJSON()),/Администратор сайта/);act(()=>r.unmount());
});
