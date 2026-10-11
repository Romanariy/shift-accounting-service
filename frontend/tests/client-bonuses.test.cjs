const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),ts=require('typescript'),React=require('react');
const {create,act}=require('react-test-renderer');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:filename}).outputText,filename);
const {default:Console,rulePayload,clientPayload}=require('../src/components/ClientBonusesConsole');
const Details=require('../src/components/CalculationDetails').default;
const text=n=>typeof n==='string'?n:!n?'':Array.isArray(n)?n.map(text).join(''):text(n.children);
const button=(r,label)=>r.root.findAllByType('button').find(n=>text(n)===label);
const response=d=>({ok:true,json:async()=>d});
const clients=[{id:1,name:'Школа',phones:['+79191234567','+79191234568'],active:true},{id:2,name:'Второй клиент',phones:['+79195555555'],active:true}];
const rule={id:1,name:'Школьные съёмки',organization:9,organization_name:'Студия',clients:[1,2],amount:'200',revision:3,start:'2026-01-01',active:true,pending:true};
const setup=(handle)=>{global.fetch=async(url,options={})=>{
 if(options.method&&options.method!=='GET')return handle(url,JSON.parse(options.body||'{}'));
 if(url.includes('bonus-clients/'))return response({items:clients,count:2});
 if(url.includes('bonus-rules/'))return response({items:[rule],count:1});
 if(url.includes('client-booking-imports/'))return response([]);
 return response({items:[],count:0});
};};
const props={data:{organization_list:[{id:9,name:'Студия',active:true}]},onChanged:async()=>{}};

test('rule payload keeps all clients and client payload accepts several phones',()=>{
 assert.deepEqual(rulePayload({organization:'9',clients:['1','2'],amount:'200'}).clients,[1,2]);
 assert.deepEqual(clientPayload({phone_text:'8 (919) 123-45-67\n+7 919 123 45 68; +79195555555'}).phones,['8 (919) 123-45-67','+7 919 123 45 68','+79195555555']);
});
test('editing a rule preserves multiple clients and revision and can remove one',async()=>{
 let saved,r;setup(async(url,p)=>{saved=p;return response({...rule,...p});});
 await act(async()=>{r=create(React.createElement(Console,props));});
 act(()=>button(r,'Изменить').props.onClick());
 const checks=r.root.findAllByType('input').filter(n=>String(n.props['aria-label']).startsWith('Клиент правила:'));
 assert.equal(checks.length,2);assert.ok(checks.every(n=>n.props.checked));
 act(()=>checks[1].props.onChange({target:{checked:false}}));
 await act(async()=>r.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(saved.clients,[1]);assert.equal(saved.revision,3);assert.equal(saved.organization,9);
 act(()=>r.unmount());
});
test('zero-change preview still applies a rule and sends fingerprint',async()=>{
 let r,requests=[];setup(async(url,p)=>{requests.push(p);return response({changes:[],fingerprint:'current-data'});});
 await act(async()=>{r=create(React.createElement(Console,props));});
 await act(async()=>button(r,'Проверить и применить').props.onClick());
 assert.match(text(r.toJSON()),/Стоимость работ не изменится/);
 await act(async()=>button(r,'Применить проверенный пересчёт').props.onClick());
 assert.equal(requests[1].fingerprint,'current-data');assert.equal(requests[1].revision,3);
 act(()=>r.unmount());
});
test('stale rule error preserves form and selected clients',async()=>{
 let r;setup(async()=>({ok:false,json:async()=>({error:'Данные изменились'})}));
 await act(async()=>{r=create(React.createElement(Console,props));});
 act(()=>button(r,'Изменить').props.onClick());
 await act(async()=>r.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.match(text(r.toJSON()),/Данные изменились/);
 assert.equal(r.root.findAllByType('input').filter(n=>n.props.checked&&n.props['aria-label']?.startsWith('Клиент правила:')).length,2);
 act(()=>r.unmount());
});
test('work details distinguish manual and client bonuses without exposing phones',()=>{
 let r;act(()=>{r=create(React.createElement(Details,{record:{base_amount:'1000',bonus:'500',client_bonus:'200',client_bonus_details:[{rule:'Школьные съёмки',client:'Школа',start:'12:00',end:'13:00',amount:'200'}]}}));});
 assert.match(text(r.toJSON()),/Школьные съёмки.*Школа.*12:00/s);assert.match(text(r.toJSON()),/500/);
 assert.doesNotMatch(text(r.toJSON()),/79191234567/);act(()=>r.unmount());
});
