import type { Cell } from './types'

const INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function inr(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return '₹' + INR.format(v)
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

/** One display string for any cell the API can produce. */
export function fmtCell(col: string, v: Cell): string {
  if (v === null || v === undefined || v === '') return '—'
  if (Array.isArray(v)) return `${v.length} bills`
  if (typeof v === 'object') {
    const entries = Object.entries(v)
    if (!entries.length) return '—'
    return entries.map(([k, x]) => `${k}: ${x}`).join(' · ')
  }
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (typeof v === 'number') return AMOUNT_COLS.has(col) ? inr(v) : String(v)
  return String(v)
}
