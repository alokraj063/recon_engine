import type { Candidate, Cell, Row } from '../types'
import { fmtCell, fmtDay, inr } from '../format'
import { BillLineage } from './BillLineage'
import { DetailField } from './BillTrailDetail'
import { CandidateCompare, type CompareField } from './CandidateCompare'

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
  ['operating_unit', 'Operating unit'],
  ['data_row', 'Export row'],
  // the matcher's tie-break evidence, per candidate (engine._candidate_details)
  ['DateGapDays', 'Gap to credit (days)'],
  ['DateSource', 'Date compared'],
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

/** CANDIDATE_LABELS as comparison fields: amount and zone are compared
 *  with the credit, the date gap marks the closest bill. */
const CANDIDATE_FIELDS: CompareField[] = CANDIDATE_LABELS
  // the bill number is each column's title; the export row is an IREPS
  // file position that tells an analyst nothing about which bill is paid
  .filter(([key]) => key !== 'bill_number' && key !== 'data_row')
  .map(([key, label]) => ({
    key, label,
    refKey: key === 'net_payable_amount' ? 'amount' : key === 'zone' ? 'zone' : undefined,
    best: key === 'DateGapDays' ? 'min' as const : undefined,
  }))

const truthy = (v: Cell) => v === true || v === '✓'

/** The credit under review: amount · ref · value date · zone on one line,
 *  the full narrative on the line below it. */
function CreditStrip({ row }: { row: Row }) {
  const narrative = typeof row.bank_narrative === 'string' ? row.bank_narrative : ''
  return (
    <div className="rv-credit">
      <span className="side-tag tag-bank">CREDIT</span>
      <span className="rv-credit-amt">{inr(row.amount as number | null)}</span>
      <span className="rv-credit-meta">{String(row.bank_ref ?? '—')}</span>
      <span className="rv-credit-meta">{fmtDay(typeof row.value_date === 'string' ? row.value_date : null)}</span>
      {row.zone ? <span className="rv-credit-meta">{String(row.zone)}</span> : null}
      {narrative && <span className="rv-credit-narr">{narrative}</span>}
    </div>
  )
}

/** How the two sides compare, value against value: bank amount ⇄ bill
 *  net payable (the match key), bank zone ⇄ bill zone, statement value
 *  date ⇄ the bill date the matcher compared against. */
function MatchPairs({ bank, bill }: {
  bank: { amount: Cell; zone: Cell; value_date: Cell;
          zone_check: Cell; date_check: Cell; date_gap_days: Cell; date_source: Cell }
  bill: { net_payable_amount: Cell; zone: Cell;
          payment_advice_date: Cell; payment_order_date: Cell } | null
}) {
  if (!bill) return null
  const pill = (side: 'bank' | 'bill', v: Cell, col: string) => (
    <span className={`pair-pill pill-${side}`}>
      {typeof v === 'number' ? inr(v) : fmtCell(col, v)}
    </span>
  )
  const verdict = (ok: boolean, note?: string) => (
    <span className={`pair-verdict ${ok ? 'ok' : 'bad'}`}>
      {ok ? '✓' : '✗'}{note ? <span className="pair-note"> {note}</span> : null}
    </span>
  )
  const usesCo7 = bank.date_source === 'co7'
  const billDate = usesCo7 ? bill.payment_order_date : bill.payment_advice_date
  const dateOk = bank.date_check === true || bank.date_check === '✓'
  const zoneOk = bank.zone_check === true || bank.zone_check === '✓'
  const gap = bank.date_gap_days
  return (
    <div className="pair-rows">
      <span className="pair-label">Amount</span>
      {pill('bank', bank.amount, 'amount')}
      {verdict(true)}
      {pill('bill', bill.net_payable_amount, 'net_payable_amount')}
      <span className="pair-note">match key: amounts must agree</span>

      <span className="pair-label">Signal</span>
      {pill('bank', bank.zone, 'zone')}
      {verdict(zoneOk)}
      {pill('bill', bill.zone, 'zone')}
      <span className="pair-note">{zoneOk ? 'signals agree' : 'signal mismatch'}</span>

      <span className="pair-label">Date</span>
      {pill('bank', bank.value_date, 'value_date')}
      {verdict(dateOk, dateOk ? undefined : `${fmtCell('date_gap_days', gap)}d gap`)}
      {pill('bill', billDate, 'payment_advice_date')}
      <span className="pair-note">
        vs {usesCo7 ? 'pay order date' : 'advice date'}
        {dateOk ? ' — within tolerance' : ' — over tolerance'}
      </span>
    </div>
  )
}

/** Review-queue row evidence: bank side, why-flagged signals, candidate
 *  cards. Render inside a `.detail-grid`. `runId` (the creating run)
 *  lets each candidate card pull its full lineage timeline. */
