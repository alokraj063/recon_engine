import type { ReconMeta, ReconResponse, Row, SummaryRow } from './types'

/** One selected run, payload + a short human label ("24 Aug, 06:21 pm · snapshot"). */
export interface SelectedRun {
  runId: string
  label: string
  payload: ReconResponse
}

/**
 * Stable entity identity for an exception row, so the same open
 * exception re-reported by several incremental runs collapses to one
 * row. Mirrors the backend identities: bank txns dedupe on
 * (bank_ref, value_date, amount), bills upsert on
 * (bill_number, submission_ref). Returns null = never dedupe.
 */
export function exceptionKey(row: Row): string | null {
  // pre-canonicalization runs persisted the old column names — fall back
  // so mixed old/new selections still dedupe correctly
  const cell = (a: string, b: string) => row[a] ?? row[b] ?? null
  if (row.exception_type === 'BANK_ONLY') {
    const ref = cell('bank_ref', 'Bank_Ref')
    const date = cell('value_date', 'Value_Date')
    const amt = cell('amount', 'Amount')
    if (ref === null && date === null && amt === null) return null
    return `B|${ref}|${date}|${amt}`
  }
  if (row.exception_type === 'BILL_ONLY') {
    const no = cell('bill_number', 'BillNumber')
    const sub = cell('submission_ref', 'CO6No')
    if (no === null && sub === null) return null
    return `L|${no}|${sub}`
  }
  // review rows appear only in their creating run; a durable ledger id,
  // when present, still guards against re-reports
  if (row.exception_type === 'MATCH_REVIEW' && typeof row.match_ledger_id === 'string')
    return `M|${row.match_ledger_id}`
  return null
}

/**
 * Rows of `key` across the selected runs. Single selection returns the
 * run's rows untouched (byte-identical to the single-run view); multi
 * selection stacks them with a leading `Run` column naming the origin.
 * Exceptions are de-duplicated across runs: runs are newest-first, so
 * keeping the FIRST occurrence per entity keeps its LATEST state.
 */
export function combinedRows(runs: SelectedRun[], key: 'matched' | 'exceptions'): Row[] {
  if (runs.length === 1) return runs[0].payload[key]
  const seen = new Set<string>()
  return runs.flatMap((r) =>
    r.payload[key].flatMap((row) => {
      if (key === 'exceptions') {
        const k = exceptionKey(row)
        if (k !== null) {
          if (seen.has(k)) return []
          seen.add(k)
        }
      }
      return [{ Run: r.label, run_id: r.runId, ...row }]
    }),
  )
}

/** Sidebar/tile counts that agree with the combined tables: matched and
 *  exception counts from the (deduped) rows, the rest summed from metas. */
export function countsFromRows(
  matched: Row[], exceptions: Row[], metas: ReconMeta[],
): ReconMeta['counts'] {
  const byType = (t: string) =>
    exceptions.filter((r) => r.exception_type === t).length
  return {
    ...sumCounts(metas),
    matched: matched.length,
    bank_only: byType('BANK_ONLY'),
    bill_only: byType('BILL_ONLY'),
    match_review: byType('MATCH_REVIEW'),
  }
}

/** Tile amounts from the combined (deduped) rows; every queue row carries
 *  `amount` (bill rows are assigned amount = net_payable_amount). */
export function amountsFromRows(matched: Row[], exceptions: Row[]) {
  const sum = (rows: Row[]) =>
    rows.reduce((a, r) => a + (typeof r.amount === 'number' ? r.amount : 0), 0)
  return {
    matched: sum(matched),
    bank_only: sum(exceptions.filter((r) => r.exception_type === 'BANK_ONLY')),
    bill_only: sum(exceptions.filter((r) => r.exception_type === 'BILL_ONLY')),
  }
}

/** Summed meta.counts (Sidebar badges + aggregate tiles). */
export function sumCounts(metas: ReconMeta[]): ReconMeta['counts'] {
  const total: Record<string, number> = {}
  for (const m of metas) {
    for (const [k, v] of Object.entries(m.counts)) {
      if (typeof v === 'number') total[k] = (total[k] ?? 0) + v
    }
  }
  return total as unknown as ReconMeta['counts']
}

/** Amount of one top-level summary category in a single run's summary. */
export function findAmount(summary: SummaryRow[], category: string): number | null {
  const row = summary.find((r) => !r.indent && r.Category === category)
  return row?.Amount ?? null
}

/** Per-category amount summed across runs; null only if every run lacks
 *  the category. */
export function sumAmount(summaries: SummaryRow[][], category: string): number | null {
  let sum = 0
  let seen = false
  for (const s of summaries) {
    const a = findAmount(s, category)
    if (a !== null) {
      sum += a
      seen = true
    }
  }
  return seen ? sum : null
}
