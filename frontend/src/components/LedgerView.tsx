import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Download, ListFilter, RotateCw } from 'lucide-react'
import { fetchLedger, fetchRun, fetchRuns, ledgerWorkbookUrl } from '../api'
import {
  ApiError,
  type LedgerException, type LedgerMatch, type LedgerViewData, type Row,
  type RunListItem,
} from '../types'
import { fmtWhen, inDayRange, inr, localDay } from '../format'
import { BillLineage } from './BillLineage'
import { ConfidenceBadge } from './ConfidenceBadge'
import { MatchedEvidence, ReviewEvidence } from './ReviewEvidence'
import {
  MatchDecision, PickList, billByNumber, useMatchDecision, type DecisionResult,
} from './MatchDecision'
import { ManualMatchPicker } from './ManualMatchPicker'
import { SnapshotNotice } from './SnapshotNotice'
import {
  EMPTY_RUN_FILTER, RunFilter, runFilterSet, runLabelFor, type RunFilterValue,
} from './RunFilter'
import { ColumnFilter } from './filters/ColumnFilter'
import { FilterChips, type FilterChip } from './filters/FilterChips'
import { FilterPopover } from './filters/FilterPopover'
import { buildOptions } from './filters/facets'

type Evidence = Row | 'loading' | 'missing' | 'manual'

const CONFIDENCE_ORDER = ['HIGH', 'MANUAL', 'AMBIGUOUS', 'LOW', 'AMOUNT_ONLY', 'BATCHED']

const titleCase = (v: string) => v.charAt(0) + v.slice(1).toLowerCase()

/** The When column's filter: the shared header icon + popover, holding a
 *  date pair instead of a checklist (applies as you pick). */
