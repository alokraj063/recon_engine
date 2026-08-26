import type { Cell, Row } from '../types'
import { fmtCell } from '../format'
import { LineageTimeline } from './LineageTimeline'

/** Labeled sections describing one bill and its document history.
 *  Shared by the exception queue's BILL_ONLY detail and the enriched
 *  bills source tab. */

export const BILL_IDENTITY: Array<[string, string]> = [
  ['bill_number', 'Bill number'],
  ['contract_no', 'Contract no'],
  ['bill_date', 'Bill date'],
  ['zone', 'Zone'],
  ['org_unit', 'Org unit'],
  ['bill_status', 'Status'],
  ['ExpectedBasis', 'Expected basis'],
  ['sheet', 'Export sheet'],
  ['data_row', 'Export row'],
]

export const BILL_MONEY: Array<[string, string]> = [
  ['gross_amount', 'Gross amt'],
  ['approved_amount', 'Approved amt'],
  ['deduction_amount', 'Deducted amt'],
  ['net_payable_amount', 'Net payable'],
  ['recovery_count', 'Recovery lines'],
  ['recoveries', 'Recoveries'],
  ['return_reason', 'Reason for return'],
]

export function DetailField({ row, k, label, valueClass }:
    { row: Row; k: string; label: string; valueClass?: string }) {
  const v = fmtCell(k, (row[k] ?? null) as Cell)
  return (
    <div>
      <div className="dt-label">{label}</div>
      <div className={`dt-value${valueClass ? ` ${valueClass}` : ''}`}>
        {v === '—' ? <span className="empty-cell">—</span> : v}
      </div>
    </div>
  )
}

interface Props {
  row: Row
  /** heading of the first section */
  title?: string
}

export function BillTrailDetail({ row, title = 'Bill' }: Props) {
  return (
    <div className="detail-grid">
      <div className="detail-section">{title}</div>
      {BILL_IDENTITY.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div className="detail-section">Amounts &amp; recoveries</div>
      {BILL_MONEY.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div className="detail-section">Bill lineage</div>
      <div className="timeline-wrap">
        <LineageTimeline row={row} />
      </div>
    </div>
  )
}
