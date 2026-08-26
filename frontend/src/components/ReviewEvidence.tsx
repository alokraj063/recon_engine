import type { Candidate, Cell, Row } from '../types'
import { fmtCell } from '../format'
import { DetailField } from './BillTrailDetail'

/**
 * The evidence behind a match decision, shared by the Exception queue's
 * MATCH_REVIEW detail and the Analyst queue's expandable rows. All
 * fragments expect to be rendered INSIDE a `.detail-grid`.
 */

/** Same field order the backend packs into each candidate dict
 *  (mirror of engine.CANDIDATE_FIELDS). */
export const CANDIDATE_LABELS: Array<[string, string]> = [
  ['bill_number', 'Bill number'],
  ['contract_no', 'Contract no'],
  ['zone', 'Zone'],
  ['bill_status', 'Status'],
  ['gross_amount', 'Gross amt'],
  ['approved_amount', 'Approved amt'],
  ['deduction_amount', 'Deducted amt'],
  ['net_payable_amount', 'Net payable'],
  ['submission_ref', 'Submission ref'],
  ['submission_date', 'Submission date'],
  ['payment_order_ref', 'Pay order ref'],
  ['payment_order_date', 'Pay order date'],
  ['payment_advice_date', 'Advice date to bank'],
  ['org_unit', 'Org unit'],
  ['LineageStatus', 'Lineage status'],
  ['PO', 'PO'],
  ['PO_Date', 'PO date'],
  ['Receipt_Doc', 'Receipt note / CRN'],
  ['Receipt_Date', 'Receipt date'],
  ['DRR_or_Challan', 'DRR / challan'],
  ['Bill_Reg_No', 'Bill reg no'],
  ['return_reason', 'Reason for return'],
  ['sheet', 'Export sheet'],
  ['data_row', 'Export row'],
]

export const REVIEW_SIGNALS: Array<[string, string]> = [
  ['flag', 'Flag'],
  ['zone_check', 'Signals agreed'],
  ['date_check', 'Date agreed'],
  ['date_gap_days', 'Date gap (days)'],
  ['date_source', 'Date compared against'],
  ['n_candidates', 'Bills sharing this amount'],
  ['tied_candidates', 'Tied at top score'],
]

export const REVIEW_BANK: Array<[string, string]> = [
  ['bank_ref', 'Bank ref'],
  ['bank_narrative', 'Narrative'],
  ['amount', 'Amount'],
  ['value_date', 'Value date'],
  ['zone', 'Zone from narrative'],
]

// matched-frame rows use the un-renamed bank columns
const MATCHED_BANK: Array<[string, string]> = [
  ['bank_ref', 'Bank ref'],
  ['narrative', 'Narrative'],
  ['amount', 'Amount'],
  ['value_date', 'Value date'],
  ['zone_from_narrative', 'Signal (bank)'],
]

const MATCHED_BILL: Array<[string, string]> = [
  ['bill_number', 'Bill number'],
  ['contract_no', 'Contract no'],
  ['bill_zone', 'Signal (bill)'],
  ['bill_status', 'Status'],
  ['payment_advice_date', 'Advice date'],
  ['payment_order_ref', 'Pay order ref'],
  ['payment_order_date', 'Pay order date'],
  ['org_unit', 'Org unit'],
  ['LineageStatus', 'Lineage'],
  ['return_reason', 'Reason for return'],
]

// the arithmetic that explains why the credit matched: gross minus
// deductions/recoveries = net payable = the credit amount
const MATCHED_AMOUNTS: Array<[string, string]> = [
  ['gross_amount', 'Gross amt'],
  ['approved_amount', 'Approved amt'],
  ['deduction_amount', 'Deducted amt'],
  ['recoveries', 'Recoveries'],
  ['recovery_count', 'Recovery lines'],
]

export function CandidateCard({ cand }: { cand: Candidate }) {
  return (
    <div className={`candidate-card${cand.Picked ? ' picked' : ''}`}>
      <div className="candidate-head">
        {cand.Picked ? <span className="chip chip-picked">PICKED</span> : <span className="chip">candidate</span>}
      </div>
      <div className="detail-grid">
        {CANDIDATE_LABELS.map(([k, l]) => {
          const v = fmtCell(k, (cand[k] ?? null) as Cell)
          return (
            <div key={k}>
              <div className="dt-label">{l}</div>
              <div className="dt-value">{v === '—' ? <span className="empty-cell">—</span> : v}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** Review-queue row evidence: bank side, why-flagged signals, candidate
 *  cards. Render inside a `.detail-grid`. */
export function ReviewEvidence({ row }: { row: Row }) {
  const cands = Array.isArray(row.Candidates) ? (row.Candidates as Candidate[]) : []
  return (
    <>
      {REVIEW_BANK.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div className="detail-section">Why it was flagged</div>
      {REVIEW_SIGNALS.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div className="detail-section">Candidate bills ({cands.length})</div>
      <div className="candidate-list">
        {cands.map((c, i) => (
          <CandidateCard key={i} cand={c} />
        ))}
      </div>
    </>
  )
}

/** Matched-frame row evidence (HIGH auto-locked matches never enter the
 *  review queue, so no candidate cards exist — show the pairing and the
 *  signal outcome instead). Render inside a `.detail-grid`. */
export function MatchedEvidence({ row }: { row: Row }) {
  // matched rows don't carry net_payable_amount as a column: the bank
  // `amount` IS the bill's net payable — that equality is the match key
  const netFallback = row.net_payable_amount === undefined
    || row.net_payable_amount === null
  const net = netFallback ? row.amount : row.net_payable_amount
  return (
    <>
      <div className="detail-section">Bank credit</div>
      {MATCHED_BANK.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div className="detail-section">Matched bill</div>
      {MATCHED_BILL.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div className="detail-section">Amounts — gross − deductions = net payable = credit</div>
      {MATCHED_AMOUNTS.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
      <div>
        <div className="dt-label">Net payable</div>
        <div className="dt-value">
          {fmtCell('net_payable_amount', (net ?? null) as Cell)}
          {netFallback && net !== null && net !== undefined && (
            <span className="chip-note"> = credit (the match key)</span>
          )}
        </div>
      </div>
      <div className="detail-section">Signals</div>
      {REVIEW_SIGNALS.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
    </>
  )
}
