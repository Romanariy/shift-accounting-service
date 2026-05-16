export type WorkType =
  | "big_admin"
  | "small_admin"
  | "photobar"
  | "cyclorama_painting"
  | "cleaning";

export type EntryStatus = "confirmed" | "needs_review";
export type EntryKind = "shift" | "companion";

export type Employee = {
  id: number;
  shortName: string;
  fullName: string;
  telegramUsername: string;
  telegramUserId: number | null;
  aliases: string[];
  defaultWorkType: WorkType;
  isActive: boolean;
  sortOrder: number;
};

export type PayRule = {
  id: number;
  code: string;
  title: string;
  calculationType: "fixed" | "hourly" | "per_unit";
  hourlyRate: string | null;
  fixedAmount: string | null;
  minAmount: string | null;
  maxAmount: string | null;
  activeFrom: string;
  activeTo: string | null;
  isActive: boolean;
};

export type ShiftEntry = {
  kind: "shift";
  id: number;
  date: string;
  employeeId: number | null;
  employeeName: string;
  workType: WorkType;
  workTypeLabel: string;
  startTime: string | null;
  endTime: string | null;
  hours: string;
  comment: string;
  calculatedAmount: string;
  source: string;
  status: EntryStatus;
  telegramAuthorUsername: string;
  rawText: string;
  syncStatus: string;
};

export type CompanionEntry = {
  kind: "companion";
  id: number;
  date: string;
  employeeId: number | null;
  employeeName: string;
  count: number;
  comment: string;
  calculatedAmount: string;
  source: string;
  status: EntryStatus;
  telegramAuthorUsername: string;
  rawText: string;
  syncStatus: string;
};

export type WorkEntry = ShiftEntry | CompanionEntry;

export type MonthSummary = {
  year: number;
  month: number;
  shiftTotal: string;
  companionTotal: string;
  phoneTotal: string;
  grandTotal: string;
  employees: Array<{
    employeeId: number;
    employeeName: string;
    shiftAmount: string;
    companionAmount: string;
    totalAmount: string;
  }>;
};

export type AuditLogEntry = {
  id: number;
  entityType: string;
  entityId: number;
  action: string;
  actor: string;
  diff: Record<string, { from: unknown; to: unknown }>;
  createdAt: string;
};

export type EntriesResponse = {
  shifts: ShiftEntry[];
  companions: CompanionEntry[];
  summary: MonthSummary;
};
