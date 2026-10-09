const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),ts=require('typescript'),React=require('react');
const {create,act}=require('react-test-renderer');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(m,f)=>m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:f}).outputText,f);
const Form=require('../src/components/OfferShiftForm').default;
const Details=require('../src/components/CalculationDetails').default;
const text=n=>typeof n==='string'?n:!n?'':Array.isArray(n)?n.map(text).join(''):text(n.children);
const button=(root,label)=>root.findAllByType('button').find(n=>text(n)===label);

test('shift form creates two different studios with linked attachments and an HTTP-safe stable key',async t=>{
  t.mock.method(require('../src/components/OfferServiceForm'),'newOfferRequestKey',()=> 'stable-http-key');
  const saves=[];let r;await act(async()=>{r=create(React.createElement(Form,{organizations:[{id:1,name:'А'},{id:2,name:'Б'}],busy:false,onCancel(){},onSave:async(...args)=>saves.push(args)}));});
  t.after(()=>act(()=>r.unmount()));
  await act(async()=>button(r.root,'Добавить вторую студию').props.onClick());
  await act(async()=>r.root.findByProps({type:'date'}).props.onChange({target:{value:'2026-10-12'}}));
  for(const [index,org] of ['1','2'].entries()){
    await act(async()=>r.root.findAllByType('select')[index].props.onChange({target:{value:org}}));
    for(const [j,value] of ['10:00','18:00'].entries())await act(async()=>r.root.findAllByProps({type:'time'})[index*2+j].props.onChange({target:{value}}));
    await act(async()=>r.root.findAllByProps({type:'file'})[index].props.onChange({target:{files:[{name:`${org}.png`,size:3}]}}));
  }
  await act(async()=>r.root.findByType('form').props.onSubmit({preventDefault(){}}));
  await act(async()=>r.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(saves.length,2);assert.equal(saves[0][0].kind,'shift');
  assert.deepEqual(saves[0][0].parts.map(p=>p.organization),[1,2]);
  assert.deepEqual(saves[0][1].map(p=>p.organization),[1,2]);
  assert.equal(saves[0][2],saves[1][2]);assert.ok(saves[0][2]);
  await act(async()=>r.root.findAllByType('select')[1].props.onChange({target:{value:'1'}}));
  await act(async()=>r.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(saves.length,2);assert.match(text(r.toJSON()),/разные студии/);
});

test('new calculation displays hours rather than halving the entire base',()=>{
  let r;act(()=>{r=create(React.createElement(Details,{record:{base_amount:'800',actual_hours:'4',overlap_hours:'2',payable_hours:'3',pricing_version:2,overlap_factor:'.5',bonus:'500'}}));});
  assert.match(text(r.toJSON()),/фактически 4 ч, пересечение 2 ч, к оплате 3 ч/);
  assert.doesNotMatch(text(r.toJSON()),/× 0.5/);assert.match(text(r.toJSON()),/доплата 500/);
  act(()=>r.unmount());
});