export function ReviewEvidence({ row, runId, onAcceptBill, busy }: {
  row: Row; runId?: string | null
  /** present only where a decision can be made (an OPEN match): renders
   *  an accept button (and "+ note") on each candidate column */
  onAcceptBill?: (billNumber: Cell, note?: string) => void
  busy?: boolean
}) {
  const cands = Array.isArray(row.Candidates) ? (row.Candidates as Candidate[]) : []
  const picked = cands.find((c) => c.Picked) ?? cands[0] ?? null
  const flag = typeof row.flag === 'string' ? row.flag : ''
  const ambiguous = flag.startsWith('AMBIGUOUS')
  // An AMBIGUOUS pick is a recommendation only when the bill dates actually
  // separated the bills. An undated tie ("arbitrarily") or a tie at the SAME
  // gap (0d; others 0d, 0d) was decided by row order — no bill may look
  // endorsed, and the reason must say the data cannot tell them apart.
  const gaps = new Set(cands.map((c) => String(c.DateGapDays ?? '')))
  const tied = ambiguous && (flag.includes('arbitrarily') || gaps.size <= 1)
  const n = cands.length
  const reason = ambiguous
    ? tied
      ? `${n} bills share this amount and score the same. Nothing in the data tells them apart — confirm which bill this credit pays.`
      : `${n} bills share this amount. Bill ${String(picked?.bill_number ?? '—')} has the date closest to the credit.`
    : flag.replace(/^[A-Z_]+\s*[-—:]\s*/, '')
  const chip = (ok: boolean, text: string) => (
    <span className={`rv-chip ${ok ? 'ok' : 'bad'}`}>{ok ? '✓' : '✗'} {text}</span>
  )
  const gap = row.date_gap_days
  return (
    <div className="rv">
      <CreditStrip row={row} />
      <div className="rv-reason">
        <p className="rv-reason-text">{reason}</p>
        <div className="rv-chips">
          {chip(true, 'Amount')}
          {chip(truthy(row.zone_check as Cell), 'Zone')}
          {chip(truthy(row.date_check as Cell),
                `Date${gap !== null && gap !== undefined && gap !== '' ? ` · ${String(gap)}d gap` : ''}`)}
        </div>
      </div>
      {/* one credit vs one bill: which signal failed. An ambiguous match
          agrees on every signal — the comparison below is the evidence */}
      {!ambiguous && picked && (
        <MatchPairs
          bank={{ amount: row.amount as Cell, zone: row.zone as Cell,
                  value_date: row.value_date as Cell,
                  zone_check: row.zone_check as Cell, date_check: row.date_check as Cell,
                  date_gap_days: row.date_gap_days as Cell,
                  date_source: row.date_source as Cell }}
          bill={{
            net_payable_amount: picked.net_payable_amount as Cell,
            zone: picked.zone as Cell,
            payment_advice_date: picked.payment_advice_date as Cell,
            payment_order_date: picked.payment_order_date as Cell,
          }}
        />
      )}
      <div className="detail-section">
        Candidate bills ({n})
        {onAcceptBill && ' — select one to accept'}
      </div>
      <CandidateCompare
        busy={busy}
        fields={CANDIDATE_FIELDS}
        credit={{ amount: row.amount as Cell, zone: row.zone as Cell }}
        columns={cands.map((c, i) => ({
          key: String(i),
          title: String(c.bill_number ?? `Bill ${i + 1}`),
          picked: !!c.Picked && !tied,
          pickNote: ambiguous ? 'closest date' : undefined,
          values: c,
          onAccept: onAcceptBill
            ? (note?: string) => onAcceptBill(c.bill_number as Cell, note)
            : undefined,
          footer: <BillLineage runId={runId} billNumber={c.bill_number}
                               fallbackRow={c as Row} />,
        }))} />
    </div>
  )
}

/** Matched-frame row evidence (HIGH auto-locked matches never enter the
 *  review queue, so no candidate cards exist — show the pairing and the
 *  signal outcome instead). Render inside a `.detail-grid`. */
export function MatchedEvidence({ row, runId }: { row: Row; runId?: string | null }) {
  // matched rows don't carry net_payable_amount as a column: the bank
  // `amount` IS the bill's net payable — that equality is the match key
  const netFallback = row.net_payable_amount === undefined
    || row.net_payable_amount === null
  const net = netFallback ? row.amount : row.net_payable_amount
  return (
    <>
      <div className="detail-section">Bank credit</div>
      <div className="side-panel side-bank">
        <span className="side-tag tag-bank">BANK STATEMENT</span>
        {MATCHED_BANK.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l}
                       valueClass={k === 'amount' ? 'match-key' : undefined} />
        ))}
      </div>
      <div className="detail-section">Matched bill</div>
      <div className="side-panel side-bill">
        <span className="side-tag tag-bill">IREPS BILL</span>
        {MATCHED_BILL.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l} />
        ))}
        <BillLineage runId={runId} billNumber={row.bill_number} fallbackRow={row} />
        <div className="detail-section">Amounts — gross − deductions = net payable = credit</div>
        {MATCHED_AMOUNTS.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l} />
        ))}
        <div>
          <div className="dt-label">Net payable</div>
          <div className="dt-value match-key">
            {fmtCell('net_payable_amount', (net ?? null) as Cell)}
            {netFallback && net !== null && net !== undefined && (
              <span className="chip-note"> = credit (match key)</span>
            )}
          </div>
        </div>
      </div>
      <div className="detail-section">Signals</div>
      <MatchPairs
        bank={{ amount: row.amount as Cell,
                zone: row.zone_from_narrative as Cell,
                value_date: row.value_date as Cell,
                zone_check: row.zone_check as Cell, date_check: row.date_check as Cell,
                date_gap_days: row.date_gap_days as Cell,
                date_source: row.date_source as Cell }}
        bill={{ net_payable_amount: (net ?? null) as Cell,
                zone: row.bill_zone as Cell,
                payment_advice_date: row.payment_advice_date as Cell,
                payment_order_date: row.payment_order_date as Cell }}
      />
      {REVIEW_SIGNALS.map(([k, l]) => (
        <DetailField key={k} row={row} k={k} label={l} />
      ))}
    </>
  )
}
