"use client";

type Member = { id:number; employee_name:string; units:string; amount:string };
export type DailyGroup = { id:number; hours:string; total:string; price:string; minimum:string|null; maximum:string|null; members:Member[] };
const rub = (value:string) => new Intl.NumberFormat("ru-RU", {style:"currency",currency:"RUB"}).format(Number(value));

export default function SharedShiftDetails({group}: {group?:DailyGroup|null}) {
  if (!group || group.members.length < 2) return null;
  return <details className="shared-shift"><summary>Разделённая смена · {group.members.length} записи</summary>
    <div className="shared-shift-body"><p><strong>{group.hours} ч · {rub(group.total)}</strong><small>Общая ставка: {rub(group.price)}/ч{group.minimum !== null && ` · минимум ${rub(group.minimum)}`}{group.maximum !== null && ` · максимум ${rub(group.maximum)}`}</small></p>
      <ul>{group.members.map(member => <li key={member.id}><span>{member.employee_name || "Без исполнителя"}<small>Запись №{member.id} · {member.units} ч</small></span><strong>{rub(member.amount)}</strong></li>)}</ul>
      <small>Общий итог распределён пропорционально часам.</small>
    </div>
  </details>;
}
