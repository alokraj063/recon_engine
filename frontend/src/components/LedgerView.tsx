import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, CheckCircle2, ChevronRight, Clock3, Download, ListChecks, ListFilter, Lock,
} from 'lucide-react'
import { fetchLedger, fetchRun, fetchRuns, ledgerWorkbookUrl } from '../api'
import {
  ApiError,
  type LedgerException, type LedgerMatch, type LedgerViewData, type Row,
  type RunListItem,
} from '../types'
import { fmtDay, fmtWhen, inDayRange, inr, inrCompact, localDay, n, plural } from '../format'
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
import { buildOptions, deSnake, facetKey } from './filters/facets'
import {
  EmptyState, MoreRows, Notice, PageHeader, RefreshButton, Stat, StatStrip, TextLink, ToolSep,
  useProgressiveRows,
} from './ui'

type Evidence = Row | 'loading' | 'missing' | 'manual'

const CONFIDENCE_ORDER = ['HIGH', 'MANUAL', 'AMBIGUOUS', 'LOW', 'AMOUNT_ONLY', 'BATCHED']

const titleCase = (v: string) => v.charAt(0) + v.slice(1).toLowerCase()
/** exception type in words — the cell says Credit / Bill like the
 *  Command Center; filters and chips add which side is missing */
const typeLabel = (v: string) =>
  v === 'BANK_ONLY' ? 'Credit — no bill' : v === 'BILL_ONLY' ? 'Bill — no credit' : deSnake(v)

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

/**
 * What a Command Center heading asks this queue to show on arrival.
 *
 * Every field maps onto a filter the queue ALREADY owns, deliberately:
 * the preset lands as a removable FilterChip, so an analyst can see why
 * the table is narrowed and clear it in one click. A preset that could
 * not be expressed as a visible chip would read as a broken page.
 * An empty array means "clear that filter" (`[]` = every value).
 */
export interface LedgerIntent {
  /** matches → Status (OPEN | LOCKED | REJECTED) */
  matchStatus?: string[]
  /** exceptions → Status (OPEN | RESOLVED) */
  excStatus?: string[]
  /** exceptions → Type (BANK_ONLY | BILL_ONLY) */
  excType?: string[]
  /** exceptions → the BANK_ONLY gap code (see gapOf) */
  excGap?: string[]
  /** exceptions → Scope "Needs action": true hides other receipts
   *  (UNRECOGNISED_RECEIPT) and credits awaiting data (AWAITING_STATUS /
   *  AWAITING_BILL_DATA) — exactly the rows db/overview.open_in_scope
   *  leaves out; false clears it */
  excOpenWork?: boolean
  /** which tab (Matches | Exceptions) to open on */
  section?: 'matches' | 'exceptions'
  /** the Command Center's date window (yyyy-mm-dd, '' = unbounded),
   *  applied on the SAME dates db/overview counts with: a match by its
   *  credit's value date, an exception by its credit's value date or its
   *  bill's due date. Always sent, so an "All time" arrival clears it. */
  from?: string
  to?: string
}

/* Quick views: one click resets the queue to a named slice of work.
   Each is an ordinary LedgerIntent, so it lands as removable chips; a
   date window already on the page is kept (the figures count within it). */
const QV_REVIEW: LedgerIntent = { section: 'matches', matchStatus: ['OPEN'] }
const QV_SETTLED: LedgerIntent = { section: 'matches', matchStatus: ['LOCKED'] }
const QV_OPEN: LedgerIntent = {
  section: 'exceptions', excStatus: ['OPEN'], excType: [], excGap: [], excOpenWork: true,
}
const QV_AWAITING: LedgerIntent = {
  section: 'exceptions', excStatus: ['OPEN'], excType: ['BANK_ONLY'],
  excGap: ['AWAITING_STATUS', 'AWAITING_BILL_DATA'], excOpenWork: false,
}

/** The date db/overview windows an exception on: BANK_ONLY by the
 *  credit's value date, BILL_ONLY by advice -> order -> submission. */
function excDay(e: LedgerException): string {
  return ((e.txn ? e.txn.value_date : e.bill?.due_date) ?? '').slice(0, 10)
}

