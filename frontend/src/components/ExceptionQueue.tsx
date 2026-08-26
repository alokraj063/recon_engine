import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { type Cell, type Row } from '../types'
import { AMOUNT_COLS, fmtCell } from '../format'
import { ReviewEvidence } from './ReviewEvidence'
import { BillTrailDetail, DetailField } from './BillTrailDetail'
import { ConfidenceBadge } from './ConfidenceBadge'
import { DataTable } from './DataTable'

type Side = 'ALL' | 'BANK_ONLY' | 'BILL_ONLY' | 'MATCH_REVIEW'

/** The shared spine every row shows; side-specific fields live in the
 *  expandable detail so the two-sided queue reads as one list. */
const SPINE: Array<[string, string]> = [
  // present only when several runs are selected (run filter)
  ['Run', 'Run'],
  ['exception_type', 'Type'],
  ['confidence', 'Confidence'],
  ['amount', 'Amount'],
  ['value_date', 'Value date'],
  ['zone', 'Zone'],
  ['bill_number', 'Bill no.'],
  ['bill_status', 'Status'],
  ['ExpectedBasis', 'Basis'],
  ['gap_type', 'Gap'],
  ['action', 'What to do'],
]

const BANK_DETAIL: Array<[string, string]> = [
  ['bank_ref', 'Bank ref'],
  ['bank_narrative', 'Narrative'],
  ['zone', 'Zone from narrative'],
  ['gap_type', 'Gap type'],
  ['customer_ref', 'Customer ref'],
  ['page', 'Statement page'],
]

function Detail({ row, onOpenInQueue, primaryRunId }: {
  row: Row
  onOpenInQueue?: (matchLedgerId: string | null) => void
  primaryRunId?: string | null
}) {
  const isBank = row.exception_type === 'BANK_ONLY'
  // combined multi-run rows carry their creating run; single-run rows
  // belong to the primary selection
  const runId = (typeof row.run_id === 'string' ? row.run_id : null) ?? primaryRunId
  if (row.exception_type === 'MATCH_REVIEW') {
    return (
      <div className="detail-grid">
        <div className="detail-section">
          Weak match — stands in the Matched tab until decided{' '}
          {typeof row.confidence === 'string' && <ConfidenceBadge label={row.confidence} />}
          {typeof row.match_ledger_id === 'string' && onOpenInQueue ? (
            <button className="btn-open decide-link"
                    onClick={() => onOpenInQueue(row.match_ledger_id as string)}>
              Decide in Analyst queue →
            </button>
          ) : (
            <span className="chip-note">
              {' '}snapshot run — no durable match to decide (run incremental to feed the queue)
            </span>
          )}
        </div>
        <ReviewEvidence row={row} runId={runId} />
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

function buildColumns(rows: Row[]): ColumnDef<Row>[] {
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  // spine keys always render (bank rows lack bill fields by design) —
  // except Run, which exists only under a multi-run selection
  return SPINE.filter(([key]) => key !== 'Run' || present.has(key))
    .map(([key, label]) => ({
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

export function ExceptionQueue({ rows, onOpenInQueue, primaryRunId }: {
  rows: Row[]
  onOpenInQueue?: (matchLedgerId: string | null) => void
  primaryRunId?: string | null
}) {
  const [side, setSide] = useState<Side>('ALL')
  const columns = useMemo(() => buildColumns(rows), [rows])
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
      renderDetail={(row) => (
        <Detail row={row} onOpenInQueue={onOpenInQueue} primaryRunId={primaryRunId} />
      )}
    />
  )
}
