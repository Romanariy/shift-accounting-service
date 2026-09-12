"use client";
import { useEffect, useRef } from "react";
import type { Item } from "./accounting-types";
const rub = (v:string) => new Intl.NumberFormat("ru-RU", {style:"currency",currency:"RUB"}).format(Number(v));

export default function RecalculationPreview({preview,busy,onClose,onApply}: {preview:any;busy:boolean;onClose:()=>void;onApply:()=>void}) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => { panel.current?.scrollIntoView({block:"start",behavior:"smooth"}); }, [preview]);
  return <section ref={panel} className="panel"><div className="panel-heading"><h2>Предпросмотр пересчёта · {preview.changes.length} изменений</h2><button className="icon-button" onClick={onClose} title="Закрыть пересчёт" aria-label="Закрыть пересчёт">×</button></div>
    <div className="padded"><p>Пересчёт применяет тарифы, действующие на дату каждой записи, включая автоначисления телефонов. Проверьте суммы «Было» и «Стало», затем нажмите «Применить проверенный пересчёт».</p>
      <p className="muted">Обрабатывается весь выбранный период по всем организациям, независимо от фильтров журнала. Записи без организации и работы в подтверждённых счетах пропускаются. После пересчёта обновите подготовленные отчёты.</p>
      {preview.errors.map((error:string) => <p key={error} className="inline-error">{error}</p>)}
      {(preview.skipped || []).map((group:any,i:number) => <p className="muted" key={i}>{group.date} · {group.organization} · {group.service}: пропущено — {group.reason}.</p>)}
      {(preview.groups || []).map((group:any,i:number) => <div className="recalculation-group" key={i}><h3>{group.date} · {group.organization} · {group.service}</h3><p>{group.hourly ? "Общая смена" : "Итого по записям"}: {rub(group.total)}{group.hourly && ` · ${group.hours} ч`}</p><div className="table-scroll"><table><thead><tr><th>Исполнитель</th><th>Часы / количество</th><th>Было</th><th>Стало</th></tr></thead><tbody>{group.members.map((member:Item) => <tr key={member.id}><td>{member.employee_name || "Без исполнителя"}<small>Запись №{member.id}</small></td><td>{member.hours}</td><td>{rub(member.before)}</td><td>{rub(member.after)}</td></tr>)}</tbody></table></div></div>)}
      {!preview.groups && preview.changes.map((change:Item) => <p key={change.id}>Запись №{change.id}: {rub(change.before)} → {rub(change.after)}</p>)}
      <button className="button" disabled={busy || !!preview.errors.length || !preview.changes.length} onClick={onApply}>Применить проверенный пересчёт</button>
    </div>
  </section>;
}
