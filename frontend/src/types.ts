/** Shapes returned by the FastAPI backend (backend/app/routes.py). */

export type Candidate = Record<string, string | number | boolean | null>

export type Cell =
  | string
  | number
  | boolean
  | null
  | number[]
  | Candidate[]
  | Record<string, string | number | null>

export type Row = Record<string, Cell>

export interface SummaryRow {
  Category: string
  Count: number | null
  Amount: number | null
  indent: boolean
}

export interface Selfcheck {
  stated_count: number
  stated_total: number
  parsed_count: number
  parsed_total: number
  /** present on gold-sourced runs: parse-time gate already ran at
   *  ingest, so a mismatch here is informational, not fatal */
  passed?: boolean
}

export interface IngestStats {
  files_reused: number
  rows_inserted: number
  bills_updated: number
  rows_reused: number
  conflicts: number
}

export interface LedgerStats {
  matches_created: number
  auto_locked: number
  exceptions_opened: number
  exceptions_resolved: number
}

export interface ReconMeta {
  counts: {
    matched: number
    bank_only: number
    bill_only: number
    match_review: number
    bank_credits: number
    bank_txns?: number
    bills?: number
    bills_grouped?: number
    recoveries?: number
  }
  selfcheck: Selfcheck | null
  config: Record<string, number | boolean>
  filenames: { statement: string; bills: string; rnote: string | null; crn: string | null }
  customer?: string
  mode?: 'snapshot' | 'incremental'
  ingest?: IngestStats
  ledger?: LedgerStats
  statement_bronze_id?: number
  rules_effective?: {
    field_map: FieldMap
    paid_statuses: string[]
    weights: Record<string, number>
  }
}

export interface ReconResponse {
  run_id: string
  summary: SummaryRow[]
  matched: Row[]
  exceptions: Row[]
  meta: ReconMeta
}

/** Source frames a completed run exposes via /api/runs/{id}/frames/{name}. */
export type FrameName = 'bank' | 'bills' | 'bills_enriched' | 'recoveries'

export interface DefaultFileInfo {
  name: string
  size: number
}

/** Repo sample document backing each field when nothing is uploaded. */
export type Defaults = Record<'statement' | 'bills' | 'rnote' | 'crn', DefaultFileInfo | null>

export class ApiError extends Error {
  code: string
  constructor(code: string, detail: string) {
    super(detail)
    this.code = code
  }
}

export type RunMode = 'snapshot' | 'incremental'

export type RunStatus = 'succeeded' | 'failed' | 'running'

/** One row of GET /api/runs. */
export interface RunListItem {
  run_id: string
  status: RunStatus
  mode: string
  created_at: string
  counts: ReconMeta['counts'] | null
  error: { error?: string; detail?: string } | string | null
}

/** One row of GET /api/customers. */
export interface CustomerInfo {
  key: string
  name: string
  sources: Record<string, string>
}

/** Human-readable side of a gold bank transaction (ledger endpoints). */
export interface LedgerTxnInfo {
  bank_ref: string | null
  amount: number | null
  value_date: string | null
  zone: string | null
  narrative: string
}

export interface LedgerBillInfo {
  gold_bill_id: string
  role: 'picked' | 'candidate'
  bill_number: string | null
  net_payable_amount: number | null
  zone: string | null
  bill_status: string | null
}

export interface LedgerMatch {
  id: string
  run_id: string
  /** per-run engine label (m0, m1, …) — repeats across runs */
  match_id: string
  /** durable per-customer match number, shown as "M-{seq}"; never reused */
  seq: number | null
  confidence: string
  status: 'OPEN' | 'LOCKED' | 'REJECTED'
  locked_by: 'AUTO_HIGH' | 'USER' | null
  created_at: string
  locked_at: string | null
  txn: LedgerTxnInfo | null
  bills: LedgerBillInfo[]
}

export interface LedgerException {
  id: string
  exception_type: 'BANK_ONLY' | 'BILL_ONLY'
  status: 'OPEN' | 'RESOLVED'
  gold_bank_txn_id: string | null
  gold_bill_id: string | null
  first_seen_run_id: string
  resolved_by_run_id: string | null
  txn?: LedgerTxnInfo | null
  bill?: Omit<LedgerBillInfo, 'gold_bill_id' | 'role'> | null
}

export interface LedgerViewData {
  matches: LedgerMatch[]
  exceptions: LedgerException[]
}

/** Gold-layer browse frames served by GET /api/gold/{frame}. */
export type GoldFrameName = 'bank' | 'bills' | 'recoveries' | 'lineage'

export interface IngestFileOutcome {
  field: string
  source_type: string
  original_name: string
  bronze_file_id: number
  outcome: 'registered' | 'deduped'
  size_bytes: number
}

export interface IngestResponse {
  customer: string
  files: IngestFileOutcome[]
  stats: IngestStats
  selfcheck: Selfcheck | null
}

export interface IngestionListItem {
  id: number
  at: string
  stats: IngestStats | null
  selfcheck_passed: boolean | null
  files: Array<{
    bronze_file_id: number
    field?: string
    source_type: string
    outcome: string
    size_bytes?: number
    original_name: string | null
  }>
}

