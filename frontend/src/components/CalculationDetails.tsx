import type { Item } from "./accounting-types";
const rub=(v:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(v||0));
export default function CalculationDetails({record}:{record:Item}) {
  if(record.base_amount==null&&!Number(record.client_bonus)&&!record.client_bonus_review)return null;
  return <small className="calculation-details">База {rub(record.base_amount)}{Number(record.pricing_version)>=2?<>{record.actual_hours!=null&&<> · фактически {Number(record.actual_hours)} ч, пересечение {Number(record.overlap_hours)} ч, к оплате {Number(record.payable_hours)} ч</>}</>:<> × {Number(record.overlap_factor||1)}</>}{Number(record.bonus)>0&&<> + доплата {rub(record.bonus)}{record.bonus_reason&&` (${record.bonus_reason})`}</>}{Number(record.client_bonus)>0&&<> + клиентские доплаты {rub(record.client_bonus)}{(record.client_bonus_details||[]).map((d:any)=><span key={d.booking}><br/>{d.rule} · {d.client} · {d.start}–{d.end} · {rub(d.amount)}</span>)}</>}{record.client_bonus_review&&<><br/>Клиентская доплата требует проверки в «Правилах доплат».</>}{record.calculation_note&&<><br/>{record.calculation_note}</>}</small>;
}
