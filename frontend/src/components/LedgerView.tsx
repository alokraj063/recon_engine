import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Filter, History, RotateCw } from 'lucide-react'
import {
  acceptMatch, fetchLedger, fetchRun, rejectMatch, reopenMatch, unlockMatch,
} from '../api'
import {
  ApiError,
  type LedgerException, type LedgerMatch, type LedgerViewData, type Row,
} from '../types'
import { fmtWhen, inr, parseUtc } from '../format'
import { BillLineage } from './BillLineage'
import { ConfidenceBadge } from './ConfidenceBadge'
import { MatchedEvidence, ReviewEvidence } from './ReviewEvidence'
import { RunsView } from './RunsView'

type Evidence = Row | 'loading' | 'missing'

type ExcFilter = 'OPEN' | 'RESOLVED' | 'ALL'
type MatchStatusFilter = 'ALL' | 'OPEN' | 'LOCKED' | 'REJECTED'

const CONFIDENCE_ORDER = ['HIGH', 'AMBIGUOUS', 'LOW', 'AMOUNT_ONLY', 'BATCHED']

/** Local calendar date (yyyy-mm-dd) of a naive-UTC timestamp — the same
 *  day the WHEN column displays, so date filters match what users see. */
function localDay(iso: string): string {
  const d = parseUtc(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** In-header filter control: a funnel icon that opens a small popover.
 *  Invisible chrome until used — the funnel turns accent-colored while
 *  a filter is active. Closes on outside mousedown (RunPicker pattern). */
function HeaderFilter({ active, children }: { active: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  return (
    <span className={`th-filter${active ? ' active' : ''}`} ref={wrap}
          onClick={(e) => e.stopPropagation()}>
      <button className="th-filter-btn" onClick={() => setOpen((o) => !o)}
              title="filter">
        <Filter size={11} strokeWidth={2} />
      </button>
      {open && (
        <span className="th-pop"
              onClick={(e) => {
                // picking an option closes; interacting with date inputs doesn't
                if ((e.target as HTMLElement).closest('.th-opt')) setOpen(false)
              }}>
          {children}
        </span>
      )}
    </span>
  )
}

interface Props {
  customerId: string
  /** match_ledger id to highlight + scroll to (arriving from the
   *  Exception queue's "Decide in Analyst queue" link) */
  focusId?: string | null
  /** called once the arrival flash has played so the parent clears
   *  focusId — the highlight is transient, not a selection */
  onFocusHandled?: () => void
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

export function LedgerView({ customerId, focusId, onFocusHandled,
                             activeRunId, onOpenRun }: Props) {
  const [showRuns, setShowRuns] = useState(false)
  const [data, setData] = useState<LedgerViewData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [excFilter, setExcFilter] = useState<ExcFilter>('OPEN')
  const [confFilter, setConfFilter] = useState<string>('ALL')
  const [matchStatusFilter, setMatchStatusFilter] = useState<MatchStatusFilter>('ALL')
  const [sortAsc, setSortAsc] = useState(false)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
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

  // arriving from the Exception queue: open that match's evidence too,
  // then release the focus once the flash has played
  useEffect(() => {
    if (!focusId || !data) return
    const m = data.matches.find((x) => x.id === focusId)
    if (m && !expanded[m.id]) {
      setExpanded((x) => ({ ...x, [m.id]: true }))
      if (!evidence[m.id]) loadEvidence(m)
    }
    const t = window.setTimeout(() => onFocusHandled?.(), 3000)
    return () => window.clearTimeout(t)
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

  const confidences = useMemo(() => {
    const present = new Set((data?.matches ?? []).map((m) => m.confidence))
    return CONFIDENCE_ORDER.filter((c) => present.has(c))
      .concat([...present].filter((c) => !CONFIDENCE_ORDER.includes(c)).sort())
  }, [data])

  const visibleMatches = useMemo(() => {
    const rows = (data?.matches ?? []).filter((m) => {
      if (confFilter !== 'ALL' && m.confidence !== confFilter) return false
      if (matchStatusFilter !== 'ALL' && m.status !== matchStatusFilter) return false
      if (dateFrom || dateTo) {
        const day = localDay(m.created_at)
        if (dateFrom && day < dateFrom) return false
        if (dateTo && day > dateTo) return false
      }
      return true
    })
    return rows.sort((a, b) => sortAsc
      ? a.created_at.localeCompare(b.created_at)
      : b.created_at.localeCompare(a.created_at))
  }, [data, confFilter, matchStatusFilter, dateFrom, dateTo, sortAsc])

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
            {(confFilter !== 'ALL' || matchStatusFilter !== 'ALL' || dateFrom || dateTo) && (
              <p className="ledger-count-note">
                {visibleMatches.length} of {data.matches.length} matches
              </p>
            )}
            <div className="ledger-wrap">
            <table className="ledger ledger-matches">
              <thead>
                <tr>
                  <th className="th-sort" onClick={() => setSortAsc((v) => !v)}>
                    When {sortAsc ? '▲' : '▼'}
                    <HeaderFilter active={!!(dateFrom || dateTo)}>
                      <span className="th-pop-dates">
                        <input type="date" value={dateFrom} aria-label="from date"
                               onChange={(e) => setDateFrom(e.target.value)} />
                        <span className="chip-note">to</span>
                        <input type="date" value={dateTo} aria-label="to date"
                               onChange={(e) => setDateTo(e.target.value)} />
                        {(dateFrom || dateTo) && (
                          <button className="th-opt"
                                  onClick={() => { setDateFrom(''); setDateTo('') }}>
                            clear dates
                          </button>
                        )}
                      </span>
                    </HeaderFilter>
                  </th>
                  <th>Match</th>
                  <th>
                    Confidence{confFilter !== 'ALL' && ` · ${confFilter}`}
                    <HeaderFilter active={confFilter !== 'ALL'}>
                      {(['ALL', ...confidences]).map((c) => (
                        <button key={c}
                                className={`th-opt${confFilter === c ? ' on' : ''}`}
                                onClick={() => setConfFilter(c)}>
                          {c === 'ALL' ? 'All' : c}
                        </button>
                      ))}
                    </HeaderFilter>
                  </th>
                  <th>
                    Status{matchStatusFilter !== 'ALL' && ` · ${matchStatusFilter}`}
                    <HeaderFilter active={matchStatusFilter !== 'ALL'}>
                      {(['ALL', 'OPEN', 'LOCKED', 'REJECTED'] as MatchStatusFilter[]).map((s) => (
                        <button key={s}
                                className={`th-opt${matchStatusFilter === s ? ' on' : ''}`}
                                onClick={() => setMatchStatusFilter(s)}>
                          {s === 'ALL' ? 'All' : s.charAt(0) + s.slice(1).toLowerCase()}
                        </button>
                      ))}
                    </HeaderFilter>
                  </th>
                  <th>Locked by</th>
                  <th>Credit</th>
                  <th>Bills</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visibleMatches.length === 0 && (
                  <tr>
                    <td colSpan={8} className="frame-note">
                      No matches for these filters.
                    </td>
                  </tr>
                )}
                {visibleMatches.map((m) => {
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
                      <td title={`run-internal label: ${m.match_id}`}>
                        {m.seq !== null ? `M-${m.seq}` : m.match_id}
                      </td>
                      <td><ConfidenceBadge label={m.confidence} /></td>
                      <td><span className={`stamp stamp-${m.status}`}>{m.status}</span></td>
                      <td>
                        {m.locked_by ? m.locked_by.replace('_', ' ') : '—'}
                        {m.locked_at && (
                          <div className="chip-note">{fmtWhen(m.locked_at)}</div>
                        )}
                      </td>
                      <td>
                        {m.txn ? (
                          <div className="party-cell">
                            <span className="party-amt">{inr(m.txn.amount)}</span>
                            <span className="party-ref">{m.txn.bank_ref}</span>
                            <span className="party-meta">
                              {[m.txn.value_date, m.txn.zone].filter(Boolean).join(' · ')}
                            </span>
                          </div>
                        ) : '—'}
                      </td>
                      <td>
                        {picked.map((b) => (
                          <div key={b.gold_bill_id} className="party-cell party-bill">
                            <span className="party-amt">{inr(b.net_payable_amount)}</span>
                            <span className="party-ref">{b.bill_number ?? b.gold_bill_id.slice(0, 8)}</span>
                          </div>
                        ))}
                        {candidates > 0 && (
                          <span className="chip-note">+{candidates} candidate{candidates === 1 ? '' : 's'}</span>
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
                          <span className="decide">
                            <button
                              className="btn-open"
                              title="Reopen this decision — the match returns to OPEN for review"
                              disabled={busy[m.id]}
                              onClick={() => decide(m.id, 'unlock')}
                            >
                              Unlock
                            </button>
                          </span>
                        )}
                        {m.status === 'REJECTED' && (
                          <span className="decide">
                            <button
                              className="btn-reopen"
                              title="Undo this rejection — the match returns to OPEN and re-claims its credit and bills"
                              disabled={busy[m.id]}
                              onClick={() => decide(m.id, 'reopen')}
                            >
                              Reopen
                            </button>
                          </span>
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
                              {m.bills.filter((b) => b.role === 'picked').map((b) => (
                                <BillLineage key={b.gold_bill_id} runId={m.run_id}
                                             billNumber={b.bill_number} />
                              ))}
                            </div>
                          ) : (
                            <div className="detail-grid">
                              {/* MatchedEvidence has no candidate cards to carry
                                  per-bill accept buttons — keep the pick-list
                                  for that path only */}
                              {m.status === 'OPEN' && m.bills.length > 0
                                && ev.exception_type !== 'MATCH_REVIEW' && (
                                <>
                                  <div className="detail-section">
                                    Pick the settling bill — accepting locks the credit
                                    to YOUR choice
                                  </div>
                                  <div className="pick-list">
                                    {m.bills.map((b) => (
                                      <span key={b.gold_bill_id} className="pick-row">
                                        <span className="chip chip-bill">
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
                                ? <ReviewEvidence row={ev} runId={m.run_id}
                                    busy={!!busy[m.id]}
                                    onAcceptBill={m.status === 'OPEN'
                                      ? (no) => {
                                          const b = m.bills.find(
                                            (x) => String(x.bill_number) === String(no))
                                          if (b) decide(m.id, 'accept', b.gold_bill_id)
                                        }
                                      : undefined} />
                                : <MatchedEvidence row={ev} runId={m.run_id} />}
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
                      <td className="oneline">{excLine(e)}</td>
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
