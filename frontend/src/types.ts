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
}

export interface ReconResponse {
  run_id: string
  summary: SummaryRow[]
  matched: Row[]
  exceptions: Row[]
  meta: ReconMeta
}

export interface TunableOptions {
  window_days: number
  co7_lookback_days: number
  date_tolerance_days: number
  amount_tolerance: number
  allow_batched: boolean
  max_batch_size: number
}

export const DEFAULT_OPTIONS: TunableOptions = {
  window_days: 0,
  co7_lookback_days: 5,
  date_tolerance_days: 2,
  amount_tolerance: 0.0,
  allow_batched: true,
  max_batch_size: 3,
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
