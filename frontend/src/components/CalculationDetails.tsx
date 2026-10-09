import type { Item } from "./accounting-types";
const rub=(v:unknown)=>new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(v||0));
export default function CalculationDetails({record}:{record:Item}) {
  if(record.base_amount==null)return null;
  return <small>База {rub(record.base_amount)}{Number(record.pricing_version)>=2?<>{record.actual_hours!=null&&<> · фактически {Number(record.actual_hours)} ч, пересечение {Number(record.overlap_hours)} ч, к оплате {Number(record.payable_hours)} ч</>}</>:<> × {Number(record.overlap_factor||1)}</>}{Number(record.bonus)>0&&<> + доплата {rub(record.bonus)}{record.bonus_reason&&` (${record.bonus_reason})`}</>}{record.calculation_note&&<><br/>{record.calculation_note}</>}</small>;
}