interface Props {
  customerId: string
  /** match_ledger id to highlight + scroll to (arriving from the
   *  Exception queue's "Decide in Analyst queue" link) */
  focusId?: string | null
  /** called once the arrival flash has played so the parent clears
   *  focusId — the highlight is transient, not a selection */
  onFocusHandled?: () => void
  /** filters to preset + section to land on, set by a Command Center
   *  heading (see LedgerIntent) */
  intent?: LedgerIntent | null
  /** called once the preset has been applied so the parent clears it —
   *  an intent is an arrival instruction, not a selection */
  onIntentHandled?: () => void
  /** the empty-ledger notice links back to Reconcile */
  onGoToReconcile: () => void
  /** display name for the head's context line */
  customerName?: string
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

/** The BANK_ONLY gap code, derived exactly the way
 *  db/overview.unrecognised_clause derives it server-side: the stored
 *  code, or — on rows written before that column existed — a blank
 *  zone_guess, which is precisely what made the default mapping assign
 *  UNRECOGNISED_RECEIPT. Keeping the rules in step is what lets each
 *  Match performance row link to a queue filter selecting the same
 *  credits it counted. BILL_ONLY rows have no gap. */
/** gap codes the Open exceptions tile does not count (db/overview.
 *  open_in_scope): each is reported once elsewhere on the Command Center */
const NOT_OPEN_WORK = new Set(['UNRECOGNISED_RECEIPT', 'AWAITING_STATUS', 'AWAITING_BILL_DATA'])

export function gapOf(e: LedgerException): string | null {
  if (e.exception_type !== 'BANK_ONLY') return null
  // the server's read-time reading wins: it is the only thing that can
  // tell the two awaiting buckets apart from a plain missing bill
  if (e.gap_detail) return e.gap_detail
  if (e.gap_type) return e.gap_type
  return e.txn?.zone ? 'SIGNAL_BILL_NOT_FOUND' : 'UNRECOGNISED_RECEIPT'
}

/** One exception's side-neutral cells — the same Ref / Zone / Date /
 *  Amount spine as the Command Center's "Largest open exceptions". */
function excCells(e: LedgerException) {
  if (e.txn) {
    // gapOf, not gap_type: the stored code cannot tell the two awaiting
    // readings apart, so a row opened from "Awaiting bill data" would sit
    // under a chip naming a reason the row itself denied. Server label
    // first (per-customer wording), else the code spelled out.
    const gap = gapOf(e)
    return { ref: e.txn.bank_ref, sub: null, zone: e.txn.zone, date: e.txn.value_date,
             amount: e.txn.amount, gap: gap ? (e.gap_label ?? deSnake(gap)) : null }
  }
  return { ref: e.bill?.bill_number ?? null, sub: e.bill?.bill_status ?? null,
           zone: e.bill?.zone ?? null, date: e.bill?.due_date ?? null,
           amount: e.bill?.net_payable_amount ?? null, gap: null }
}

export function LedgerView({
  customerId, focusId, onFocusHandled, intent, onIntentHandled, onGoToReconcile, customerName,
}: Props) {
  const [data, setData] = useState<LedgerViewData | null>(null)
  // the customer's runs: labels for run ids + the run filter's choices
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [runFilter, setRunFilter] = useState<RunFilterValue>(EMPTY_RUN_FILTER)
  const [error, setError] = useState<string | null>(null)
  // column filters — multi-select; empty = every value. OPEN is the
  // exceptions default so the queue opens on what needs work
  const [excFilter, setExcFilter] = useState<string[]>(['OPEN'])
  const [excTypeFilter, setExcTypeFilter] = useState<string[]>([])
  const [excGapFilter, setExcGapFilter] = useState<string[]>([])
  // "Needs action": drop other receipts (never matchable) and credits
  // awaiting data (cannot have matched yet) — the Open exceptions tile
  const [excOpenWork, setExcOpenWork] = useState(false)
  // the window a Command Center heading arrived with — a separate filter
  // from When (the match's created_at), because the figures it came from
  // are dated by the credit / bill, not by the match row
  const [windowFrom, setWindowFrom] = useState('')
  const [windowTo, setWindowTo] = useState('')
  const [confFilter, setConfFilter] = useState<string[]>([])
  const [matchStatusFilter, setMatchStatusFilter] = useState<string[]>([])
  const [sortAsc, setSortAsc] = useState(false)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const scrolled = useRef<string | null>(null)
  // which table is showing. null = not chosen yet: the first load picks
  // the one with work in it (matches to review first)
  const [tab, setTab] = useState<'matches' | 'exceptions' | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [evidence, setEvidence] = useState<Record<string, Evidence>>({})
  // the OPEN exception an analyst is pairing by hand (one picker at a time)
  const [picking, setPicking] = useState<string | null>(null)
  // exception rows expanded to show their advice / narrative
  const [excOpen, setExcOpen] = useState<Record<string, boolean>>({})

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
    setTab('matches')
    const m = data.matches.find((x) => x.id === focusId)
    if (m && !expanded[m.id]) {
      setExpanded((x) => ({ ...x, [m.id]: true }))
      if (!evidence[m.id]) loadEvidence(m)
    }
    const t = window.setTimeout(() => onFocusHandled?.(), 3000)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, data])

  // Arriving from a Command Center heading: apply its preset onto this
  // view's own filters, so the narrowing shows as a removable chip
  // rather than as an inexplicably short table. The run filter is
  // cleared because a Command Center tile counts the whole customer, not
  // one run — leaving a stale run filter on would silently double-narrow.
  // The tile's date window lands as its own chip (Credit date / Date),
  // applied on the dates db/overview counted with — never folded into
  // When, which is the match row's created_at. The quick views at the
  // top of the page take the same path with an empty window.
  const applyIntent = (it: LedgerIntent) => {
    if (it.matchStatus) {
      setMatchStatusFilter(it.matchStatus)
      setConfFilter([])
      setDateFrom('')
      setDateTo('')
    }
    if (it.excStatus) setExcFilter(it.excStatus)
    if (it.excType) setExcTypeFilter(it.excType)
    if (it.excGap) setExcGapFilter(it.excGap)
    if (it.excOpenWork !== undefined) setExcOpenWork(it.excOpenWork)
    if (it.from !== undefined || it.to !== undefined) {
      setWindowFrom(it.from ?? '')
      setWindowTo(it.to ?? '')
    }
    setRunFilter(EMPTY_RUN_FILTER)
    if (it.section) setTab(it.section)
  }

  useEffect(() => {
    if (!intent) return
    applyIntent(intent)
    onIntentHandled?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent])

  const load = useCallback(() => {
    setError(null)
    fetchLedger(customerId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
    fetchRuns(customerId, 200).then(setRuns).catch(() => setRuns([]))
  }, [customerId])

  useEffect(load, [load])

  // a run filter and an arrival window belong to one customer — reset on
  // a real SWITCH only. Effects also run on mount, and this one runs after
  // the intent effect above, so an unconditional reset wiped the window a
  // Command Center link had just set (Open exceptions 18 -> 1,855 rows).
  const shownCustomer = useRef(customerId)
  useEffect(() => {
    if (shownCustomer.current === customerId) return
    shownCustomer.current = customerId
    setRunFilter(EMPTY_RUN_FILTER)
    setWindowFrom('')
    setWindowTo('')
  }, [customerId])

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
      && (excTypeFilter.length === 0 || excTypeFilter.includes(e.exception_type))
      && (excGapFilter.length === 0 || excGapFilter.includes(facetKey(gapOf(e))))
      && (!excOpenWork || !NOT_OPEN_WORK.has(gapOf(e) ?? ''))
      && (!(windowFrom || windowTo) || inDayRange(excDay(e), windowFrom, windowTo))
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
      if ((windowFrom || windowTo)
          && !inDayRange((m.txn?.value_date ?? '').slice(0, 10), windowFrom, windowTo)) return false
      return true
    })
    return rows.sort((a, b) => sortAsc
      ? a.created_at.localeCompare(b.created_at)
      : b.created_at.localeCompare(a.created_at))
  }, [data, confFilter, matchStatusFilter, runSet, dateFrom, dateTo, windowFrom, windowTo, sortAsc])

  const windowOn = !!(windowFrom || windowTo)
  const windowValues = windowOn ? [`${windowFrom || '…'} → ${windowTo || '…'}`] : []
  const clearWindow = () => { setWindowFrom(''); setWindowTo('') }
  const allMatches = data?.matches ?? []
  const matchChips: FilterChip[] = [
    { key: 'when', label: 'When',
      values: dateFrom || dateTo ? [`${dateFrom || '…'} → ${dateTo || '…'}`] : [],
      onRemove: () => { setDateFrom(''); setDateTo('') } },
    { key: 'credit-date', label: 'Credit date', values: windowValues, onRemove: clearWindow },
    { key: 'confidence', label: 'Confidence', values: confFilter,
      onRemove: (v) => setConfFilter(v === undefined ? [] : confFilter.filter((x) => x !== v)) },
    { key: 'status', label: 'Status', values: matchStatusFilter, format: titleCase,
      onRemove: (v) => setMatchStatusFilter(v === undefined ? [] : matchStatusFilter.filter((x) => x !== v)) },
  ]
  const excChips: FilterChip[] = [
    { key: 'exc-status', label: 'Status', values: excFilter, format: titleCase,
      onRemove: (v) => setExcFilter(v === undefined ? [] : excFilter.filter((x) => x !== v)) },
    { key: 'exc-type', label: 'Type', values: excTypeFilter, format: typeLabel,
      onRemove: (v) => setExcTypeFilter(v === undefined ? [] : excTypeFilter.filter((x) => x !== v)) },
    { key: 'exc-gap', label: 'Gap', values: excGapFilter, format: deSnake,
      onRemove: (v) => setExcGapFilter(v === undefined ? [] : excGapFilter.filter((x) => x !== v)) },
    { key: 'exc-scope', label: 'Scope', values: excOpenWork ? ['Needs action'] : [],
      onRemove: () => setExcOpenWork(false) },
    { key: 'exc-date', label: 'Date', values: windowValues, onRemove: clearWindow },
  ]
  const drawnMatches = useProgressiveRows(visibleMatches)
  const drawnExc = useProgressiveRows(exceptions)
  // a focused match (arriving from the Exception queue / AR / Audit) may
  // sit past the first batch: draw down to it so its row exists to scroll to
  const focusIdx = focusId ? visibleMatches.findIndex((m) => m.id === focusId) : -1
  const revealMatches = drawnMatches.reveal
  useEffect(() => { if (focusIdx >= 0) revealMatches(focusIdx + 1) }, [focusIdx, revealMatches])
  const runNote = data && runSet
    ? `${visibleMatches.length} of ${data.matches.length} matches`
    : undefined

  // quick-view figures — over the whole ledger, narrowed ONLY by a date
  // window a Command Center link brought (so "3 open" there reads 3 here
  // too); the column filters never move them
  const inWinM = (m: LedgerMatch) => !windowOn
    || inDayRange((m.txn?.value_date ?? '').slice(0, 10), windowFrom, windowTo)
  const inWinE = (e: LedgerException) => !windowOn || inDayRange(excDay(e), windowFrom, windowTo)
  const toReview = allMatches.filter((m) => m.status === 'OPEN' && inWinM(m))
  const settled = allMatches.filter((m) => m.status === 'LOCKED' && inWinM(m))
  const openExc = allExceptions.filter((e) => e.status === 'OPEN' && inWinE(e))
  const needsAction = openExc.filter((e) => !NOT_OPEN_WORK.has(gapOf(e) ?? ''))
  const awaiting = openExc.filter((e) => (gapOf(e) ?? '').startsWith('AWAITING_'))
  const sumTxn = (ms: LedgerMatch[]) => ms.reduce((a, m) => a + (m.txn?.amount ?? 0), 0)
  const sumExc = (es: LedgerException[]) => es.reduce((a, e) => a + (excCells(e).amount ?? 0), 0)
  const needCredits = needsAction.filter((e) => e.exception_type === 'BANK_ONLY').length
  const needBills = needsAction.length - needCredits

  const empty = !!data && data.matches.length === 0 && data.exceptions.length === 0
  const activeTab: 'matches' | 'exceptions' = tab
    ?? (toReview.length > 0 || allExceptions.length === 0 ? 'matches' : 'exceptions')
  const chips = activeTab === 'matches' ? matchChips : excChips
  const anyChip = chips.some((c) => c.values.length > 0)
  const shown = activeTab === 'matches' ? visibleMatches.length : exceptions.length
  const total = activeTab === 'matches' ? allMatches.length : allExceptions.length
  const clearExc = () => {
    setExcFilter([]); setExcTypeFilter([]); setExcGapFilter([]); setExcOpenWork(false); clearWindow()
    setRunFilter(EMPTY_RUN_FILTER)
  }
  const clearMatches = () => {
    setConfFilter([]); setMatchStatusFilter([]); setDateFrom(''); setDateTo(''); clearWindow()
    setRunFilter(EMPTY_RUN_FILTER)
  }

  return (
    <section className="ui-page">
      <PageHeader title="Analyst queue"
                  context={<>Decide weak matches and work the exceptions
                    {customerName ? <> · {customerName}</> : null}</>}>
        {runs.length > 0 && (
          <RunFilter runs={runs} value={runFilter} onChange={setRunFilter} note={runNote} />
        )}
        <RefreshButton onClick={load} label="Refresh the queue" />
        <ToolSep />
        <a className="ui-btn" href={ledgerWorkbookUrl(customerId)} download
           title="Ledger as Excel — Matches (incl. manual), Manual_Matches, Exceptions">
          <Download size={15} strokeWidth={1.75} /> Export ledger
        </a>
      </PageHeader>

      {error && <Notice tone="error">{error}</Notice>}

      {!data && !error && <div className="ui-card sk" style={{ minHeight: 320 }} />}

      {empty && (
        <section className="ui-card">
          <SnapshotNotice runs={runs} what="The Analyst queue" onGoToReconcile={onGoToReconcile} />
        </section>
      )}

      {data && !empty && (
        <>
          <StatStrip>
            <Stat label={<><ListChecks size={12} strokeWidth={2} /> To review</>}
                  value={n(toReview.length)} tone={toReview.length ? 'warn' : 'ok'}
                  sub={toReview.length
                    ? `${plural(toReview.length, 'match', 'matches')} · ${inrCompact(sumTxn(toReview))}`
                    : 'nothing waiting'}
                  onOpen={() => applyIntent(QV_REVIEW)}
                  title="Weak matches waiting for accept or reject" />
            <Stat label={<><AlertTriangle size={12} strokeWidth={2} /> Open exceptions</>}
                  value={n(needsAction.length)} tone={needsAction.length ? 'bad' : 'ok'}
                  sub={needsAction.length
                    ? `${n(needCredits)} ${plural(needCredits, 'credit', 'credits')} · ${n(needBills)} ${plural(needBills, 'bill', 'bills')} · ${inrCompact(sumExc(needsAction))}`
                    : 'all clear'}
                  onOpen={() => applyIntent(QV_OPEN)}
                  title="Unmatched credits and bills that need an analyst (other receipts and credits awaiting data left out)" />
            <Stat label={<><Clock3 size={12} strokeWidth={2} /> Awaiting data</>}
                  value={n(awaiting.length)}
                  sub={awaiting.length
                    ? `${plural(awaiting.length, 'credit', 'credits')} · ${inrCompact(sumExc(awaiting))}`
                    : 'none'}
                  onOpen={() => applyIntent(QV_AWAITING)}
                  title="Credits that could not have matched yet — the bill is still in flight, or its export is not ingested" />
            <Stat label={<><Lock size={12} strokeWidth={2} /> Settled</>}
                  value={n(settled.length)}
                  sub={`${plural(settled.length, 'match', 'matches')} · ${inrCompact(sumTxn(settled))}`}
                  onOpen={() => applyIntent(QV_SETTLED)}
                  title="Locked matches — auto (HIGH), accepted or matched by hand" />
          </StatStrip>

          <section className="ui-card">
            <div className="ui-tabbar">
              <div className="ui-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={activeTab === 'matches'}
                        className={`ui-tab${activeTab === 'matches' ? ' is-on' : ''}`}
                        onClick={() => setTab('matches')}>
                  Matches <span className="ui-tab-count">{n(visibleMatches.length)}</span>
                </button>
                <button type="button" role="tab" aria-selected={activeTab === 'exceptions'}
                        className={`ui-tab${activeTab === 'exceptions' ? ' is-on' : ''}`}
                        onClick={() => setTab('exceptions')}>
                  Exceptions <span className="ui-tab-count">{n(exceptions.length)}</span>
                </button>
              </div>
              {shown !== total && (
                <span className="ui-tabbar-note">
                  {n(shown)} of {n(total)}{' '}
                  {activeTab === 'matches'
                    ? plural(total, 'match', 'matches') : plural(total, 'exception', 'exceptions')}
                  <TextLink onClick={activeTab === 'matches' ? clearMatches : clearExc}>Show all</TextLink>
                </span>
              )}
            </div>
            {anyChip && (
              <div className="ui-filterbar"><FilterChips chips={chips} /></div>
            )}

            {activeTab === 'matches' && (allMatches.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />} title="No matches yet">
                <span>Matches appear here once an incremental run pairs credits with bills.</span>
              </EmptyState>
            ) : (
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
                  <th>Credit</th>
                  <th>Bills</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {visibleMatches.length === 0 && (
                  <tr>
                    <td colSpan={8} className="ui-table-empty">
                      No matches for these filters —{' '}
                      <TextLink onClick={clearMatches}>show all</TextLink>
                    </td>
                  </tr>
                )}
                {drawnMatches.shown.map((m) => {
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
                      <td className="mono" title={`run-internal label: ${m.match_id}`}>
                        {m.seq !== null ? `M-${m.seq}` : m.match_id}
                      </td>
                      <td className="run-cell" title={m.run_id ?? 'matched by user — no creating run'}>
                        {m.run_id ? runLabelFor(runs, m.run_id) : <span className="chip-note">manual</span>}
                      </td>
                      <td><ConfidenceBadge label={m.confidence} /></td>
                      <td>
                        <span className={`stamp stamp-${m.status}`}>{m.status}</span>
                        {m.locked_by && (
                          <div className="chip-note" title={m.locked_at ? fmtWhen(m.locked_at) : undefined}>
                            by {m.locked_by.replace('_', ' ').toLowerCase()}
                          </div>
                        )}
                      </td>
                      <td>
                        {m.txn ? (
                          <div className="party-cell">
                            <span className="party-amt">{inr(m.txn.amount)}</span>
                            <span className="party-ref">{m.txn.bank_ref}</span>
                            <span className="party-meta">
                              {[m.txn.value_date ? fmtDay(m.txn.value_date) : null, m.txn.zone]
                                .filter(Boolean).join(' · ')}
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
                        <td colSpan={8}>
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
                <MoreRows remaining={drawnMatches.remaining} onMore={drawnMatches.more}
                          colSpan={8} noun="matches" />
              </tbody>
            </table>
            </div>
            ))}

            {activeTab === 'exceptions' && (allExceptions.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />} title="No exceptions">
                <span>Every credit and advised bill the runs have seen is accounted for.</span>
              </EmptyState>
            ) : exceptions.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />}
                          title={`Nothing matching ${[
                            excFilter.length ? `status ${excFilter.map(titleCase).join(' / ').toLowerCase()}` : null,
                            excTypeFilter.length ? `type ${excTypeFilter.map(typeLabel).join(' / ').toLowerCase()}` : null,
                            excGapFilter.length ? `gap ${excGapFilter.map(deSnake).join(' / ').toLowerCase()}` : null,
                            excOpenWork ? 'needs action' : null,
                          ].filter(Boolean).join(' · ') || 'these filters'}${runSet ? ' for the selected runs' : ''}`}>
                <TextLink onClick={clearExc}>Show all exceptions</TextLink>
              </EmptyState>
            ) : (
              <div className="ledger-wrap">
              <table className="ledger ledger-exceptions">
                <thead>
                  <tr>
                    <th>
                      Type
                      <ColumnFilter label="Type" value={excTypeFilter} onApply={setExcTypeFilter}
                                    format={typeLabel}
                                    options={buildOptions(allExceptions, (e) => e.exception_type)} />
                    </th>
                    <th>
                      Status
                      <ColumnFilter label="Status" value={excFilter} onApply={setExcFilter}
                                    format={titleCase}
                                    options={buildOptions(allExceptions, (e) => e.status)} />
                    </th>
                    <th>Reference</th>
                    <th>Zone</th>
                    <th>Date</th>
                    <th className="th-num">Amount</th>
                    <th>
                      Gap
                      <ColumnFilter label="Gap" value={excGapFilter} onApply={setExcGapFilter}
                                    format={deSnake}
                                    options={buildOptions(allExceptions, gapOf)} />
                    </th>
                    <th>First seen</th>
                    <th>Resolved by</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {drawnExc.shown.map((e) => {
                    const c = excCells(e)
                    const open = !!excOpen[e.id]
                    return (
                    <Fragment key={e.id}>
                    <tr className={`xq-row${open ? ' open' : ''}`}
                        onClick={() => setExcOpen((x) => ({ ...x, [e.id]: !x[e.id] }))}>
                      <td>
                        <ChevronRight className="chev chev-ic" size={14}
                                      strokeWidth={2} aria-hidden />
                        <span className={`ui-type type-${e.exception_type}`} title={typeLabel(e.exception_type)}>
                          {e.exception_type === 'BANK_ONLY' ? 'Credit'
                            : e.exception_type === 'BILL_ONLY' ? 'Bill' : deSnake(e.exception_type)}
                        </span>
                      </td>
                      <td><span className={`stamp stamp-${e.status}`}>{e.status}</span></td>
                      <td>
                        <div className="party-cell">
                          <span className="party-ref">{c.ref ?? '—'}</span>
                          {c.sub && <span className="party-meta">{c.sub}</span>}
                        </div>
                      </td>
                      <td>{c.zone ?? '—'}</td>
                      <td className="nowrap" title={c.date ?? undefined}>{c.date ? fmtDay(c.date) : '—'}</td>
                      <td className="num">{inr(c.amount)}</td>
                      <td className="exc-gap">{c.gap ?? '—'}</td>
                      <td className="run-cell" title={e.first_seen_run_id ?? undefined}>
                        {runLabelFor(runs, e.first_seen_run_id)}
                      </td>
                      <td className="run-cell"
                          title={[e.resolved_by, e.resolved_by_run_id ?? e.resolved_by_match_id,
                                  e.resolved_at ? fmtWhen(e.resolved_at) : null]
                                   .filter(Boolean).join(' · ') || undefined}>
                        {resolvedBy(e, runs)}
                      </td>
                      <td onClick={(ev) => ev.stopPropagation()}>
                        {e.status === 'OPEN' && (
                          <button type="button" className="ui-btn is-sm"
                                  title={e.exception_type === 'BANK_ONLY'
                                    ? 'pair this credit with open bill(s) by hand'
                                    : 'pair this bill with an open credit by hand'}
                                  onClick={() => setPicking(picking === e.id ? null : e.id)}>
                            {e.exception_type === 'BANK_ONLY' ? 'Match to bill…' : 'Match to credit…'}
                          </button>
                        )}
                      </td>
                    </tr>
                    {open && (
                      <tr className="xq-detail">
                        <td colSpan={10}>
                          {e.gap_action && <div className="detail-advice">{e.gap_action}</div>}
                          <div className="detail-grid">
                            {e.txn ? (
                              <>
                                <div className="detail-section">Bank credit — no bill behind it</div>
                                <div>
                                  <div className="dt-label">Narrative</div>
                                  <div className="dt-value">{e.txn.narrative || '—'}</div>
                                </div>
                              </>
                            ) : (
                              <>
                                <div className="detail-section">Bill — advised but no credit landed</div>
                                <div>
                                  <div className="dt-label">Submission ref</div>
                                  <div className="dt-value">{e.bill?.submission_ref ?? '—'}</div>
                                </div>
                                <div>
                                  <div className="dt-label">Bill status</div>
                                  <div className="dt-value">{e.bill?.bill_status ?? '—'}</div>
                                </div>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                    {picking === e.id && (
                      <tr className="xq-detail">
                        <td colSpan={10}>
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
                    )
                  })}
                  <MoreRows remaining={drawnExc.remaining} onMore={drawnExc.more}
                            colSpan={10} noun="exceptions" />
                </tbody>
              </table>
              </div>
            ))}

            <div className="ui-card-foot">
              {activeTab === 'matches'
                ? <>HIGH-confidence matches lock automatically; weaker ones wait here for accept or
                    reject. Rejecting releases the bills and re-opens the credit — Reopen undoes it
                    unless a later run has claimed either side.</>
                : <>Click a row for advice on what to do next. “Match to bill… / credit…” pairs an
                    open exception by hand, and the match is locked straight away.</>}
            </div>
          </section>
        </>
      )}
    </section>
  )
}
