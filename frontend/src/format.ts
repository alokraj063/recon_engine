import type { Cell } from './types'

const INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function inr(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return '₹' + INR.format(v)
}

/** Headline rupees in Indian units (₹525.19 Cr, ₹96.15 L, ₹48,200) —
 *  for KPI figures where the exact inr() string would not fit; callers
 *  put the exact value in a title so nothing is lost. */
export function inrCompact(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const a = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`
  return `${sign}₹${Math.round(a).toLocaleString('en-IN')}`
}

/** Backend timestamps are naive UTC (no offset in the ISO string);
 *  append Z so the browser doesn't parse them as local time. */
export function parseUtc(iso: string): Date {
  return new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
}

const WHEN = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
})

export function fmtWhen(iso: string): string {
  return WHEN.format(parseUtc(iso))
}

/** Local calendar date (yyyy-mm-dd) of a naive-UTC timestamp — the same
 *  day fmtWhen displays, so date-range filters match what users see. */
export function localDay(iso: string): string {
  const d = parseUtc(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** True when a yyyy-mm-dd day falls inside an optional [from, to] range
 *  (either bound may be '' = unbounded). */
export function inDayRange(day: string, from: string, to: string): boolean {
  if (from && day < from) return false
  if (to && day > to) return false
  return true
}

/** Column names holding money, formatted with Indian grouping.
 *  The PascalCase entries are the pre-canonicalization vocabulary — kept
 *  so runs persisted before the gold-schema rename still show ₹. */
export const AMOUNT_COLS = new Set([
  'amount', 'gross_amount', 'approved_amount', 'deduction_amount',
  'net_payable_amount', 'recovery_sum', 'recovery_amt',
  'Amount', 'BillAmt', 'PassedAmt', 'DeductedAmt', 'NetAmt',
  'RecoverySum', 'RecoveryAmt',
])

export const DATE_HINT = /(date|Date)/

export function isNumericCol(col: string): boolean {
  return AMOUNT_COLS.has(col) || /(_days|Count|Qty|amount)/i.test(col)
}

/** Reference strings persisted before the parser normalized them carry a
 *  float artifact ("1120425700382.0"); strip it for display only. */
export function stripFloatArtifact(s: string): string {
  return /^\d+\.0$/.test(s) ? s.slice(0, -2) : s
}

/** One display string for any cell the API can produce. */
export function fmtCell(col: string, v: Cell): string {
  if (v === null || v === undefined || v === '') return '—'
  if (Array.isArray(v)) return `${v.length} bill${v.length === 1 ? '' : 's'}`
  if (typeof v === 'object') {
    const entries = Object.entries(v)
    if (!entries.length) return '—'
    return entries.map(([k, x]) => `${k}: ${x}`).join(' · ')
  }
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (typeof v === 'number') return AMOUNT_COLS.has(col) ? inr(v) : String(v)
  if (col === 'source' && v === 'NON_IREPS') return 'Non-IREPS'
  if (col === 'source' && v === 'DEBIT') return 'Debit'
  return stripFloatArtifact(String(v))
}

/** A whole count with Indian grouping (1,23,456). */
export const n = (v: number) => v.toLocaleString('en-IN')

/** A 0..1 ratio as "96.9%" — '—' when there is nothing to divide. */
export const pct = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`

export const plural = (v: number, one: string, many: string) => (v === 1 ? one : many)

const DAY = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
/** yyyy-mm-dd (or an ISO timestamp's date part) -> "21 Aug 2026",
 *  parsed as a calendar day, no timezone shift. */
export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return y && m && d ? DAY.format(new Date(y, m - 1, d)) : iso
}

/** Whole days from a yyyy-mm-dd date to another (the data's own "today"). */
export function ageDays(date: string, asOf: string): number | null {
  const a = Date.parse(date.slice(0, 10)), b = Date.parse(asOf.slice(0, 10))
  if (Number.isNaN(a) || Number.isNaN(b)) return null
  return Math.max(0, Math.round((b - a) / 86_400_000))
}
