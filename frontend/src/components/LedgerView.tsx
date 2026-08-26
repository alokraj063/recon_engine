import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, History, RotateCw } from 'lucide-react'
import {
  acceptMatch, fetchLedger, fetchRun, rejectMatch, reopenMatch, unlockMatch,
} from '../api'
import {
  ApiError,
  type LedgerException, type LedgerMatch, type LedgerViewData, type Row,
} from '../types'
import { fmtWhen, inr } from '../format'
import { ConfidenceBadge } from './ConfidenceBadge'
import { MatchedEvidence, ReviewEvidence } from './ReviewEvidence'
import { RunsView } from './RunsView'

type Evidence = Row | 'loading' | 'missing'

type ExcFilter = 'OPEN' | 'RESOLVED' | 'ALL'

interface Props {
  customerId: string
  /** match_ledger id to highlight + scroll to (arriving from the
   *  Exception queue's "Decide in Analyst queue" link) */
  focusId?: string | null
  /** run history inset (⧉ Runs, top right) */
  activeRunId: string | null
  onOpenRun: (runId: string) => void
}

function txnLine(m: LedgerMatch): string {
  const t = m.txn
  if (!t) return '—'
  return [t.bank_ref, inr(t.amount), t.value_date, t.zone].filter(Boolean).join(' · ')
}

function excLine(e: LedgerException): string {
  if (e.txn) {
    return [e.txn.bank_ref, inr(e.txn.amount), e.txn.value_date, e.txn.zone]
      .filter(Boolean).join(' · ')
  }
  if (e.bill) {
    return [e.bill.bill_number, inr(e.bill.net_payable_amount), e.bill.bill_status, e.bill.zone]
      .filter(Boolean).join(' · ')
  }
  return '—'
}

