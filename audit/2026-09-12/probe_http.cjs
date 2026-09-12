// Integration probe for the isolated audit app only, at 127.0.0.1:3100.
// This performs writes. Never point it at production. No worker/bot is started.
const base = 'http://127.0.0.1:3100/api/shifts/ledger/';
async function request(path, method='GET', payload) {
  const response = await fetch(base+path,{method,headers:{'Content-Type':'application/json'},body:payload===undefined?undefined:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${JSON.stringify(data)}`);
  return data;
}
function emit(probe, result) { console.log(JSON.stringify({probe,...result})); }
(async()=>{
  const config=await request('bootstrap/');
  const organization=config.organization_list.find(o=>o.active).id;
  const employee=config.employees.find(e=>e.active).id;
  const service=config.services.find(s=>s.legacy_code==='small_admin').id;
  const common={organization,employee,date:'2026-04-12',units:'1'};
  const work=await request('records/','POST',{...common,kind:'oneoff',description:'Audit HTTP work',amount:'100'});
  const expense=await request('records/','POST',{...common,kind:'expense',description:'Audit HTTP expense',amount:'50'});
  const shift=await request('records/','POST',{...common,kind:'service',service,start_time:'10:00',end_time:'14:00',amount:'0'});
  const changed=await request(`records/${shift.id}/`,'PUT',{description:'Audit HTTP comment'});
  emit('create_and_edit',{work:work.amount,expense:expense.amount,shift:shift.amount,commentPreservesAmount:changed.amount===shift.amount});
  const journal=await request('records/?month=2026-04');
  emit('journal',{count:journal.count,total:journal.total});
  const preview=await request('recalculate/','POST',{start:'2026-04-01',end:'2026-04-30'});
  const applied=await request('recalculate/','POST',{start:'2026-04-01',end:'2026-04-30',fingerprint:preview.fingerprint});
  emit('recalculate',{errors:applied.errors});
  const batch=await request('batches/','POST',{start:'2026-04-12',end:'2026-04-12',organizations:[organization]});
  const invoice=batch.invoices[0];
  const file=await fetch(base+`invoices/${invoice.id}/file/`);
  const bytes=new Uint8Array(await file.arrayBuffer());
  emit('invoice_excel',{status:file.status,zipSignature:bytes[0]===80&&bytes[1]===75,bytes:bytes.length,total:invoice.total,errors:invoice.errors});
  const revised=await request(`invoices/${invoice.id}/`,'PUT',{adjustments:[{description:'Audit discount',amount:'-10'}]});
  emit('invoice_adjustment',{total:revised.invoices[0].total});
  const approval=await fetch(base+`batches/${batch.id}/approve/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:revised.version})});
  emit('approval_without_recipient',{status:approval.status,body:await approval.json()});
  const history=await request(`audit/?entity_id=${shift.id}&entity_type=record`);
  emit('history',{events:history.length});
  await request(`batches/${batch.id}/`,'DELETE',{});
  for(const row of [work,expense,shift]) await request(`records/${row.id}/`,'DELETE',{});
  emit('cleanup',{remaining:(await request('records/?month=2026-04')).count});
})().catch(e=>{console.error(e);process.exitCode=1;});