/** One row of GET /api/gold/files — a bronze file owning gold rows. */
export interface GoldFileInfo {
  bronze_file_id: number
  source_type: string
  original_name: string
  uploaded_at: string
  gold_counts: Record<string, number>
  statement: {
    value_date_min: string | null
    value_date_max: string | null
    credits: number
  } | null
}

/** Tunables are deliberately absent: omitting them lets the customer's
 *  saved matching config govern the run (API-side merge); API callers
 *  can still pass explicit per-run overrides. */
export interface ReconcileParams {
  customer_id: string
  statement_bronze_id: number
  mode: RunMode
}

// --- command center overview ------------------------------------------

export interface OverviewException {
  id: string
  exception_type: 'BANK_ONLY' | 'BILL_ONLY'
  ref: string | null
  zone: string | null
  amount: number | null
  date: string
}

export interface Overview {
  gold: { bank_txns: number; credits: number; bills: number
          recoveries: number; lineage_docs: number }
  matches: { OPEN: number; LOCKED: number; REJECTED: number }
  locked_by: { AUTO_HIGH: number; USER: number }
  open_exceptions: { BANK_ONLY: number; BILL_ONLY: number }
  resolved_exceptions: number
  open_value: { bank_only: number; bill_only: number; total: number }
  matched_credits: number
  match_rate: number | null
  top_exceptions: OverviewException[]
  last_run: { run_id: string; mode: string; created_at: string
              counts: ReconMeta['counts'] | null } | null
  last_ingestion: { at: string; original_name: string; source_type: string } | null
}

// --- AR reconciliation -------------------------------------------------

export type ArStatus = 'SETTLED' | 'IN_REVIEW' | 'AWAITING' | 'OVERDUE'

export interface ArRow {
  bill_number: string | null
  zone: string | null
  org_unit: string | null
  bill_status: string | null
  gross_amount: number | null
  net_payable_amount: number | null
  due_date: string | null
  age_days: number | null
  status: ArStatus
  pay: { bank_ref: string | null; amount: number | null; value_date: string | null } | null
  variance: number | null
  match_ledger_id: string | null
  match_seq: number | null
  exception_id: string | null
}

export interface ArView {
  as_of: string
  kpis: {
    outstanding: { count: number; value: number }
    received: { count: number; value: number; mtd_value: number }
    match_rate: number | null
    overdue: { count: number; value: number }
  }
  aging: Array<{ bucket: string; count: number; value: number }>
  rows: ArRow[]
}

// --- audit trail -------------------------------------------------------

export interface AuditEventRow {
  id: number
  event_type: string
  severity: string
  entity_type: string | null
  entity_id: string | null
  /** human-facing name resolved at read time: "M-28", a filename, … */
  entity_label?: string | null
  /** display context joined at read time (ids only, never amounts) */
  context?: Record<string, unknown> | null
  run_id: string | null
  details: Record<string, unknown> | null
  created_at: string
}

// --- per-customer configuration ---------------------------------------

export interface AdapterOption {
  key: string
  label: string
  /** source family the adapter belongs to, e.g. "HSBC", "IREPS" —
   *  the ingest UI groups an ERP's documents by this */
  system: string
  /** file extensions the adapter parses (e.g. [".pdf"]) — drives the
   *  file picker's accept attribute; empty means no restriction */
  file_kinds: string[]
  /** slot role: bank_statement | bill_status | lineage — any lineage-role
   *  adapter can serve any lineage_* slot */
  role?: string
}

/** source_type -> available adapters (GET /api/adapters). */
export type AdapterRegistry = Record<string, AdapterOption[]>

/** Gold field lists feeding the matching-config dropdowns. */
export type GoldSchema = Record<
  'bank_txns' | 'bills',
  { fields: string[]; date_fields: string[]; numeric_fields: string[] }
>

export interface ExactSignal {
  bank_field: string
  bill_field: string
  weight: number
  key?: string | null
}

export interface FieldMap {
  bank_amount_field: string
  bill_amount_field: string
  bank_date_field: string
  bill_date_primary: string
  bill_date_fallback: string | null
  exact_signals: ExactSignal[]
  eligibility_field: string
  fallback_due_statuses: string[]
}

/** Advisory-copy dictionaries keyed by section then frozen code. */
export type CopyText = Record<string, Record<string, string>>

export interface CustomerRules {
  date_tolerance_days: number
  amount_tolerance: number
  window_days: number
  co7_lookback_days: number
  allow_batched: boolean
  max_batch_size: number
  paid_statuses: string[]
  weights: Record<string, number>
  field_map: FieldMap
  /** stored overrides only (sparse); what PUT persists */
  copy_overrides?: CopyText
  /** fully-resolved advisory text (defaults + overrides); read-only echo */
  copy_effective?: CopyText
  batch_amount_slack: number
  amount_decimals: number
  ar_overdue_days: number
}

export interface CustomerConfig {
  key: string
  name: string
  sources: Record<string, string>
  rules: CustomerRules
}
