import type { ColumnDef } from '@tanstack/react-table'
import type { Row } from '../types'
import { AMOUNT_COLS, fmtCell } from '../format'
import { ConfidenceBadge } from './ConfidenceBadge'
import { DataTable } from './DataTable'

/** Ordered, labelled columns; anything else in the payload is appended
 *  hidden so the column menu can still reveal it. */
const CURATED: Array<[string, string]> = [
  // present only when several runs are selected (run filter)
  ['Run', 'Run'],
  ['confidence', 'Confidence'],
  ['amount', 'Amount'],
  ['value_date', 'Value date'],
  ['zone_from_narrative', 'Signal (bank)'],
  ['bill_zone', 'Signal (bill)'],
  ['bill_number', 'Bill no.'],
  ['contract_no', 'Contract'],
  ['payment_advice_date', 'Advice date'],
  ['payment_order_ref', 'Pay order ref'],
  ['payment_order_date', 'Pay order date'],
  ['bill_status', 'Status'],
  ['org_unit', 'Org unit'],
  ['LineageStatus', 'Lineage'],
  ['PO', 'PO'],
  ['Receipt_Doc', 'Receipt doc'],
  ['flag', 'Flag'],
  ['narrative', 'Narrative'],
  ['bank_ref', 'Bank ref'],
]

// mirrors engine.MATCH_SIDE_COLS + TRAIL leftovers
const HIDDEN_BY_DEFAULT = [
  'narrative', 'bank_ref', 'date_gap_days', 'date_source', 'amount_check', 'zone_check',
  'date_check', 'n_candidates', 'tied_candidates', 'customer_ref', 'timestamp', 'page',
  'bill_date', 'contract_date', 'vendor_code', 'submission_date',
  'gross_amount', 'approved_amount', 'deduction_amount', 'recoveries', 'recovery_count',
  'return_reason', 'RNOTE_MatchedVia', 'CRN_MatchedVia', 'PO_Date',
  'Receipt_Date', 'Receipt_Qty', 'DRR_or_Challan', 'Bill_Reg_No', 'bill_indices',
]

function buildColumns(rows: Row[]): ColumnDef<Row>[] {
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  const curatedKeys = CURATED.filter(([k]) => present.has(k))
  const rest = [...present].filter((k) => !CURATED.some(([c]) => c === k)).sort()

  const make = (key: string, label: string): ColumnDef<Row> => ({
    id: key,
    header: label,
    accessorFn: (row) => row[key],
    cell: (ctx) => {
      const v = ctx.row.original[key]
      if (key === 'confidence' && typeof v === 'string') return <ConfidenceBadge label={v} />
      if (key === 'flag' && v) return <span className="flag-note">{String(v)}</span>
      if (key === 'bill_indices' && Array.isArray(v) && v.length > 1)
        return <span className="chip">{v.length} bills</span>
      const text = fmtCell(key, v)
      return text === '—' ? <span className="empty-cell">—</span> : text
    },
  })

  return [...curatedKeys.map(([k, l]) => make(k, l)), ...rest.map((k) => make(k, k))]
}

export function MatchedTable({ rows }: { rows: Row[] }) {
  return (
    <DataTable
      rows={rows}
      columns={buildColumns(rows)}
      numericIds={AMOUNT_COLS}
      initialHidden={HIDDEN_BY_DEFAULT}
    />
  )
}
