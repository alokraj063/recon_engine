import { useCallback, useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import {
  type Cell, type LedgerException, type LedgerMatch, type LedgerViewData, type Row,
} from '../types'
import { AMOUNT_COLS, fmtCell } from '../format'
import { ReviewEvidence } from './ReviewEvidence'
import { BillTrailDetail, DetailField } from './BillTrailDetail'
import { ConfidenceBadge } from './ConfidenceBadge'
import { DataTable } from './DataTable'
import {
  MatchDecision, PickList, billByNumber, ledgerStatusLabel, useMatchDecision, type Decide,
} from './MatchDecision'
import { ManualMatchPicker } from './ManualMatchPicker'

type Side = 'ALL' | 'BANK_ONLY' | 'BILL_ONLY' | 'MATCH_REVIEW'

/** The shared spine every row shows; side-specific fields live in the
 *  expandable detail so the two-sided queue reads as one list. */
const SPINE: Array<[string, string]> = [
  // present only when several runs are selected (run filter)
  ['Run', 'Run'],
  ['exception_type', 'Type'],
  ['confidence', 'Confidence'],
  // live match_ledger state (overlay), present only when a row has a
  // durable match to decide
  ['ledger_status', 'Ledger status'],
  ['amount', 'Amount'],
  ['value_date', 'Value date'],
  ['zone', 'Zone'],
  ['bill_number', 'Bill no.'],
  ['bill_status', 'Status'],
  ['ExpectedBasis', 'Basis'],
  ['gap_type', 'Gap'],
]

const BANK_DETAIL: Array<[string, string]> = [
  ['bank_ref', 'Bank ref'],
  ['bank_narrative', 'Narrative'],
  ['zone', 'Zone from narrative'],
  ['gap_type', 'Gap type'],
  ['customer_ref', 'Customer ref'],
  ['page', 'Statement page'],
]

/** The live ledger row behind a frozen MATCH_REVIEW row, if the overlay
 *  has it: the frame's confidence/Candidates are as-of-run, but status,
 *  locked_by and the bill list must come from match_ledger NOW. */
function liveMatch(row: Row, ledger: LedgerViewData | null | undefined): LedgerMatch | null {
  if (typeof row.match_ledger_id !== 'string' || !ledger) return null
  return ledger.matches.find((m) => m.id === row.match_ledger_id) ?? null
}

/** The live exception_ledger row behind a frozen BANK_ONLY / BILL_ONLY
 *  row. Run frames carry no ledger ids, so the join is by identity: the
 *  credit's bank_ref, or the bill's (bill_number, submission_ref). Where
 *  several ledger rows share a key (a resubmitted bill), the OPEN one
 *  wins, else the latest resolved. */
function liveException(row: Row, ledger: LedgerViewData | null | undefined): LedgerException | null {
  if (!ledger) return null
  const want = row.exception_type === 'BANK_ONLY' ? 'BANK_ONLY'
    : row.exception_type === 'BILL_ONLY' ? 'BILL_ONLY' : null
  if (!want) return null
  const hits = ledger.exceptions.filter((e) => {
    if (e.exception_type !== want) return false
    if (want === 'BANK_ONLY') {
      return !!e.txn && row.bank_ref != null && String(e.txn.bank_ref) === String(row.bank_ref)
    }
    if (!e.bill || row.bill_number == null) return false
    if (String(e.bill.bill_number) !== String(row.bill_number)) return false
    return e.bill.submission_ref == null || row.submission_ref == null
      || String(e.bill.submission_ref) === String(row.submission_ref)
  })
  if (hits.length === 0) return null
  return hits.find((e) => e.status === 'OPEN')
    ?? hits.slice().sort((a, b) => (b.resolved_at ?? '').localeCompare(a.resolved_at ?? ''))[0]
}

/** Human label for a live exception's state. */
function exceptionStatusLabel(e: LedgerException): string {
  if (e.status !== 'RESOLVED') return 'OPEN'
  const m = e.resolved_by_match_seq != null ? ` M-${e.resolved_by_match_seq}` : ''
  switch (e.resolved_by) {
    case 'USER_ACCEPT': return `RESOLVED · accepted${m}`
    case 'USER_MANUAL': return `RESOLVED · by user${m}`
    case 'USER_REOPEN': return `RESOLVED · reopened${m}`
    default: return 'RESOLVED · by run'
  }
}

/** "Match to bill… / Match to credit…" for a frozen BANK_ONLY / BILL_ONLY
 *  row whose live ledger exception is OPEN; a snapshot-run row (nothing
 *  in the ledger) gets the disabled button and the existing hint. */
function ManualMatchAction({ row, ledger, customerId, onLedgerChanged }: {
  row: Row
  ledger?: LedgerViewData | null
  customerId?: string
  onLedgerChanged?: () => void
}) {
  const [open, setOpen] = useState(false)
  const live = liveException(row, ledger)
  const isBank = row.exception_type === 'BANK_ONLY'
  const label = isBank ? 'Match to bill…' : 'Match to credit…'
  if (!live || live.status !== 'OPEN' || !customerId) {
    return (
      <div className="detail-section">
        <button className="btn-open" disabled title={live
          ? 'already resolved in the ledger'
          : 'snapshot run — no ledger row to match (run incremental to feed the queue)'}>
          {label}
        </button>
        {!live && (
          <span className="chip-note">
            {' '}snapshot run — no durable exception to match (run incremental to feed the queue)
          </span>
        )}
      </div>
    )
  }
  return (
    <div className="detail-section">
      {!open ? (
        <button className="btn-open" onClick={() => setOpen(true)}>{label}</button>
      ) : (
        <ManualMatchPicker
          customerId={customerId}
          anchor={live}
          candidates={(ledger?.exceptions ?? []).filter((x) => x.status === 'OPEN'
            && x.exception_type === (isBank ? 'BILL_ONLY' : 'BANK_ONLY'))}
          onDone={() => { setOpen(false); onLedgerChanged?.() }}
          onCancel={() => setOpen(false)} />
      )}
    </div>
  )
}

function Detail({ row, onOpenInQueue, primaryRunId, ledger, busy, decide, customerId, onLedgerChanged }: {
  row: Row
  onOpenInQueue?: (matchLedgerId: string | null) => void
  primaryRunId?: string | null
  ledger?: LedgerViewData | null
  busy: boolean
  decide: Decide
  customerId?: string
  onLedgerChanged?: () => void
}) {
  const isBank = row.exception_type === 'BANK_ONLY'
  // combined multi-run rows carry their creating run; single-run rows
  // belong to the primary selection
  const runId = (typeof row.run_id === 'string' ? row.run_id : null) ?? primaryRunId
  // "What to do" lives here rather than as a table column: the prose is too
  // long for the row spine and was collapsing into ~300px-tall cells
  const advice = typeof row.action === 'string' && row.action
    ? <div className="detail-advice">{row.action}</div> : null
  if (row.exception_type === 'MATCH_REVIEW') {
    const hasLedgerId = typeof row.match_ledger_id === 'string'
    const live = liveMatch(row, ledger)
    return (
      <div className="detail-grid">
        {advice}
        <div className="detail-section">
          Weak match — stands in the Matched tab until decided{' '}
          {typeof row.confidence === 'string' && <ConfidenceBadge label={row.confidence} />}
          {!hasLedgerId && (
            <span className="chip-note">
              {' '}snapshot run — no durable match to decide (run incremental to feed the queue)
            </span>
          )}
          {hasLedgerId && !live && (
            <span className="chip-note">{' '}loading ledger state…</span>
          )}
          {live && (
            <span className="live-decision">
              <span className={`stamp stamp-${live.status}`}>{ledgerStatusLabel(live)}</span>
              <MatchDecision match={live} busy={busy} decide={decide} />
              {onOpenInQueue && (
                <button className="btn-open decide-link"
                        onClick={() => onOpenInQueue(live.id)}>
                  Open in Analyst queue →
                </button>
              )}
            </span>
          )}
        </div>
        {live && <PickList match={live} busy={busy} decide={decide} />}
        <ReviewEvidence row={row} runId={runId} busy={busy}
                        onAcceptBill={live && live.status === 'OPEN'
                          ? (no) => {
                              const b = billByNumber(live, no)
                              if (b) decide(live.id, 'accept', b.gold_bill_id)
                            }
                          : undefined} />
      </div>
    )
  }
  if (isBank) {
    return (
      <div className="detail-grid">
        {advice}
        <div className="detail-section">Bank credit — no bill behind it</div>
        {BANK_DETAIL.map(([k, l]) => (
          <DetailField key={k} row={row} k={k} label={l} />
        ))}
        <ManualMatchAction row={row} ledger={ledger} customerId={customerId}
                           onLedgerChanged={onLedgerChanged} />
      </div>
    )
  }
  return (
    <>
      {advice && <div className="detail-grid">{advice}</div>}
      <BillTrailDetail row={row} title="Bill — advised but no credit landed" />
      <div className="detail-grid">
        <ManualMatchAction row={row} ledger={ledger} customerId={customerId}
                           onLedgerChanged={onLedgerChanged} />
      </div>
    </>
  )
}

function buildColumns(rows: Row[], ledger: LedgerViewData | null | undefined): ColumnDef<Row>[] {
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  // review rows carry their ledger id; BANK/BILL_ONLY rows join by
  // identity, so the column shows whenever the overlay resolved anything
  const anyLedgerId = rows.some((r) => typeof r.match_ledger_id === 'string'
                                       || liveException(r, ledger) !== null)
  // spine keys always render (bank rows lack bill fields by design) —
  // except Run, which exists only under a multi-run selection, and
  // Ledger status, which needs at least one durable match to show
  return SPINE.filter(([key]) => (key !== 'Run' || present.has(key))
                                  && (key !== 'ledger_status' || anyLedgerId))
    .map(([key, label]) => ({
    id: key,
    header: label,
    accessorFn: (row) => {
      if (key === 'ledger_status') {
        const m = liveMatch(row, ledger)
        if (m) return ledgerStatusLabel(m)
        const e = liveException(row, ledger)
        return e ? exceptionStatusLabel(e) : null
      }
      return row[key]
    },
    cell: (ctx) => {
      if (key === 'ledger_status') {
        const m = liveMatch(ctx.row.original, ledger)
        if (m) return <span className={`stamp stamp-${m.status}`}>{ledgerStatusLabel(m)}</span>
        const e = liveException(ctx.row.original, ledger)
        if (!e) return <span className="empty-cell">—</span>
        return <span className={`stamp stamp-${e.status}`}>{exceptionStatusLabel(e)}</span>
      }
      const v = ctx.row.original[key]
      if (key === 'exception_type' && typeof v === 'string')
        return <span className={`stamp stamp-${v}`}>{v.replace(/_/g, ' ')}</span>
      if (key === 'confidence')
        return typeof v === 'string' ? <ConfidenceBadge label={v} /> : <span className="empty-cell">—</span>
      // display-only: let SCREAMING_SNAKE literals wrap instead of forcing
      // the column to their full min-content width
      if ((key === 'gap_type' || key === 'ExpectedBasis') && typeof v === 'string')
        return v.replace(/_/g, ' ')
      const text = fmtCell(key, v as Cell)
      return text === '—' ? <span className="empty-cell">—</span> : text
    },
  }))
}

export function ExceptionQueue({
  rows, onOpenInQueue, primaryRunId, emptyNote, ledger, onLedgerChanged, customerId,
}: {
  rows: Row[]
  /** needed for manual matching (POST /api/matches/manual is per customer) */
  customerId?: string
  onOpenInQueue?: (matchLedgerId: string | null) => void
  primaryRunId?: string | null
  /** why the WHOLE queue is empty (the run raised no exceptions) — a
   *  segment with nothing in it gets its own note below */
  emptyNote?: React.ReactNode
  /** live /api/ledger state for the customer (App refetches it on every
   *  ledger epoch) — overlays status + decision controls on the frozen
   *  MATCH_REVIEW rows; null while loading or for pre-ledger runs */
  ledger?: LedgerViewData | null
  /** a decision was taken here — App bumps the ledger epoch so every
   *  ledger-fed view (and this overlay) refetches */
  onLedgerChanged?: () => void
}) {
  const [side, setSide] = useState<Side>('ALL')
  const [error, setError] = useState<string | null>(null)
  const onDecided = useCallback(() => {
    setError(null)
    onLedgerChanged?.()
  }, [onLedgerChanged])
  const { decide, busy } = useMatchDecision(onDecided, setError)
  const columns = useMemo(() => buildColumns(rows, ledger), [rows, ledger])
  const filtered = side === 'ALL' ? rows : rows.filter((r) => r.exception_type === side)

  const seg = (
    <div className="seg-row">
    <div className="seg">
      {(['ALL', 'BANK_ONLY', 'BILL_ONLY', 'MATCH_REVIEW'] as Side[]).map((s) => (
        <button key={s} className={side === s ? 'on' : ''} onClick={() => setSide(s)}>
          {s === 'ALL' ? 'All' : s.replace(/_/g, ' ')}
        </button>
      ))}
    </div>
    {error && <span className="flag-note">{error}</span>}
    </div>
  )

  return (
    <DataTable
      key={side}
      rows={filtered}
      columns={columns}
      numericIds={AMOUNT_COLS}
      toolbar={seg}
      emptyNote={rows.length === 0 ? emptyNote : (
        <p className="frame-note">
          no {side.replace(/_/g, ' ')} exceptions in this run —{' '}
          <button className="link-btn" onClick={() => setSide('ALL')}>show all</button>
        </p>
      )}
      renderDetail={(row) => (
        <Detail row={row} onOpenInQueue={onOpenInQueue} primaryRunId={primaryRunId}
                ledger={ledger} customerId={customerId} onLedgerChanged={onLedgerChanged}
                busy={typeof row.match_ledger_id === 'string' && !!busy[row.match_ledger_id]}
                decide={decide} />
      )}
    />
  )
}