export function LedgerView({ customerId, focusId, activeRunId, onOpenRun }: Props) {
  const [showRuns, setShowRuns] = useState(false)
  const [data, setData] = useState<LedgerViewData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [excFilter, setExcFilter] = useState<ExcFilter>('OPEN')
  const scrolled = useRef<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [evidence, setEvidence] = useState<Record<string, Evidence>>({})

  /** Pull the match's evidence row from its creating run's persisted
   *  payload (fetchRun is cached + legacy-normalized). Review matches
   *  live in the queue; HIGH auto-locked ones only in matched. */
  const loadEvidence = useCallback((m: LedgerMatch) => {
    setEvidence((ev) => (ev[m.id] ? ev : { ...ev, [m.id]: 'loading' }))
    fetchRun(m.run_id)
      .then((p) => {
        const row =
          p.exceptions.find((r) => r.match_ledger_id === m.id)
          ?? p.matched.find((r) => r.match_ledger_id === m.id)
        setEvidence((ev) => ({ ...ev, [m.id]: row ?? 'missing' }))
      })
      .catch(() => setEvidence((ev) => ({ ...ev, [m.id]: 'missing' })))
  }, [])

  const toggle = (m: LedgerMatch) => {
    setExpanded((x) => ({ ...x, [m.id]: !x[m.id] }))
    if (!evidence[m.id]) loadEvidence(m)
  }

  // arriving from the Exception queue: open that match's evidence too
  useEffect(() => {
    if (!focusId || !data) return
    const m = data.matches.find((x) => x.id === focusId)
    if (m && !expanded[m.id]) {
      setExpanded((x) => ({ ...x, [m.id]: true }))
      if (!evidence[m.id]) loadEvidence(m)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, data])

  const load = useCallback(() => {
    setError(null)
    fetchLedger(customerId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [customerId])

  useEffect(load, [load])

  const decide = async (id: string,
                        action: 'accept' | 'reject' | 'unlock' | 'reopen',
                        goldBillId?: string) => {
    setBusy((b) => ({ ...b, [id]: true }))
    try {
      const res = action === 'accept' ? await acceptMatch(id, goldBillId)
        : action === 'unlock' ? await unlockMatch(id)
        : action === 'reopen' ? await reopenMatch(id)
        : await rejectMatch(id)
      // apply the authoritative response locally...
      setData((d) => d && {
        ...d,
        matches: d.matches.map((m) =>
          m.id === id
            ? {
                ...m,
                status: res.status as LedgerMatch['status'],
                locked_by: ('locked_by' in res ? res.locked_by : m.locked_by) as LedgerMatch['locked_by'],
              }
            : m),
      })
      // ...then refetch: a reject also opens a BANK_ONLY exception
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy((b) => ({ ...b, [id]: false }))
    }
  }

  const exceptions = (data?.exceptions ?? []).filter(
    (e) => excFilter === 'ALL' || e.status === excFilter,
  )

  return (
    <>
      <div className="result-head">
        <h2>Analyst queue</h2>
        <span className="file-note">
          customer: {customerId}
          <button className="btn-refresh btn-ic" onClick={load}>
            <RotateCw size={13} strokeWidth={1.75} /> refresh
          </button>
          <button className={`btn-refresh new-customer-btn btn-ic${showRuns ? ' on' : ''}`}
                  onClick={() => setShowRuns((v) => !v)}>
            <History size={14} strokeWidth={1.75} /> Runs {showRuns ? '▴' : '▾'}
          </button>
        </span>
      </div>

      {showRuns && (
        <div className="config-inset">
          <RunsView customerId={customerId} activeRunId={activeRunId}
                    onOpenRun={onOpenRun} />
        </div>
      )}

      <div className="view-card">
        {error && <p className="frame-note">{error}</p>}
        {data && data.matches.length === 0 && data.exceptions.length === 0 && (
          <p className="frame-note">
            The ledger fills up when you run in incremental mode — durable matches and carried-forward
            exceptions will appear here.
          </p>
        )}

        {data && data.matches.length > 0 && (
          <>
            <h3 className="ledger-h">Durable matches</h3>
            <div className="ledger-wrap">
            <table className="ledger ledger-matches">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Match</th>
                  <th>Confidence</th>
                  <th>Status</th>
                  <th>Locked by</th>
                  <th>Credit</th>
                  <th>Bills</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.matches.map((m) => {
                  const picked = m.bills.filter((b) => b.role === 'picked')
                  const candidates = m.bills.length - picked.length
                  const ev = evidence[m.id]
                  return (
                    <Fragment key={m.id}>
                    <tr className={`xq-row${expanded[m.id] ? ' open' : ''}${m.id === focusId ? ' focus-row' : ''}`}
                        onClick={() => toggle(m)}
                        ref={(el) => {
                          if (el && m.id === focusId && scrolled.current !== m.id) {
                            scrolled.current = m.id
                            el.scrollIntoView({ block: 'center', behavior: 'smooth' })
                          }
                        }}>
                      <td>
                        <ChevronRight className="chev chev-ic" size={14}
                                      strokeWidth={2} aria-hidden />
                        {fmtWhen(m.created_at)}
                      </td>
                      <td>{m.match_id}</td>
                      <td><ConfidenceBadge label={m.confidence} /></td>
                      <td><span className={`stamp stamp-${m.status}`}>{m.status}</span></td>
                      <td>
                        {m.locked_by ? m.locked_by.replace('_', ' ') : '—'}
                        {m.locked_at && (
                          <div className="chip-note">{fmtWhen(m.locked_at)}</div>
                        )}
                      </td>
                      <td>{txnLine(m)}</td>
                      <td>
                        {picked.map((b) => (
                          <span key={b.gold_bill_id} className="chip">
                            {b.bill_number ?? b.gold_bill_id.slice(0, 8)} · {inr(b.net_payable_amount)}
                          </span>
                        ))}
                        {candidates > 0 && (
                          <span className="chip-note"> +{candidates} candidate{candidates === 1 ? '' : 's'}</span>
                        )}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        {m.status === 'OPEN' && (
                          <span className="decide">
                            <button
                              className="btn-accept"
                              disabled={busy[m.id]}
                              onClick={() => decide(m.id, 'accept')}
                            >
                              Accept
                            </button>
                            <button
                              className="btn-reject"
                              disabled={busy[m.id]}
                              onClick={() => decide(m.id, 'reject')}
                            >
                              Reject
                            </button>
                          </span>
                        )}
                        {m.status === 'LOCKED' && (
                          <button
                            className="btn-refresh"
                            title="Reopen this decision — the match returns to OPEN for review"
                            disabled={busy[m.id]}
                            onClick={() => decide(m.id, 'unlock')}
                          >
                            Unlock
                          </button>
                        )}
                        {m.status === 'REJECTED' && (
                          <button
                            className="btn-reopen"
                            title="Undo this rejection — the match returns to OPEN and re-claims its credit and bills"
                            disabled={busy[m.id]}
                            onClick={() => decide(m.id, 'reopen')}
                          >
                            Reopen
                          </button>
                        )}
                      </td>
                    </tr>
                    {expanded[m.id] && (
                      <tr className="xq-detail">
                        <td colSpan={8}>
                          {ev === 'loading' || ev === undefined ? (
                            <p className="frame-note"><span className="quill" /> loading evidence…</p>
                          ) : ev === 'missing' ? (
                            <div className="detail-grid">
                              <div className="detail-section">
                                Evidence unavailable — the creating run's payload could not
                                be read. Ledger summary:
                              </div>
                              <div>
                                <div className="dt-label">Credit</div>
                                <div className="dt-value">{txnLine(m)}</div>
                              </div>
                              <div>
                                <div className="dt-label">Narrative</div>
                                <div className="dt-value">{m.txn?.narrative ?? '—'}</div>
                              </div>
                            </div>
                          ) : (
                            <div className="detail-grid">
                              {m.status === 'OPEN' && m.bills.length > 0 && (
                                <>
                                  <div className="detail-section">
                                    Pick the settling bill — accepting locks the credit
                                    to YOUR choice
                                  </div>
                                  <div className="pick-list">
                                    {m.bills.map((b) => (
                                      <span key={b.gold_bill_id} className="pick-row">
                                        <span className="chip">
                                          {b.bill_number ?? b.gold_bill_id.slice(0, 8)}
                                          {' · '}{inr(b.net_payable_amount)}
                                          {b.zone ? ` · ${b.zone}` : ''}
                                        </span>
                                        {b.role === 'picked' && (
                                          <span className="chip-note">matcher's pick</span>
                                        )}
                                        <button className="btn-accept" disabled={busy[m.id]}
                                                onClick={() => decide(m.id, 'accept', b.gold_bill_id)}>
                                          Accept this bill
                                        </button>
                                      </span>
                                    ))}
                                  </div>
                                </>
                              )}
                              {ev.exception_type === 'MATCH_REVIEW'
                                ? <ReviewEvidence row={ev} />
                                : <MatchedEvidence row={ev} />}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
            </div>
          </>
        )}

        {data && data.exceptions.length > 0 && (
          <>
            <h3 className="ledger-h">
              Exceptions
              <span className="seg seg-inline">
                {(['OPEN', 'RESOLVED', 'ALL'] as ExcFilter[]).map((f) => (
                  <button key={f} className={excFilter === f ? 'on' : ''} onClick={() => setExcFilter(f)}>
                    {f === 'ALL' ? 'All' : f.toLowerCase()}
                  </button>
                ))}
              </span>
            </h3>
            {exceptions.length === 0 ? (
              <p className="frame-note">Nothing with status {excFilter.toLowerCase()}.</p>
            ) : (
              <div className="ledger-wrap">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Detail</th>
                    <th>First seen (run)</th>
                    <th>Resolved by (run)</th>
                  </tr>
                </thead>
                <tbody>
                  {exceptions.map((e) => (
                    <tr key={e.id}>
                      <td><span className={`stamp stamp-${e.exception_type}`}>{e.exception_type.replace('_', ' ')}</span></td>
                      <td><span className={`stamp stamp-${e.status}`}>{e.status}</span></td>
                      <td>{excLine(e)}</td>
                      <td>{e.first_seen_run_id.slice(0, 8)}</td>
                      <td>{e.resolved_by_run_id ? e.resolved_by_run_id.slice(0, 8) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </>
        )}
      </div>

      <p className="footer-note">
        HIGH-confidence matches lock automatically; review-confidence matches wait here (and in the
        exception queue) for a human decision. A locked match never re-enters the matching pool;
        rejecting one releases its bills and re-opens the credit. A rejection can be undone with
        Reopen — unless a later run has already claimed the credit or its bills.
      </p>
    </>
  )
}
