const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const {create,act} = require('react-test-renderer');
for (const ext of ['.ts','.tsx']) require.extensions[ext]=(module,filename)=>{
  module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:filename}).outputText,filename);
};
const Offers = require('../src/components/OffersConsole').default;
const {parseIntervals} = require('../src/components/OffersConsole');
const {POST,GET} = require('../src/app/api/shifts/[...path]/route');
const {NextRequest}=require('next/server');
const text=n=>typeof n==='string'?n:Array.isArray(n)?n.map(text).join(''):n?.children?.map(text).join('')||'';
const button=(root,label)=>root.findAllByType('button').find(n=>text(n)===label);
const config={enabled:true,chat_id:-100,thread_id:77,coordinator:1,auto_publish:false,organizations:[{id:3,name:'Фотобар'}],contacts:[{id:1,user_id:11,name:'Publisher'}],employees:[],workers:[]};

test('multipart proxy preserves every byte and boundary; protected images stay binary',async t=>{
  const original=global.fetch;t.after(()=>{global.fetch=original;});
  const bytes=new Uint8Array([0,255,128,13,10,65,66]);
  global.fetch=async(url,options)=>{
    assert.deepEqual(new Uint8Array(options.body),bytes);
    assert.equal(options.headers['Content-Type'],'multipart/form-data; boundary=test-boundary');
    return new Response(JSON.stringify({ok:true}),{headers:{'Content-Type':'application/json'}});
  };
  const request=new NextRequest('http://localhost/api/shifts/ledger/offer-profiles/3/sample/',{method:'POST',headers:{'Content-Type':'multipart/form-data; boundary=test-boundary'},body:bytes});
  const response=await POST(request,{params:Promise.resolve({path:['ledger','offer-profiles','3','sample']})});
  assert.equal(response.status,200);
  global.fetch=async()=>new Response(bytes,{headers:{'Content-Type':'image/png'}});
  const image=await GET(new NextRequest('http://localhost/api/shifts/ledger/offer-images/1/'),{params:Promise.resolve({path:['ledger','offer-images','1']})});
  assert.deepEqual(new Uint8Array(await image.arrayBuffer()),bytes);
  assert.equal(image.headers.get('cache-control'),'private, no-store');
});

test('proxy rejects oversized chunked uploads and cross-origin writes',async t=>{
  const original=global.fetch;t.after(()=>{global.fetch=original;});let calls=0;
  global.fetch=async()=>{calls++;throw new Error('Must not forward');};
  const context={params:Promise.resolve({path:['ledger','offers']})};
  const large=new NextRequest('http://localhost/api/shifts/ledger/offers/',{method:'POST',body:new Uint8Array(12*1024*1024+1)});
  assert.equal((await POST(large,context)).status,413);
  const cross=new NextRequest('http://localhost/api/shifts/ledger/offers/',{method:'POST',headers:{origin:'https://evil.example',host:'localhost'},body:'{}'});
  assert.equal((await POST(cross,context)).status,403);assert.equal(calls,0);
});

test('manual intervals reject unparsed trailing text',()=>{
  assert.deepEqual(parseIntervals('13:00–16:00, 16:30-20:00'),[{start:'13:00',end:'16:00'},{start:'16:30',end:'20:00'}]);
  assert.throws(()=>parseIntervals('13:00-16:00, broken'));
});

test('profile upload uses FormData and publishing permission stays on organization profile',async t=>{
  const original=global.fetch;t.after(()=>{global.fetch=original;});let saved,uploaded;
  global.fetch=async(url,options)=>{
    if(url.endsWith('/sample/')){uploaded=options.body;return {ok:true,json:async()=>({})};}
    if(url.endsWith('offer-profiles/3/')){saved=JSON.parse(options.body);return {ok:true,json:async()=>({})};}
    const data=url.includes('offer-config')?config:url.includes('offer-profiles')?{items:[]}:{items:[],count:0};
    return {ok:true,json:async()=>data};
  };
  let renderer;await act(async()=>{renderer=create(React.createElement(Offers));});t.after(()=>act(()=>renderer.unmount()));
  act(()=>button(renderer.root,'Залы и разрешения').props.onClick());
  const textarea=renderer.root.findByType('textarea');act(()=>textarea.props.onChange({target:{value:'БОХО\nСтол визажный | Стол ви…'}}));
  const checks=renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox');
  act(()=>{checks[0].props.onChange({target:{checked:true}});checks[2].props.onChange({target:{checked:true}});});
  await act(async()=>renderer.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(saved.confirmed,true);assert.deepEqual(saved.publishers,[1]);assert.deepEqual(saved.rooms[1],{name:'Стол визажный',aliases:['Стол ви…']});
  const upload=renderer.root.findAllByType('input').find(n=>n.props.type==='file');
  await act(async()=>upload.props.onChange({target:{files:[new File(['bytes'],'ref.png',{type:'image/png'})],value:'ref.png'}}));
  assert.ok(uploaded instanceof FormData);assert.equal(uploaded.get('image').name,'ref.png');
});
