"use client";

export type JournalFilterValues = { employee: string; service: string; kind: string; status: string; source: string };
export const emptyJournalFilters: JournalFilterValues = { employee: "", service: "", kind: "", status: "", source: "" };

type Option = { value: number | string; label: string };
type Props = {
  filters: JournalFilterValues;
  onChange: (values: JournalFilterValues) => void;
  employees: Option[];
  services: Option[];
  organizations: Option[];
  organization: string;
  onOrganization: (value: string) => void;
  expenses: boolean;
  search: string;
  onSearch: (value: string) => void;
  ordering: string;
  onOrdering: (value: string) => void;
  onReset: () => void;
};

export const orderingOptions = [
  ["-date", "Сначала новые"], ["date", "Сначала старые"],
  ["-amount", "Сумма: по убыванию"], ["amount", "Сумма: по возрастанию"],
  ["employee", "По сотруднику А–Я"], ["-employee", "По сотруднику Я–А"],
  ["organization", "По организации А–Я"], ["-organization", "По организации Я–А"],
  ["service", "По работе А–Я"], ["-service", "По работе Я–А"],
  ["units", "Объём: по возрастанию"], ["-units", "Объём: по убыванию"],
];

export default function JournalFilters(props: Props) {
  const { filters, onChange } = props;
  const select = (key: keyof JournalFilterValues, label: string, options: Option[]) => (
    <label key={key}><span>{label}</span><select value={filters[key]} onChange={e => onChange({ ...filters, [key]: e.target.value })}>
      <option value="">Все</option>{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select></label>
  );
  return <div className="journal-filters" id="journal-filters">
    <label className="journal-search"><span>Поиск по всем записям месяца</span><input type="search" value={props.search} onChange={e => props.onSearch(e.target.value)} placeholder="Работа, описание, сотрудник…" /></label>
    <label><span>Организация</span><select value={props.organization} onChange={e => props.onOrganization(e.target.value)}><option value="">Все организации</option>{props.organizations.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
    {!props.expenses && select("employee", "Сотрудник", [...props.employees, { value: "unassigned", label: "Без исполнителя" }])}
    {!props.expenses && select("service", "Услуга", props.services)}
    {!props.expenses && select("kind", "Вид работы", [{ value: "service", label: "Типовая услуга" }, { value: "oneoff", label: "Разовая работа" }])}
    {select("status", "Статус", [{ value: "ready", label: "Учтено" }, { value: "review", label: "Нужно проверить" }])}
    {select("source", "Источник", [{ value: "web", label: "Сайт" }, { value: "telegram", label: "Telegram" }, { value: "auto", label: "Автоначисление" }, { value: "demo", label: "Тестовые данные" }, { value: "import", label: "Импорт" }, { value: "manual", label: "Старый ручной ввод" }])}
    <label><span>Сортировка</span><select value={props.ordering} onChange={e => props.onOrdering(e.target.value)}>{orderingOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <button type="button" className="button secondary compact reset-filters" onClick={props.onReset} title="Сбросить поиск, организацию, фильтры и сортировку">Сбросить фильтры</button>
  </div>;
}

export function FilterToggle({open, count, controls, onClick}: {open:boolean;count:number;controls:string;onClick:()=>void}) {
  return <button type="button" className="button secondary filter-toggle" aria-expanded={open} aria-controls={controls} onClick={onClick}>
    Фильтры{count > 0 && <span className="filter-count" aria-label={`Активных условий: ${count}`}>{count}</span>}<span aria-hidden="true">{open ? "↑" : "↓"}</span>
  </button>;
}

export function SortHeading({field, label, ordering, onChange, right=false}: {field:string;label:string;ordering:string;onChange:(value:string)=>void;right?:boolean}) {
  const selected = ordering.replace(/^-/, "") === field;
  const descending = selected && ordering.startsWith("-");
  return <th className={right ? "right" : undefined} aria-sort={selected ? descending ? "descending" : "ascending" : "none"}>
    <button type="button" className="sort-heading" onClick={() => onChange(selected && !descending ? "-" + field : field)} title={`Сортировать: ${label.toLowerCase()}`}>
      {label} <span aria-hidden="true">{selected ? descending ? "↓" : "↑" : "↕"}</span>
    </button>
  </th>;
}

export function MonthPicker({value,onChange,max}: {value:string;onChange:(month:string)=>void;max:string}) {
  function step(delta:number) {
    const [year, month] = value.split("-").map(Number);
    onChange(new Date(Date.UTC(year, month - 1 + delta, 1)).toISOString().slice(0, 7));
  }
  const label = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(value+"-01T00:00:00Z"));
  return <div className="month-picker">
    <button type="button" className="month-arrow" onClick={()=>step(-1)} title="Предыдущий месяц" aria-label="Предыдущий месяц">‹</button>
    <label><span>{label}</span><input type="month" value={value} max={max} aria-label="Месяц журнала" onChange={e=>{if(e.target.value && e.target.validity.valid)onChange(e.target.value);}} /></label>
    <button type="button" className="month-arrow" onClick={()=>step(1)} disabled={value>=max} title="Следующий месяц" aria-label="Следующий месяц">›</button>
  </div>;
}
