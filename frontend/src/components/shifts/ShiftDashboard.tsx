"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import type {
  AuditLogEntry,
  CompanionEntry,
  Employee,
  EntriesResponse,
  EntryKind,
  EntryStatus,
  PayRule,
  ShiftEntry,
  WorkEntry,
  WorkType,
} from "../../types/shifts";

const workTypes: Array<{ value: WorkType; label: string }> = [
  { value: "big_admin", label: "Большой админ" },
  { value: "small_admin", label: "Малый админ" },
  { value: "photobar", label: "Фотобар" },
  { value: "cyclorama_painting", label: "Покраска циклораммы" },
  { value: "cleaning", label: "Уборка" },
];

const payCodes = [
  ...workTypes,
  { value: "companion", label: "Сопровождение" },
  { value: "phone_with_big_admin", label: "Телефоны при большом админе" },
  { value: "phone_without_big_admin", label: "Телефоны без большого админа" },
];

type EntryForm = {
  id: number | null;
  kind: EntryKind;
  date: string;
  employeeId: string;
  workType: WorkType;
  startTime: string;
  endTime: string;
  hours: string;
  count: string;
  comment: string;
  status: EntryStatus;
};

type EmployeeForm = {
  id: number | null;
  shortName: string;
  fullName: string;
  telegramUsername: string;
  aliases: string;
  defaultWorkType: WorkType;
  isActive: boolean;
  sortOrder: string;
};

type PayRuleForm = {
  id: number | null;
  code: string;
  title: string;
  calculationType: "fixed" | "hourly" | "per_unit";
  hourlyRate: string;
  fixedAmount: string;
  minAmount: string;
  maxAmount: string;
  activeFrom: string;
  activeTo: string;
  isActive: boolean;
};

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function currentMonth() {
  return new Date().toISOString().slice(0, 7);
}

function splitMonth(value: string) {
  const [year, month] = value.split("-").map((part) => Number.parseInt(part, 10));
  return { year, month };
}

function createEntryForm(date = todayIso()): EntryForm {
  return {
    id: null,
    kind: "shift",
    date,
    employeeId: "",
    workType: "small_admin",
    startTime: "",
    endTime: "",
    hours: "",
    count: "1",
    comment: "",
    status: "confirmed",
  };
}

function createEmployeeForm(): EmployeeForm {
  return {
    id: null,
    shortName: "",
    fullName: "",
    telegramUsername: "",
    aliases: "",
    defaultWorkType: "small_admin",
    isActive: true,
    sortOrder: "100",
  };
}

function createPayRuleForm(): PayRuleForm {
  return {
    id: null,
    code: "small_admin",
    title: "Малый админ",
    calculationType: "hourly",
    hourlyRate: "200.00",
    fixedAmount: "",
    minAmount: "600.00",
    maxAmount: "1200.00",
    activeFrom: "2026-01-01",
    activeTo: "",
    isActive: true,
  };
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error ?? "Ошибка запроса.");
  }

  return response.json() as Promise<T>;
}

function money(value: string | null | undefined) {
  return `${Number(value ?? 0).toLocaleString("ru-RU")} ₽`;
}

function entrySortValue(entry: WorkEntry) {
  return `${entry.date}-${entry.kind}-${entry.id}`;
}

function getEntryAmount(entry: WorkEntry) {
  return money(entry.calculatedAmount);
}

