import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { Candidate, Cell, Row } from '../types'
import { AMOUNT_COLS, fmtCell } from '../format'
import { BillTrailDetail, DetailField } from './BillTrailDetail'
import { ConfidenceBadge } from './ConfidenceBadge'
import { DataTable } from './DataTable'

type Side = 'ALL' | 'BANK_ONLY' | 'BILL_ONLY' | 'MATCH_REVIEW'

/** The shared spine every row shows; side-specific fields live in the
 *  expandable detail so the two-sided queue reads as one list. */
const SPINE: Array<[string, string]> = [
  ['exception_type', 'Type'],
  ['confidence', 'Confidence'],
  ['Amount', 'Amount'],
  ['Value_Date', 'Value date'],
  ['Zone', 'Zone'],
  ['BillNumber', 'Bill no.'],
  ['Status', 'Status'],
  ['ExpectedBasis', 'Basis'],
  ['gap_type', 'Gap'],
  ['action', 'What to do'],
]

const BANK_DETAIL: Array<[string, string]> = [
  ['Bank_Ref', 'Bank ref'],
  ['Bank_Narrative', 'Narrative'],
  ['Zone', 'Zone from narrative'],
  ['gap_type', 'Gap type'],
  ['customer_ref', 'Customer ref'],
  ['page', 'Statement page'],
]

/** Same field order the backend packs into each candidate dict. */
const CANDIDATE_LABELS: Array<[string, string]> = [
  ['BillNumber', 'Bill number'],
  ['ContractNo', 'Contract no'],
  ['Zone', 'Zone'],
  ['Status', 'Status'],
  ['BillAmt', 'Bill amt'],
  ['PassedAmt', 'Passed amt'],
  ['DeductedAmt', 'Deducted amt'],
  ['NetAmt', 'Net amt'],
  ['CO6No', 'CO6 no'],
  ['CO6Date', 'CO6 date'],
  ['CO7No', 'CO7 no'],
  ['CO7Date', 'CO7 date'],
  ['PaymentAdviceDateToBank', 'Advice date to bank'],
  ['AccountingUnit', 'Accounting unit'],
  ['LineageStatus', 'Lineage status'],
  ['PO', 'PO'],
  ['PO_Date', 'PO date'],
  ['Receipt_Doc', 'Receipt note / CRN'],
  ['Receipt_Date', 'Receipt date'],
  ['DRR_or_Challan', 'DRR / challan'],
  ['Bill_Reg_No', 'Bill reg no'],
  ['ReasonForReturn', 'Reason for return'],
  ['Sheet', 'Export sheet'],
  ['DataRow', 'Export row'],
]

const REVIEW_SIGNALS: Array<[string, string]> = [
  ['flag', 'Flag'],
  ['zone_check', 'Zone agreed'],
  ['date_check', 'Date agreed'],
  ['date_gap_days', 'Date gap (days)'],
  ['date_source', 'Date compared against'],
  ['n_candidates', 'Bills sharing this amount'],
  ['tied_candidates', 'Tied at top score'],
]

const REVIEW_BANK: Array<[string, string]> = [
  ['Bank_Ref', 'Bank ref'],
  ['Bank_Narrative', 'Narrative'],
  ['Amount', 'Amount'],
  ['Value_Date', 'Value date'],
  ['Zone', 'Zone from narrative'],
]

function CandidateCard({ cand }: { cand: Candidate }) {
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

function Detail({ row }: { row: Row }) {
  const isBank = row.exception_type === 'BANK_ONLY'
  if (row.exception_type === 'MATCH_REVIEW') {
    const cands = Array.isArray(row.Candidates) ? (row.Candidates as Candidate[]) : []
    return (
      <div className="detail-grid">
        <div className="detail-section">
          Weak match — stands in the Matched tab, confirm or reject{' '}
          {typeof row.confidence === 'string' && <ConfidenceBadge label={row.confidence} />}
        </div>
        {REVIEW_BANK.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l} />
        ))}
        <div className="detail-section">Why it was flagged</div>
        {REVIEW_SIGNALS.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l} />
        ))}
        <div className="detail-section">
          Candidate bills ({cands.length})
        </div>
        <div className="candidate-list">
          {cands.map((c, i) => (
            <CandidateCard key={i} cand={c} />
          ))}
        </div>
      </div>
    )
  }
  if (isBank) {
    return (
      <div className="detail-grid">
        <div className="detail-section">Bank credit — no bill behind it</div>
        {BANK_DETAIL.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l} />
        ))}
      </div>
    )
  }
  return <BillTrailDetail row={row} title="Bill — advised but no credit landed" />
}

function buildColumns(): ColumnDef<Row>[] {
  return SPINE.map(([key, label]) => ({
    id: key,
    header: label,
    accessorFn: (row) => row[key],
    cell: (ctx) => {
      const v = ctx.row.original[key]
      if (key === 'exception_type' && typeof v === 'string')
        return <span className={`stamp stamp-${v}`}>{v.replace(/_/g, ' ')}</span>
      if (key === 'confidence')
        return typeof v === 'string' ? <ConfidenceBadge label={v} /> : <span className="empty-cell">—</span>
      if (key === 'action') return <div className="action-cell">{v ? String(v) : '—'}</div>
      const text = fmtCell(key, v as Cell)
      return text === '—' ? <span className="empty-cell">—</span> : text
    },
  }))
}

export function ExceptionQueue({ rows }: { rows: Row[] }) {
  const [side, setSide] = useState<Side>('ALL')
  const columns = useMemo(buildColumns, [])
  const filtered = side === 'ALL' ? rows : rows.filter((r) => r.exception_type === side)

  const seg = (
    <div className="seg">
      {(['ALL', 'BANK_ONLY', 'BILL_ONLY', 'MATCH_REVIEW'] as Side[]).map((s) => (
        <button key={s} className={side === s ? 'on' : ''} onClick={() => setSide(s)}>
          {s === 'ALL' ? 'All' : s.replace(/_/g, ' ')}
        </button>
      ))}
    </div>
  )

  return (
    <DataTable
      key={side}
      rows={filtered}
      columns={columns}
      numericIds={AMOUNT_COLS}
      toolbar={seg}
      renderDetail={(row) => <Detail row={row} />}
    />
  )
}
