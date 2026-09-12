// Component-level audit only: this does not replace real browser validation.
// Run from repository root: node audit/2026-09-12/probe_frontend.cjs
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const req = Module.createRequire(path.resolve(__dirname, '../../frontend/package.json'));
const ts = req('typescript');
const React = req('react');
const { create, act } = req('react-test-renderer');
for (const ext of ['.ts', '.tsx']) require.extensions[ext] = (module, filename) => {
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true }, fileName: filename,
  });
  module._compile(output.outputText, filename);
};
const originalLoad = Module._load;
Module._load = function(name, ...args) {
  if (name === 'next/link') return ({href,children,...props}) => React.createElement('a',{href,...props},children);
  return originalLoad.call(this,name,...args);
};
const AccountingApp = require('../../frontend/src/components/AccountingApp').default;
Module._load = originalLoad;
const text = n => typeof n === 'string' ? n : Array.isArray(n) ? n.map(text).join('') : n?.children?.map(text).join('') || '';
const button = (root, label) => root.findAllByType('button').find(n => text(n) === label);
const field = (root, label) => root.findAllByType('label').find(n => n.findAllByType('span').some(s => text(s).replace(' *','') === label));
const response = data => ({ok:true,json:async()=>data});
const bootstrap = {
  services:[{id:1,name:'Audit service',aliases:[],active:true,input_type:'time',frequency:'entry'}], rates:[],
  organization_list:[{id:1,name:'Audit org',active:true}], employees:[{id:1,name:'Audit person',active:true}],
  accruals:[],preferences:[],contacts:[],organizations:[],settings:{},
};
let failReload = false, writeCount = 0, writes = [];
global.window = {addEventListener(){},removeEventListener(){}};
global.document = {hidden:false};
global.fetch = async (url,options={}) => {
  if (options.method === 'POST' && url.endsWith('records/')) {
    writeCount++;
    writes.push(JSON.parse(options.body));
    if(writeCount === 1) failReload = true;
    return response({id:writeCount});
  }
  if (url.includes('bootstrap/')) {
    if (failReload) {failReload=false; throw new Error('Audit: reload connection lost after successful save');}
    return response(bootstrap);
  }
  if (url.includes('records/')) return response({records:[],count:0,total:'0',review:0,employee_totals:[]});
  return response([]);
};
(async()=>{
  let renderer;
  try {
    await act(async()=>{renderer=create(React.createElement(AccountingApp), {createNodeMock:e=>e.type==='dialog'?{showModal(){},close(){}}:null});});
    const root=renderer.root;
    await act(async()=>button(root,'Журнал работ').props.onClick());
    await act(async()=>root.findByProps({'aria-label':'Месяц журнала'}).props.onChange({target:{value:'2026-04',validity:{valid:true}}}));
    await act(async()=>button(root,'+ Записать услугу').props.onClick());
    console.log(JSON.stringify({probe:'service_form_defaults',amount:field(root,'Сумма для ввода готовой стоимости').findByType('input').props.value,
      amountRequired:!!field(root,'Сумма для ввода готовой стоимости').findByType('input').props.required,
      selectedMonth:'2026-04',newRecordDate:field(root,'Дата').findByType('input').props.value}));
    await act(async()=>button(root,'Отмена').props.onClick());
    await act(async()=>button(root,'Разовая работа').props.onClick());
    for(const [name,value,type] of [['Организация','1','select'],['Исполнитель','1','select'],['Сумма, ₽','100','input'],['Что сделано / комментарий','Audit oneoff','textarea']]) {
      await act(async()=>field(root,name).findByType(type).props.onChange({target:{value}}));
    }
    await act(async()=>root.findByType('form').props.onSubmit({preventDefault(){}}));
    console.log(JSON.stringify({probe:'successful_write_failed_reload',writes:writeCount,formRemainsOpen:root.findAllByType('form').length>0,errorVisible:text(renderer.toJSON()).includes('Audit: reload')}));
    await act(async()=>root.findByType('form').props.onSubmit({preventDefault(){}}));
    console.log(JSON.stringify({probe:'retry_after_failed_reload',writes:writeCount,identicalPayloads:JSON.stringify(writes[0])===JSON.stringify(writes[1])}));
  } finally { if(renderer) act(()=>renderer.unmount()); }
})().catch(e=>{console.error(e);process.exitCode=1;});