function DateRangeFilter({ from, to, onChange }: {
  from: string; to: string; onChange: (from: string, to: string) => void
}) {
  const [open, setOpen] = useState(false)
  const btn = useRef<HTMLButtonElement>(null)
  const active = !!(from || to)
  return (
    <span className="col-filter" onClick={(e) => e.stopPropagation()}>
      <button ref={btn} type="button" className={`col-filter-btn${active ? ' on' : ''}`}
              onClick={() => setOpen((o) => !o)} title="filter by date" aria-label="filter by date">
        <ListFilter size={12} strokeWidth={2} />
        {active && <span className="col-filter-dot" aria-hidden />}
      </button>
      <FilterPopover anchorRef={btn} open={open} onClose={() => setOpen(false)}>
        <div className="filter-pop-head">When</div>
        <div className="filter-pop-dates">
          <input type="date" value={from} aria-label="from date"
                 onChange={(e) => onChange(e.target.value, to)} />
          <span className="chip-note">to</span>
          <input type="date" value={to} aria-label="to date"
                 onChange={(e) => onChange(from, e.target.value)} />
        </div>
        <div className="filter-pop-foot">
          <button className="link-btn" onClick={() => { onChange('', ''); setOpen(false) }}>Clear</button>
          <button className="filter-apply" onClick={() => setOpen(false)}>Done</button>
        </div>
      </FilterPopover>
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
  /** the empty-ledger notice links back to Reconcile */
  onGoToReconcile: () => void
}

function txnLine(m: LedgerMatch): string {
  const t = m.txn
  if (!t) return '—'
  return [t.bank_ref, inr(t.amount), t.value_date, t.zone].filter(Boolean).join(' · ')
}

/** "Resolved by" for the Exceptions panel: the run that matched it, or
 *  the analyst decision (accept / manual pairing / undo of a rejection)
 *  and the match it points at. */
function resolvedBy(e: LedgerException, runs: RunListItem[]): string {
  if (e.status !== 'RESOLVED') return '—'
  const m = e.resolved_by_match_seq != null ? `M-${e.resolved_by_match_seq}` : null
  switch (e.resolved_by) {
    case 'USER_ACCEPT': return `accepted${m ? ` ${m}` : ''}`
    case 'USER_MANUAL': return `matched by user${m ? ` (${m})` : ''}`
    case 'USER_REOPEN': return `reopened${m ? ` ${m}` : ''}`
    default: return e.resolved_by_run_id ? `run ${runLabelFor(runs, e.resolved_by_run_id)}` : '—'
  }
}

function excLine(e: LedgerException): string {
  if (e.txn) {
    return [e.txn.bank_ref, inr(e.txn.amount), e.txn.value_date, e.txn.zone,
            e.gap_type ? e.gap_type.replace(/_/g, ' ').toLowerCase() : null]
      .filter(Boolean).join(' · ')
  }
  if (e.bill) {
    return [e.bill.bill_number, inr(e.bill.net_payable_amount), e.bill.bill_status, e.bill.zone]
      .filter(Boolean).join(' · ')
  }
  return '—'
}

export function LedgerView({ customerId, focusId, onFocusHandled, onGoToReconcile }: Props) {
  const [data, setData] = useState<LedgerViewData | null>(null)
  // the customer's runs: labels for run ids + the run filter's choices
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [runFilter, setRunFilter] = useState<RunFilterValue>(EMPTY_RUN_FILTER)
  const [error, setError] = useState<string | null>(null)
  // column filters — multi-select; empty = every value. OPEN is the
  // exceptions default so the queue opens on what needs work
  const [excFilter, setExcFilter] = useState<string[]>(['OPEN'])
  const [confFilter, setConfFilter] = useState<string[]>([])
  const [matchStatusFilter, setMatchStatusFilter] = useState<string[]>([])
  const [sortAsc, setSortAsc] = useState(false)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const scrolled = useRef<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [evidence, setEvidence] = useState<Record<string, Evidence>>({})
  // the OPEN exception an analyst is pairing by hand (one picker at a time)
  const [picking, setPicking] = useState<string | null>(null)

  /** Pull the match's evidence row from its creating run's persisted
   *  payload (fetchRun is cached + legacy-normalized). Review matches
   *  live in the queue; HIGH auto-locked ones only in matched. */
  const loadEvidence = useCallback((m: LedgerMatch) => {
    // a MANUAL match has no creating run and therefore no engine
    // evidence: the ledger summary + the analyst's note IS its evidence
    if (!m.run_id) {
      setEvidence((ev) => ({ ...ev, [m.id]: 'manual' }))
      return
    }
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
    fetchRuns(customerId, 200).then(setRuns).catch(() => setRuns([]))
  }, [customerId])

  useEffect(load, [load])

  // a run filter belongs to one customer's run history
  useEffect(() => setRunFilter(EMPTY_RUN_FILTER), [customerId])

  const runSet = useMemo(() => runFilterSet(runFilter, runs), [runFilter, runs])

  // decisions go through the shared MatchDecision hook (the Exception
  // queue uses the same one): apply the authoritative response locally,
  // then refetch — a reject also opens a BANK_ONLY exception
  const onDecided = useCallback((id: string, res: DecisionResult) => {
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
    load()
  }, [load])
  const { decide, busy } = useMatchDecision(onDecided, setError)

  // an exception belongs to the run that first saw it AND the run that
  // resolved it — filtering by either run keeps it in view
  const allExceptions = data?.exceptions ?? []
  const exceptions = allExceptions.filter(
    (e) => (excFilter.length === 0 || excFilter.includes(e.status))
      && (!runSet || (!!e.first_seen_run_id && runSet.has(e.first_seen_run_id))
          || (!!e.resolved_by_run_id && runSet.has(e.resolved_by_run_id))),
  )

  const confidences = useMemo(() => {
    const present = new Set((data?.matches ?? []).map((m) => m.confidence))
    return CONFIDENCE_ORDER.filter((c) => present.has(c))
      .concat([...present].filter((c) => !CONFIDENCE_ORDER.includes(c)).sort())
  }, [data])

  const visibleMatches = useMemo(() => {
    const rows = (data?.matches ?? []).filter((m) => {
      if (confFilter.length && !confFilter.includes(m.confidence)) return false
      if (matchStatusFilter.length && !matchStatusFilter.includes(m.status)) return false
      // a MANUAL match belongs to no run: a run filter hides it
      if (runSet && (!m.run_id || !runSet.has(m.run_id))) return false
      if (!inDayRange(localDay(m.created_at), dateFrom, dateTo)) return false
      return true
    })
    return rows.sort((a, b) => sortAsc
      ? a.created_at.localeCompare(b.created_at)
      : b.created_at.localeCompare(a.created_at))
  }, [data, confFilter, matchStatusFilter, runSet, dateFrom, dateTo, sortAsc])

  const matchesFiltered = confFilter.length > 0 || matchStatusFilter.length > 0
    || !!dateFrom || !!dateTo || runSet !== null
  const allMatches = data?.matches ?? []
  const matchChips: FilterChip[] = [
    { key: 'when', label: 'When',
      values: dateFrom || dateTo ? [`${dateFrom || '…'} → ${dateTo || '…'}`] : [],
      onRemove: () => { setDateFrom(''); setDateTo('') } },
    { key: 'confidence', label: 'Confidence', values: confFilter,
      onRemove: (v) => setConfFilter(v === undefined ? [] : confFilter.filter((x) => x !== v)) },
    { key: 'status', label: 'Status', values: matchStatusFilter, format: titleCase,
      onRemove: (v) => setMatchStatusFilter(v === undefined ? [] : matchStatusFilter.filter((x) => x !== v)) },
  ]
  const excChips: FilterChip[] = [
    { key: 'exc-status', label: 'Status', values: excFilter, format: titleCase,
      onRemove: (v) => setExcFilter(v === undefined ? [] : excFilter.filter((x) => x !== v)) },
  ]
  const runNote = data && runSet
    ? `${visibleMatches.length} of ${data.matches.length} matches`
    : undefined

  return (
    <>
      <div className="result-head">
        <h2 className="page-title">Analyst queue</h2>
        <span className="file-note">
          customer: {customerId}
          <button className="btn-refresh btn-ic" onClick={load}>
            <RotateCw size={13} strokeWidth={1.75} /> refresh
          </button>
          <a className="btn-download btn-ic" href={ledgerWorkbookUrl(customerId)} download
             title="Ledger as Excel — Matches (incl. manual), Manual_Matches, Exceptions">
            <Download size={13} strokeWidth={1.75} /> Export ledger
          </a>
          {runs.length > 0 && (
            <RunFilter runs={runs} value={runFilter} onChange={setRunFilter}
                       note={runNote} />
          )}
        </span>
      </div>

      <div className="view-card">
        {error && <p className="frame-note">{error}</p>}
        {data && data.matches.length === 0 && data.exceptions.length === 0 && (
          <SnapshotNotice runs={runs} what="The Analyst queue" onGoToReconcile={onGoToReconcile} />
        )}

        {data && data.matches.length > 0 && (
          <>
            <div className="ledger-filter-row">
              <FilterChips chips={matchChips} />
              {matchesFiltered && (
                <span className="ledger-count-note">
                  {visibleMatches.length} of {data.matches.length} matches
                </span>
              )}
            </div>
            <div className="ledger-wrap">
            <table className="ledger ledger-matches">
              <thead>
                <tr>
                  <th className="th-sort" onClick={() => setSortAsc((v) => !v)}>
                    When {sortAsc ? '▲' : '▼'}
                    <DateRangeFilter from={dateFrom} to={dateTo}
                                     onChange={(f, t) => { setDateFrom(f); setDateTo(t) }} />
                  </th>
                  <th>Match</th>
                  <th>Run</th>
                  <th>
                    Confidence
                    <ColumnFilter label="Confidence" value={confFilter} onApply={setConfFilter}
                                  options={buildOptions(allMatches, (m) => m.confidence)
                                    .sort((a, b) => confidences.indexOf(a.value) - confidences.indexOf(b.value))} />
                  </th>
                  <th>
                    Status
                    <ColumnFilter label="Status" value={matchStatusFilter} onApply={setMatchStatusFilter}
                                  format={titleCase}
                                  options={buildOptions(allMatches, (m) => m.status)} />
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
                    <td colSpan={9} className="frame-note">
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
                      <td className="run-cell" title={m.run_id ?? 'matched by user — no creating run'}>
                        {m.run_id ? runLabelFor(runs, m.run_id) : <span className="chip-note">manual</span>}
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
                        <MatchDecision match={m} busy={!!busy[m.id]} decide={decide} />
                      </td>
                    </tr>
                    {expanded[m.id] && (
                      <tr className="xq-detail">
                        <td colSpan={9}>
                          {ev === 'loading' || ev === undefined ? (
                            <p className="frame-note"><span className="quill" /> loading evidence…</p>
                          ) : ev === 'missing' || ev === 'manual' ? (
                            <div className="detail-grid">
                              <div className="detail-section">
                                {ev === 'manual'
                                  ? 'Matched by user — no engine evidence; the analyst paired this credit and bill(s) by hand.'
                                  : "Evidence unavailable — the creating run's payload could not be read. Ledger summary:"}
                              </div>
                              {ev === 'manual' && (
                                <div>
                                  <div className="dt-label">Note</div>
                                  <div className="dt-value">{m.note || '—'}</div>
                                </div>
                              )}
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
                              {ev.exception_type !== 'MATCH_REVIEW' && (
                                <PickList match={m} busy={!!busy[m.id]} decide={decide} />
                              )}
                              {ev.exception_type === 'MATCH_REVIEW'
                                ? <ReviewEvidence row={ev} runId={m.run_id}
                                    busy={!!busy[m.id]}
                                    onAcceptBill={m.status === 'OPEN'
                                      ? (no) => {
                                          const b = billByNumber(m, no)
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

        {data && allExceptions.length > 0 && (
          <>
            <h3 className="ledger-h">
              Exceptions
              {runSet && (
                <span className="chip-note"> {exceptions.length} of {allExceptions.length} </span>
              )}
            </h3>
            <div className="ledger-filter-row">
              <FilterChips chips={excChips} />
            </div>
            {exceptions.length === 0 ? (
              <p className="frame-note">
                Nothing with status {excFilter.map(titleCase).join(' / ').toLowerCase()}
                {runSet ? ' for the selected runs' : ''} —{' '}
                <button className="link-btn" onClick={() => setExcFilter([])}>show all</button>
              </p>
            ) : (
              <div className="ledger-wrap">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>
                      Status
                      <ColumnFilter label="Status" value={excFilter} onApply={setExcFilter}
                                    format={titleCase}
                                    options={buildOptions(allExceptions, (e) => e.status)} />
                    </th>
                    <th>Detail</th>
                    <th>First seen (run)</th>
                    <th>Resolved by</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {exceptions.map((e) => (
                    <Fragment key={e.id}>
                    <tr>
                      <td><span className={`stamp stamp-${e.exception_type}`}>{e.exception_type.replace('_', ' ')}</span></td>
                      <td><span className={`stamp stamp-${e.status}`}>{e.status}</span></td>
                      <td className="oneline">{excLine(e)}</td>
                      <td className="run-cell" title={e.first_seen_run_id ?? undefined}>
                        {runLabelFor(runs, e.first_seen_run_id)}
                      </td>
                      <td className="run-cell"
                          title={[e.resolved_by, e.resolved_by_run_id ?? e.resolved_by_match_id,
                                  e.resolved_at ? fmtWhen(e.resolved_at) : null]
                                   .filter(Boolean).join(' · ') || undefined}>
                        {resolvedBy(e, runs)}
                      </td>
                      <td>
                        {e.status === 'OPEN' && (
                          <button className="btn-open"
                                  title={e.exception_type === 'BANK_ONLY'
                                    ? 'pair this credit with open bill(s) by hand'
                                    : 'pair this bill with an open credit by hand'}
                                  onClick={() => setPicking(picking === e.id ? null : e.id)}>
                            {e.exception_type === 'BANK_ONLY' ? 'Match to bill…' : 'Match to credit…'}
                          </button>
                        )}
                      </td>
                    </tr>
                    {picking === e.id && (
                      <tr className="xq-detail">
                        <td colSpan={6}>
                          <ManualMatchPicker
                            customerId={customerId}
                            anchor={e}
                            candidates={allExceptions.filter((x) => x.status === 'OPEN'
                              && x.exception_type === (e.exception_type === 'BANK_ONLY' ? 'BILL_ONLY' : 'BANK_ONLY'))}
                            onDone={() => { setPicking(null); load() }}
                            onCancel={() => setPicking(null)} />
                        </td>
                      </tr>
                    )}
                    </Fragment>
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