export default function ShiftDashboard() {
  const [month, setMonth] = useState(currentMonth());
  const [employeeFilter, setEmployeeFilter] = useState("");
  const [workTypeFilter, setWorkTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [payRules, setPayRules] = useState<PayRule[]>([]);
  const [entries, setEntries] = useState<EntriesResponse | null>(null);
  const [entryForm, setEntryForm] = useState<EntryForm>(() => createEntryForm());
  const [employeeForm, setEmployeeForm] = useState<EmployeeForm>(() => createEmployeeForm());
  const [payRuleForm, setPayRuleForm] = useState<PayRuleForm>(() => createPayRuleForm());
  const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([]);
  const [selectedAuditTitle, setSelectedAuditTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const activeEmployees = useMemo(
    () => employees.filter((employee) => employee.isActive),
    [employees]
  );

  const tableEntries = useMemo(() => {
    const combined = [...(entries?.shifts ?? []), ...(entries?.companions ?? [])];
    return combined.sort((left, right) => entrySortValue(right).localeCompare(entrySortValue(left)));
  }, [entries]);

  const { year, month: monthNumber } = splitMonth(month);
  const reportUrl = `/api/shifts/report.xlsx?year=${year}&month=${monthNumber}`;

  const loadData = useCallback(async () => {
    const query = new URLSearchParams({
      year: String(year),
      month: String(monthNumber),
    });

    if (employeeFilter) {
      query.set("employeeId", employeeFilter);
    }

    if (workTypeFilter) {
      query.set("workType", workTypeFilter);
    }

    if (statusFilter) {
      query.set("status", statusFilter);
    }

    setIsLoading(true);
    setError(null);

    try {
      const [employeePayload, payRulePayload, entryPayload] = await Promise.all([
        fetchJson<{ employees: Employee[] }>("/api/shifts/employees"),
        fetchJson<{ payRules: PayRule[] }>("/api/shifts/pay-rules"),
        fetchJson<EntriesResponse>(`/api/shifts/entries?${query.toString()}`),
      ]);

      setEmployees(employeePayload.employees);
      setPayRules(payRulePayload.payRules);
      setEntries(entryPayload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить данные.");
    } finally {
      setIsLoading(false);
    }
  }, [employeeFilter, monthNumber, statusFilter, workTypeFilter, year]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const resetEntryForm = () => {
    setEntryForm(createEntryForm(`${month}-01`));
  };

  const saveEntry = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const payload = {
      kind: entryForm.kind,
      date: entryForm.date,
      employeeId: entryForm.employeeId ? Number(entryForm.employeeId) : null,
      workType: entryForm.workType,
      startTime: entryForm.startTime || null,
      endTime: entryForm.endTime || null,
      hours: entryForm.hours,
      count: Number(entryForm.count || 1),
      comment: entryForm.comment,
      status: entryForm.status,
    };
    const url = entryForm.id
      ? `/api/shifts/entries/${entryForm.kind}/${entryForm.id}`
      : "/api/shifts/entries";

    await fetchJson(url, {
      method: entryForm.id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    resetEntryForm();
    await loadData();
  };

  const editEntry = (entry: WorkEntry) => {
    setEntryForm({
      id: entry.id,
      kind: entry.kind,
      date: entry.date,
      employeeId: entry.employeeId ? String(entry.employeeId) : "",
      workType: entry.kind === "shift" ? entry.workType : "small_admin",
      startTime: entry.kind === "shift" ? entry.startTime ?? "" : "",
      endTime: entry.kind === "shift" ? entry.endTime ?? "" : "",
      hours: entry.kind === "shift" ? entry.hours : "",
      count: entry.kind === "companion" ? String(entry.count) : "1",
      comment: entry.comment,
      status: entry.status,
    });
  };

  const deleteEntry = async (entry: WorkEntry) => {
    await fetchJson(`/api/shifts/entries/${entry.kind}/${entry.id}`, { method: "DELETE" });
    await loadData();
  };

  const loadAudit = async (entry: WorkEntry) => {
    const payload = await fetchJson<{ auditLog: AuditLogEntry[] }>(
      `/api/shifts/audit-log?entityType=${entry.kind}&entityId=${entry.id}`
    );
    setSelectedAuditTitle(`${entry.kind === "shift" ? "Смена" : "Сопровождение"} #${entry.id}`);
    setAuditLog(payload.auditLog);
  };

  const saveEmployee = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const payload = {
      shortName: employeeForm.shortName,
      fullName: employeeForm.fullName,
      telegramUsername: employeeForm.telegramUsername,
      aliases: employeeForm.aliases
        .split(",")
        .map((alias) => alias.trim())
        .filter(Boolean),
      defaultWorkType: employeeForm.defaultWorkType,
      isActive: employeeForm.isActive,
      sortOrder: Number(employeeForm.sortOrder || 100),
    };

    await fetchJson(
      employeeForm.id ? `/api/shifts/employees/${employeeForm.id}` : "/api/shifts/employees",
      {
        method: employeeForm.id ? "PUT" : "POST",
        body: JSON.stringify(payload),
      }
    );
    setEmployeeForm(createEmployeeForm());
    await loadData();
  };

  const editEmployee = (employee: Employee) => {
    setEmployeeForm({
      id: employee.id,
      shortName: employee.shortName,
      fullName: employee.fullName,
      telegramUsername: employee.telegramUsername,
      aliases: employee.aliases.join(", "),
      defaultWorkType: employee.defaultWorkType,
      isActive: employee.isActive,
      sortOrder: String(employee.sortOrder),
    });
  };

  const savePayRule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const payload = {
      code: payRuleForm.code,
      title: payRuleForm.title,
      calculationType: payRuleForm.calculationType,
      hourlyRate: payRuleForm.hourlyRate,
      fixedAmount: payRuleForm.fixedAmount,
      minAmount: payRuleForm.minAmount,
      maxAmount: payRuleForm.maxAmount,
      activeFrom: payRuleForm.activeFrom,
      activeTo: payRuleForm.activeTo || null,
      isActive: payRuleForm.isActive,
    };

    await fetchJson(
      payRuleForm.id ? `/api/shifts/pay-rules/${payRuleForm.id}` : "/api/shifts/pay-rules",
      {
        method: payRuleForm.id ? "PUT" : "POST",
        body: JSON.stringify(payload),
      }
    );
    setPayRuleForm(createPayRuleForm());
    await loadData();
  };

  const editPayRule = (rule: PayRule) => {
    setPayRuleForm({
      id: rule.id,
      code: rule.code,
      title: rule.title,
      calculationType: rule.calculationType,
      hourlyRate: rule.hourlyRate ?? "",
      fixedAmount: rule.fixedAmount ?? "",
      minAmount: rule.minAmount ?? "",
      maxAmount: rule.maxAmount ?? "",
      activeFrom: rule.activeFrom,
      activeTo: rule.activeTo ?? "",
      isActive: rule.isActive,
    });
  };

  return (
    <main className="shift-page">
      <section className="shift-toolbar">
        <div>
          <h1 className="shift-title">Учет смен</h1>
          <p className="shift-subtitle">Telegram, ручные записи, история и Excel-отчеты</p>
        </div>

        <div className="shift-toolbar__controls">
          <input
            className="ui-input"
            type="month"
            value={month}
            onChange={(event) => setMonth(event.target.value)}
          />
          <select
            className="ui-select"
            value={employeeFilter}
            onChange={(event) => setEmployeeFilter(event.target.value)}
          >
            <option value="">Все сотрудники</option>
            {activeEmployees.map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.shortName}
              </option>
            ))}
          </select>
          <select
            className="ui-select"
            value={workTypeFilter}
            onChange={(event) => setWorkTypeFilter(event.target.value)}
          >
            <option value="">Все типы</option>
            {workTypes.map((workType) => (
              <option key={workType.value} value={workType.value}>
                {workType.label}
              </option>
            ))}
          </select>
          <select
            className="ui-select"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">Все статусы</option>
            <option value="confirmed">Подтверждено</option>
            <option value="needs_review">Нужно проверить</option>
          </select>
          <a className="ui-button ui-button--default shift-report-link" href={reportUrl}>
            Excel
          </a>
        </div>
      </section>

      {error ? <p className="status-alert">{error}</p> : null}
      {isLoading ? <p className="status-info">Загрузка данных...</p> : null}

      {entries ? (
        <section className="shift-summary">
          <div>
            <span>Смены</span>
            <strong>{money(entries.summary.shiftTotal)}</strong>
          </div>
          <div>
            <span>Сопровождения</span>
            <strong>{money(entries.summary.companionTotal)}</strong>
          </div>
          <div>
            <span>Телефоны</span>
            <strong>{money(entries.summary.phoneTotal)}</strong>
          </div>
          <div>
            <span>Итого</span>
            <strong>{money(entries.summary.grandTotal)}</strong>
          </div>
        </section>
      ) : null}

      <section className="shift-layout">
        <form className="shift-panel" onSubmit={saveEntry}>
          <div className="shift-panel__header">
            <h2>{entryForm.id ? "Редактирование записи" : "Новая запись"}</h2>
            <button className="ui-button ui-button--outline ui-button--sm" type="button" onClick={resetEntryForm}>
              Сброс
            </button>
          </div>

          <div className="shift-form-grid">
            <label>
              Тип записи
              <select
                className="ui-select"
                value={entryForm.kind}
                onChange={(event) =>
                  setEntryForm((form) => ({ ...form, kind: event.target.value as EntryKind }))
                }
              >
                <option value="shift">Смена</option>
                <option value="companion">Сопровождение</option>
              </select>
            </label>
            <label>
              Дата
              <input
                className="ui-input"
                type="date"
                required
                value={entryForm.date}
                onChange={(event) => setEntryForm((form) => ({ ...form, date: event.target.value }))}
              />
            </label>
            <label>
              Сотрудник
              <select
                className="ui-select"
                value={entryForm.employeeId}
                onChange={(event) =>
                  setEntryForm((form) => ({ ...form, employeeId: event.target.value }))
                }
              >
                <option value="">Не назначен</option>
                {activeEmployees.map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.shortName}
                  </option>
                ))}
              </select>
            </label>
            {entryForm.kind === "shift" ? (
              <>
                <label>
                  Тип работы
                  <select
                    className="ui-select"
                    value={entryForm.workType}
                    onChange={(event) =>
                      setEntryForm((form) => ({ ...form, workType: event.target.value as WorkType }))
                    }
                  >
                    {workTypes.map((workType) => (
                      <option key={workType.value} value={workType.value}>
                        {workType.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Начало
                  <input
                    className="ui-input"
                    type="time"
                    value={entryForm.startTime}
                    onChange={(event) =>
                      setEntryForm((form) => ({ ...form, startTime: event.target.value }))
                    }
                  />
                </label>
                <label>
                  Конец
                  <input
                    className="ui-input"
                    type="time"
                    value={entryForm.endTime}
                    onChange={(event) =>
                      setEntryForm((form) => ({ ...form, endTime: event.target.value }))
                    }
                  />
                </label>
                <label>
                  Часы
                  <input
                    className="ui-input"
                    inputMode="decimal"
                    value={entryForm.hours}
                    placeholder="авто"
                    onChange={(event) =>
                      setEntryForm((form) => ({ ...form, hours: event.target.value }))
                    }
                  />
                </label>
              </>
            ) : (
              <label>
                Кол-во сопровождений
                <input
                  className="ui-input"
                  min={1}
                  type="number"
                  value={entryForm.count}
                  onChange={(event) => setEntryForm((form) => ({ ...form, count: event.target.value }))}
                />
              </label>
            )}
            <label>
              Статус
              <select
                className="ui-select"
                value={entryForm.status}
                onChange={(event) =>
                  setEntryForm((form) => ({ ...form, status: event.target.value as EntryStatus }))
                }
              >
                <option value="confirmed">Подтверждено</option>
                <option value="needs_review">Нужно проверить</option>
              </select>
            </label>
            <label className="shift-form-grid__wide">
              Комментарий
              <input
                className="ui-input"
                value={entryForm.comment}
                onChange={(event) =>
                  setEntryForm((form) => ({ ...form, comment: event.target.value }))
                }
              />
            </label>
          </div>

          <button className="ui-button ui-button--default" type="submit">
            {entryForm.id ? "Сохранить" : "Добавить"}
          </button>
        </form>

        <section className="shift-panel shift-panel--table">
          <div className="shift-panel__header">
            <h2>Записи месяца</h2>
            <span>{tableEntries.length}</span>
          </div>

          <div className="shift-table">
            <div className="shift-table__head">
              <span>Дата</span>
              <span>Кто</span>
              <span>Тип</span>
              <span>Объем</span>
              <span>Сумма</span>
              <span>Действия</span>
            </div>
            {tableEntries.map((entry) => (
              <div className="shift-table__row" key={`${entry.kind}-${entry.id}`}>
                <span>{entry.date}</span>
                <span>{entry.employeeName || "Не назначен"}</span>
                <span>
                  {entry.kind === "shift" ? (entry as ShiftEntry).workTypeLabel : "Сопровождение"}
                </span>
                <span>
                  {entry.kind === "shift"
                    ? `${(entry as ShiftEntry).hours} ч`
                    : `${(entry as CompanionEntry).count} шт.`}
                </span>
                <strong>{getEntryAmount(entry)}</strong>
                <span className="shift-table__actions">
                  <button className="ui-button ui-button--outline ui-button--sm" type="button" onClick={() => editEntry(entry)}>
                    Править
                  </button>
                  <button className="ui-button ui-button--secondary ui-button--sm" type="button" onClick={() => loadAudit(entry)}>
                    История
                  </button>
                  <button className="ui-button ui-button--outline ui-button--sm" type="button" onClick={() => deleteEntry(entry)}>
                    Удалить
                  </button>
                </span>
              </div>
            ))}
          </div>
        </section>
      </section>

      <section className="shift-layout shift-layout--bottom">
        <section className="shift-panel">
          <div className="shift-panel__header">
            <h2>Сотрудники</h2>
            <button className="ui-button ui-button--outline ui-button--sm" type="button" onClick={() => setEmployeeForm(createEmployeeForm())}>
              Новый
            </button>
          </div>
          <form className="shift-form-grid" onSubmit={saveEmployee}>
            <label>
              Имя
              <input className="ui-input" required value={employeeForm.shortName} onChange={(event) => setEmployeeForm((form) => ({ ...form, shortName: event.target.value }))} />
            </label>
            <label>
              Telegram
              <input className="ui-input" value={employeeForm.telegramUsername} onChange={(event) => setEmployeeForm((form) => ({ ...form, telegramUsername: event.target.value }))} />
            </label>
            <label>
              Роль по умолчанию
              <select className="ui-select" value={employeeForm.defaultWorkType} onChange={(event) => setEmployeeForm((form) => ({ ...form, defaultWorkType: event.target.value as WorkType }))}>
                {workTypes.map((workType) => (
                  <option key={workType.value} value={workType.value}>{workType.label}</option>
                ))}
              </select>
            </label>
            <label>
              Алиасы
              <input className="ui-input" value={employeeForm.aliases} onChange={(event) => setEmployeeForm((form) => ({ ...form, aliases: event.target.value }))} />
            </label>
            <button className="ui-button ui-button--default" type="submit">Сохранить сотрудника</button>
          </form>
          <div className="shift-chip-list">
            {employees.map((employee) => (
              <button className="shift-chip" key={employee.id} type="button" onClick={() => editEmployee(employee)}>
                {employee.shortName} {employee.telegramUsername ? `@${employee.telegramUsername}` : ""}
              </button>
            ))}
          </div>
        </section>

        <section className="shift-panel">
          <div className="shift-panel__header">
            <h2>Стоимость оплаты</h2>
            <button className="ui-button ui-button--outline ui-button--sm" type="button" onClick={() => setPayRuleForm(createPayRuleForm())}>
              Новая
            </button>
          </div>
          <form className="shift-form-grid" onSubmit={savePayRule}>
            <label>
              Код
              <select className="ui-select" value={payRuleForm.code} onChange={(event) => setPayRuleForm((form) => ({ ...form, code: event.target.value }))}>
                {payCodes.map((code) => (
                  <option key={code.value} value={code.value}>{code.label}</option>
                ))}
              </select>
            </label>
            <label>
              Название
              <input className="ui-input" required value={payRuleForm.title} onChange={(event) => setPayRuleForm((form) => ({ ...form, title: event.target.value }))} />
            </label>
            <label>
              Тип расчета
              <select className="ui-select" value={payRuleForm.calculationType} onChange={(event) => setPayRuleForm((form) => ({ ...form, calculationType: event.target.value as PayRuleForm["calculationType"] }))}>
                <option value="fixed">Фиксированная</option>
                <option value="hourly">Почасовая</option>
                <option value="per_unit">За штуку</option>
              </select>
            </label>
            <label>
              Ставка/час
              <input className="ui-input" value={payRuleForm.hourlyRate} onChange={(event) => setPayRuleForm((form) => ({ ...form, hourlyRate: event.target.value }))} />
            </label>
            <label>
              Фикс/штука
              <input className="ui-input" value={payRuleForm.fixedAmount} onChange={(event) => setPayRuleForm((form) => ({ ...form, fixedAmount: event.target.value }))} />
            </label>
            <label>
              Мин.
              <input className="ui-input" value={payRuleForm.minAmount} onChange={(event) => setPayRuleForm((form) => ({ ...form, minAmount: event.target.value }))} />
            </label>
            <label>
              Макс.
              <input className="ui-input" value={payRuleForm.maxAmount} onChange={(event) => setPayRuleForm((form) => ({ ...form, maxAmount: event.target.value }))} />
            </label>
            <label>
              С даты
              <input className="ui-input" type="date" required value={payRuleForm.activeFrom} onChange={(event) => setPayRuleForm((form) => ({ ...form, activeFrom: event.target.value }))} />
            </label>
            <button className="ui-button ui-button--default" type="submit">Сохранить правило</button>
          </form>
          <div className="shift-chip-list">
            {payRules.map((rule) => (
              <button className="shift-chip" key={rule.id} type="button" onClick={() => editPayRule(rule)}>
                {rule.title}: {rule.hourlyRate ? `${rule.hourlyRate}/ч` : rule.fixedAmount}
              </button>
            ))}
          </div>
        </section>

        <section className="shift-panel">
          <div className="shift-panel__header">
            <h2>История</h2>
            <span>{selectedAuditTitle}</span>
          </div>
          <div className="shift-audit-list">
            {auditLog.map((item) => (
              <article key={item.id}>
                <strong>{item.action}</strong>
                <span>{new Date(item.createdAt).toLocaleString("ru-RU")}</span>
                <code>{Object.keys(item.diff).join(", ") || "без изменений"}</code>
              </article>
            ))}
            {auditLog.length === 0 ? <p className="shift-empty">Выберите запись в таблице.</p> : null}
          </div>
        </section>
      </section>
    </main>
  );
}
