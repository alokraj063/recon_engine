import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, Ban, CheckCircle2, ChevronRight, Clock3, Download, ListChecks, ListFilter, Lock,
} from 'lucide-react'
import {
  approveNonIrepsBulk, decideNonIreps, fetchLedger, fetchRun, fetchRuns, ledgerWorkbookUrl,
  type NonIrepsDecision,
} from '../api'
import { receiptKind, receiptKindLabel } from '../receiptKinds'
import {
  ApiError,
  type LedgerException, type LedgerMatch, type LedgerViewData, type Row,
  type RunListItem, type ZoneInfo,
} from '../types'
import { fmtDay, fmtWhen, inDayRange, inr, inrCompact, n, plural } from '../format'
import { BillLineage } from './BillLineage'
import { ConfidenceBadge } from './ConfidenceBadge'
import { MatchedEvidence, ReviewEvidence } from './ReviewEvidence'
import {
  DecidedBy, DecisionNote, MatchDecision, PickList, billByNumber, useMatchDecision, type DecisionResult,
} from './MatchDecision'
import { ManualMatchPicker } from './ManualMatchPicker'
import { loadSavedDateFilter, resolveWindow } from './DateFilter'
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
  DecisionDialog, HelpLabel,
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
  /** which tab to open on — the three exception tabs are excBucket's
   *  partition, so "exceptions" alone is the Open exceptions figure */
  section?: QueueTab
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
const QV_REVIEW: LedgerIntent = { section: 'review', matchStatus: [] }
const QV_SETTLED: LedgerIntent = { section: 'matches', matchStatus: ['LOCKED'] }
const QV_OPEN: LedgerIntent = { section: 'exceptions', excStatus: ['OPEN'], excType: [], excGap: [] }
const QV_AWAITING: LedgerIntent = { section: 'awaiting', excStatus: ['OPEN'], excType: [], excGap: [] }
const QV_NON_IREPS: LedgerIntent = { section: 'non_ireps', excStatus: ['OPEN'], excType: [], excGap: [] }

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
  // who, for a user resolution; older rows recorded no user
  const who = e.resolved_by_user ? ` · ${e.resolved_by_user}` : ''
  switch (e.resolved_by) {
    case 'USER_ACCEPT': return `Accepted${m ? ` ${m}` : ''}${who}`
    case 'USER_MANUAL': return `Manual match${m ? ` ${m}` : ''}${who}`
    case 'USER_REOPEN': return `Reopened${m ? ` ${m}` : ''}${who}`
    case 'USER_NON_IREPS': return `Approved non-IREPS${who}`
    default: return e.resolved_by_run_id ? `Run ${runLabelFor(runs, e.resolved_by_run_id)}` : '—'
  }
}

/** A bill as a row names it: its number, or — for a works-contract bill,
 *  which IREPS exports with no number ('-') — its CO6 reference. */
function billName(b: { bill_number: string | null; submission_ref?: string | null; gold_bill_id: string }): string {
  const num = (b.bill_number ?? '').trim()
  if (num && num !== '-') return num
  return b.submission_ref ? `CO6 ${b.submission_ref}` : b.gold_bill_id.slice(0, 8)
}

/** Awaiting data's "Bill status": the CURRENT status of the same-amount
 *  bill the credit waits on (server: db/overview.gap_bills). Awaiting bill
 *  data has no bill by definition. */
function awaitingBill(e: LedgerException): { text: string; sub?: string; title?: string } {
  const bills = e.gap_bills ?? []
  if (!bills.length) {
    return gapOf(e) === 'AWAITING_BILL_DATA'
      ? { text: 'No bill yet', title: 'The bill export covering this credit has not arrived' }
      : { text: '—' }
  }
  const [first, ...rest] = bills
  return {
    text: first.bill_status ?? '—',
    sub: `${first.bill_number ?? first.submission_ref ?? '—'}${rest.length ? ` +${rest.length}` : ''}`,
    title: bills.map((b) => `${b.bill_number ?? b.submission_ref ?? '—'}: ${b.bill_status ?? '—'}`).join('\n'),
  }
}

/** The BANK_ONLY gap code, derived exactly the way
 *  db/overview.unrecognised_clause derives it server-side: the stored
 *  code, or — on rows written before that column existed — a blank
 *  zone_guess, which is precisely what made the default mapping assign
 *  UNRECOGNISED_RECEIPT. Keeping the rules in step is what lets each
 *  Match performance row link to a queue filter selecting the same
 *  credits it counted. BILL_ONLY rows have no gap. */
export type ExcTab = 'exceptions' | 'awaiting' | 'non_ireps'
/** review = OPEN matches awaiting a decision; matches = the decided
 *  ones (LOCKED / REJECTED) — a split by status, so one match is in
 *  exactly one of them */
export type MatchTab = 'review' | 'matches'
export type QueueTab = MatchTab | ExcTab
const isMatchTab = (t: QueueTab): t is MatchTab => t === 'review' || t === 'matches'
const inMatchTab = (m: LedgerMatch, t: MatchTab) => (m.status === 'OPEN') === (t === 'review')

/** Which exceptions tab a row belongs to — one tab per row, the same
 *  partition db/overview counts with:
 *    non_ireps   other receipts (UNRECOGNISED_RECEIPT, or an analyst's
 *                NON_IREPS approval) — never matched, never in the rate
 *    awaiting    IREPS credits that could not have matched YET
 *    exceptions  the IREPS work: every bill-only row + unmatched credits
 *  An analyst's IREPS decision (reject) always keeps a row out of
 *  non_ireps, whatever the stored engine code says. */
export function excBucket(e: LedgerException): ExcTab {
  if (e.exception_type !== 'BANK_ONLY') return 'exceptions'
  if (e.source_decision === 'NON_IREPS' || e.resolved_by === 'USER_NON_IREPS') return 'non_ireps'
  const gap = gapOf(e) ?? ''
  if (e.source_decision !== 'IREPS' && gap === 'UNRECOGNISED_RECEIPT') return 'non_ireps'
  if (gap.startsWith('AWAITING_')) return 'awaiting'
  return 'exceptions'
}

const EXC_TABS: Array<{ key: ExcTab; label: string; noun: [string, string] }> = [
  { key: 'exceptions', label: 'Exceptions', noun: ['exception', 'exceptions'] },
  { key: 'awaiting', label: 'Awaiting data', noun: ['credit', 'credits'] },
  { key: 'non_ireps', label: 'Non-IREPS receipts', noun: ['receipt', 'receipts'] },
]

export function gapOf(e: LedgerException): string | null {
  if (e.exception_type !== 'BANK_ONLY') return null
  // the server's read-time reading wins: it is the only thing that can
  // tell the two awaiting buckets apart from a plain missing bill
  if (e.gap_detail) return e.gap_detail
  if (e.gap_type) return e.gap_type
  return e.txn?.zone ? 'SIGNAL_BILL_NOT_FOUND' : 'UNRECOGNISED_RECEIPT'
}

/** A row's segment cell: the directory's segment, the zone's full name
 *  on hover ("NR · Northern Railway · NORTH"). */
function SegmentCell({ z }: { z?: ZoneInfo | null }) {
  return (
    <td title={z ? [z.code, z.name, z.region].filter(Boolean).join(' · ') : undefined}>
      {z?.segment ?? '—'}
    </td>
  )
}
const segOf = (row: { zone_info?: ZoneInfo | null }) => row.zone_info?.segment ?? null

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
  const [excSegFilter, setExcSegFilter] = useState<string[]>([])
  // a non-IREPS decision in flight (exception id) + its error
  const [deciding, setDeciding] = useState<string | null>(null)
  // Non-IREPS tab: which narrative kinds are shown ([] = all), and
  // whether a bulk approval is running
  const [kindFilter, setKindFilter] = useState<string[]>([])
  const [bulking, setBulking] = useState(false)
  // the Command Center's date window — a separate filter from When (the
  // match's created_at), because the figures it mirrors are dated by the
  // credit / bill, not by the match row. It opens on the window saved on
  // the Command Center (every tab), a heading's intent overrides it, and
  // the chip clears it for this visit only
  const [savedWindow] = useState(() => resolveWindow(loadSavedDateFilter(customerId)))
  const [windowFrom, setWindowFrom] = useState(savedWindow.from)
  const [windowTo, setWindowTo] = useState(savedWindow.to)
  const [confFilter, setConfFilter] = useState<string[]>([])
  const [segFilter, setSegFilter] = useState<string[]>([])
  const [matchStatusFilter, setMatchStatusFilter] = useState<string[]>([])
  const [sortAsc, setSortAsc] = useState(false)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const scrolled = useRef<string | null>(null)
  // which table is showing. null = not chosen yet: the first load picks
  // the one with work in it (matches to review first)
  const [tab, setTab] = useState<QueueTab | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [evidence, setEvidence] = useState<Record<string, Evidence>>({})
  // the OPEN exception an analyst is pairing by hand (one picker at a time)
  const [picking, setPicking] = useState<string | null>(null)
  // the non-IREPS row whose decision is being made with a note
  const [noting, setNoting] = useState<LedgerException | null>(null)
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
    const m = data.matches.find((x) => x.id === focusId)
    setTab(m?.status === 'OPEN' ? 'review' : 'matches')
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
    const w = resolveWindow(loadSavedDateFilter(customerId))
    setWindowFrom(w.from)
    setWindowTo(w.to)
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
  // one filter set, shared by the three exception tabs; each tab shows
  // its own bucket of the filtered rows
  const filteredExc = allExceptions.filter(
    (e) => (excFilter.length === 0 || excFilter.includes(e.status))
      && (excTypeFilter.length === 0 || excTypeFilter.includes(e.exception_type))
      && (excGapFilter.length === 0 || excGapFilter.includes(facetKey(gapOf(e))))
      && (excSegFilter.length === 0 || excSegFilter.includes(facetKey(segOf(e))))
      && (!(windowFrom || windowTo) || inDayRange(excDay(e), windowFrom, windowTo))
      && (!runSet || (!!e.first_seen_run_id && runSet.has(e.first_seen_run_id))
          || (!!e.resolved_by_run_id && runSet.has(e.resolved_by_run_id))),
  )
  const byBucket = (rows: LedgerException[]) => {
    const out: Record<ExcTab, LedgerException[]> = { exceptions: [], awaiting: [], non_ireps: [] }
    for (const e of rows) out[excBucket(e)].push(e)
    return out
  }
  const byKind = (rows: LedgerException[]) => (kindFilter.length === 0 ? rows
    : rows.filter((e) => kindFilter.includes(receiptKind(e.txn?.narrative).key)))
  const excTabs = useMemo(() => byBucket(filteredExc),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, excFilter, excTypeFilter, excGapFilter, excSegFilter, windowFrom, windowTo, runSet])
  const allByTab = useMemo(() => byBucket(allExceptions),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data])

  const confidences = useMemo(() => {
    const present = new Set((data?.matches ?? []).map((m) => m.confidence))
    return CONFIDENCE_ORDER.filter((c) => present.has(c))
      .concat([...present].filter((c) => !CONFIDENCE_ORDER.includes(c)).sort())
  }, [data])

  const visibleMatches = useMemo(() => {
    const rows = (data?.matches ?? []).filter((m) => {
      if (confFilter.length && !confFilter.includes(m.confidence)) return false
      if (segFilter.length && !segFilter.includes(facetKey(segOf(m)))) return false
      if (matchStatusFilter.length && !matchStatusFilter.includes(m.status)) return false
      // a MANUAL match belongs to no run: a run filter hides it
      if (runSet && (!m.run_id || !runSet.has(m.run_id))) return false
      if (!inDayRange((m.txn?.value_date ?? '').slice(0, 10), dateFrom, dateTo)) return false
      if ((windowFrom || windowTo)
          && !inDayRange((m.txn?.value_date ?? '').slice(0, 10), windowFrom, windowTo)) return false
      return true
    })
    // by the credit's value date, newest first; ties by creation
    const key = (m: LedgerMatch) => `${(m.txn?.value_date ?? '').slice(0, 10)} ${m.created_at}`
    return rows.sort((a, b) => sortAsc
      ? key(a).localeCompare(key(b))
      : key(b).localeCompare(key(a)))
  }, [data, confFilter, segFilter, matchStatusFilter, runSet, dateFrom, dateTo, windowFrom, windowTo, sortAsc])

  const windowOn = !!(windowFrom || windowTo)
  const windowValues = windowOn ? [`${windowFrom || '…'} → ${windowTo || '…'}`] : []
  const clearWindow = () => { setWindowFrom(''); setWindowTo('') }
  const allMatches = data?.matches ?? []
  const matchChips: FilterChip[] = [
    { key: 'when', label: 'Date',
      values: dateFrom || dateTo ? [`${dateFrom || '…'} → ${dateTo || '…'}`] : [],
      onRemove: () => { setDateFrom(''); setDateTo('') } },
    { key: 'credit-date', label: 'Credit date', values: windowValues, onRemove: clearWindow },
    { key: 'confidence', label: 'Confidence', values: confFilter,
      onRemove: (v) => setConfFilter(v === undefined ? [] : confFilter.filter((x) => x !== v)) },
    { key: 'segment', label: 'Segment', values: segFilter,
      onRemove: (v) => setSegFilter(v === undefined ? [] : segFilter.filter((x) => x !== v)) },
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
    { key: 'exc-segment', label: 'Segment', values: excSegFilter,
      onRemove: (v) => setExcSegFilter(v === undefined ? [] : excSegFilter.filter((x) => x !== v)) },
    { key: 'exc-date', label: 'Date', values: windowValues, onRemove: clearWindow },
    { key: 'exc-kind', label: 'Kind', values: kindFilter, format: receiptKindLabel,
      onRemove: (v) => setKindFilter(v === undefined ? [] : kindFilter.filter((x) => x !== v)) },
  ]
  // with no tab chosen (sidebar arrival) the queue opens on the first
  // tab, Matches; links and quick views pick their own
  const activeTab: QueueTab = tab ?? 'matches'
  const matchTab: MatchTab = isMatchTab(activeTab) ? activeTab : 'matches'
  const reviewRows = visibleMatches.filter((m) => inMatchTab(m, 'review'))
  const decidedRows = visibleMatches.filter((m) => inMatchTab(m, 'matches'))
  const tabMatches = matchTab === 'review' ? reviewRows : decidedRows
  const tabAllMatches = allMatches.filter((m) => inMatchTab(m, matchTab))
  // the Run column earns its place only when the rows differ on it
  const showRun = new Set(tabMatches.map((m) => m.run_id)).size > 1
  const matchCols = showRun ? 12 : 11
  const drawnMatches = useProgressiveRows(tabMatches)
  const excTab: ExcTab = isMatchTab(activeTab) ? 'exceptions' : activeTab
  const exceptions = excTab === 'non_ireps' ? byKind(excTabs[excTab]) : excTabs[excTab]
  // the Non-IREPS tab drops the Gap column (every row carries the same one)
  const excCols = excTab === 'non_ireps' ? 9 : excTab === 'awaiting' ? 12 : 11
  // what a bulk approval would cover: the OPEN rows the filters leave
  const bulkRows = excTab === 'non_ireps'
    ? exceptions.filter((e) => e.status === 'OPEN') : []
  const tabAll = allByTab[excTab]
  const tabMeta = EXC_TABS.find((t) => t.key === excTab)!
  const drawnExc = useProgressiveRows(exceptions)
  // a focused match (arriving from the Exception queue / AR / Audit) may
  // sit past the first batch: draw down to it so its row exists to scroll to
  const focusIdx = focusId ? tabMatches.findIndex((m) => m.id === focusId) : -1
  const revealMatches = drawnMatches.reveal
  useEffect(() => { if (focusIdx >= 0) revealMatches(focusIdx + 1) }, [focusIdx, revealMatches])
  const runNote = data && runSet
    ? `${visibleMatches.length} of ${data.matches.length} matches`
    : undefined

  // quick-view figures — over the whole ledger, narrowed ONLY by the
  // Command Center's date window (so "3 open" there reads 3 here too);
  // the column filters never move them
  const inWinM = (m: LedgerMatch) => !windowOn
    || inDayRange((m.txn?.value_date ?? '').slice(0, 10), windowFrom, windowTo)
  const inWinE = (e: LedgerException) => !windowOn || inDayRange(excDay(e), windowFrom, windowTo)
  const toReview = allMatches.filter((m) => m.status === 'OPEN' && inWinM(m))
  const settled = allMatches.filter((m) => m.status === 'LOCKED' && inWinM(m))
  const openExc = allExceptions.filter((e) => e.status === 'OPEN' && inWinE(e))
  const needsAction = openExc.filter((e) => excBucket(e) === 'exceptions')
  const awaiting = openExc.filter((e) => excBucket(e) === 'awaiting')
  const nonIreps = openExc.filter((e) => excBucket(e) === 'non_ireps')
  const sumTxn = (ms: LedgerMatch[]) => ms.reduce((a, m) => a + (m.txn?.amount ?? 0), 0)
  const sumExc = (es: LedgerException[]) => es.reduce((a, e) => a + (excCells(e).amount ?? 0), 0)
  const needCredits = needsAction.filter((e) => e.exception_type === 'BANK_ONLY').length
  const needBills = needsAction.length - needCredits

  const empty = !!data && data.matches.length === 0 && data.exceptions.length === 0
  const chips = isMatchTab(activeTab) ? matchChips : excChips
  const anyChip = chips.some((c) => c.values.length > 0)
  const shown = isMatchTab(activeTab) ? tabMatches.length : exceptions.length
  const total = isMatchTab(activeTab) ? tabAllMatches.length : tabAll.length
  const clearExc = () => {
    setExcFilter([]); setExcTypeFilter([]); setExcGapFilter([]); setExcSegFilter([]); clearWindow()
    setKindFilter([]); setRunFilter(EMPTY_RUN_FILTER)
  }
  const approveShown = async (rows: LedgerException[]) => {
    setBulking(true)
    setError(null)
    try {
      await approveNonIrepsBulk(customerId, rows.map((e) => e.id))
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBulking(false)
    }
  }
  // gap codes are tab-specific (an awaiting code empties the Exceptions
  // tab), so a tab switched by hand drops the gap filter
  const openTab = (t: QueueTab) => {
    if (t !== activeTab && !isMatchTab(t)) setExcGapFilter([])
    // a non-IREPS receipt has no zone, so no segment either
    if (t === 'non_ireps') setExcSegFilter([])
    // the status filter would empty the other match tab (OPEN is the
    // whole of To review, never in Matches)
    if (isMatchTab(t) && t !== activeTab) setMatchStatusFilter([])
    setTab(t)
  }
  const decideSource = async (e: LedgerException, d: NonIrepsDecision, note?: string) => {
    setDeciding(e.id)
    setError(null)
    try {
      await decideNonIreps(e.id, d, note)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setDeciding(null)
    }
  }
  const clearMatches = () => {
    setConfFilter([]); setSegFilter([]); setMatchStatusFilter([]); setDateFrom(''); setDateTo(''); clearWindow()
    setRunFilter(EMPTY_RUN_FILTER)
  }

  return (
    <section className={`ui-page${empty ? '' : ' is-fill'}`}>
      <PageHeader title="Analyst queue"
                  context={customerName}>
        {runs.length > 0 && (
          <RunFilter runs={runs} value={runFilter} onChange={setRunFilter} note={runNote} />
        )}
        <RefreshButton onClick={load} label="Refresh the queue" />
        <ToolSep />
        <a className="ui-btn" href={ledgerWorkbookUrl(customerId)} download
           title="Download the match and exception ledger (.xlsx)">
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
            <Stat label={<><Lock size={12} strokeWidth={2} /> Settled</>} accent="settled"
                  value={n(settled.length)}
                  sub={`${plural(settled.length, 'match', 'matches')} · ${inrCompact(sumTxn(settled))}`}
                  onOpen={() => applyIntent(QV_SETTLED)}
                  title="Locked matches: automatic, accepted or manual" />
            <Stat label={<><ListChecks size={12} strokeWidth={2} /> To review</>}
                  accent="review" value={n(toReview.length)}
                  sub={toReview.length
                    ? `${plural(toReview.length, 'match', 'matches')} · ${inrCompact(sumTxn(toReview))}`
                    : 'None pending'}
                  onOpen={() => applyIntent(QV_REVIEW)}
                  title="Matches pending a decision" />
            <Stat label={<><AlertTriangle size={12} strokeWidth={2} /> Open exceptions</>}
                  accent="open" value={n(needsAction.length)}
                  sub={needsAction.length
                    ? `${n(needCredits)} ${plural(needCredits, 'credit', 'credits')} · ${n(needBills)} ${plural(needBills, 'bill', 'bills')} · ${inrCompact(sumExc(needsAction))}`
                    : 'None open'}
                  onOpen={() => applyIntent(QV_OPEN)}
                  title="Unmatched IREPS credits and bills requiring review" />
            <Stat label={<><Clock3 size={12} strokeWidth={2} /> Awaiting data</>} accent="awaiting"
                  value={n(awaiting.length)}
                  sub={awaiting.length
                    ? `${plural(awaiting.length, 'credit', 'credits')} · ${inrCompact(sumExc(awaiting))}`
                    : 'None'}
                  onOpen={() => applyIntent(QV_AWAITING)}
                  title="Credits pending source status or bill data" />
            <Stat label={<><Ban size={12} strokeWidth={2} /> Non-IREPS</>} accent="other"
                  value={n(nonIreps.length)}
                  sub={nonIreps.length
                    ? `${plural(nonIreps.length, 'receipt', 'receipts')} to confirm · ${inrCompact(sumExc(nonIreps))}`
                    : 'None to confirm'}
                  onOpen={() => applyIntent(QV_NON_IREPS)}
                  title="Receipts with no IREPS signal. Kept out of matching; approve or mark as IREPS." />
          </StatStrip>

          <section className="ui-card is-fill">
            <div className="ui-tabbar">
              <div className="ui-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={activeTab === 'matches'}
                        className={`ui-tab${activeTab === 'matches' ? ' is-on' : ''}`}
                        onClick={() => openTab('matches')}>
                  Matches <span className="ui-tab-count">{n(decidedRows.length)}</span>
                </button>
                <button type="button" role="tab" aria-selected={activeTab === 'review'}
                        className={`ui-tab${activeTab === 'review' ? ' is-on' : ''}`}
                        onClick={() => openTab('review')}>
                  To review <span className="ui-tab-count">{n(reviewRows.length)}</span>
                </button>
                {EXC_TABS.map((t) => (
                  <button key={t.key} type="button" role="tab" aria-selected={activeTab === t.key}
                          className={`ui-tab${activeTab === t.key ? ' is-on' : ''}`}
                          onClick={() => openTab(t.key)}>
                    {t.label} <span className="ui-tab-count">{n(excTabs[t.key].length)}</span>
                  </button>
                ))}
              </div>
              {bulkRows.length > 1 && (
                <button type="button" className="ui-btn is-sm tabbar-action" disabled={bulking}
                        title="Approve every open receipt currently listed as non-IREPS"
                        onClick={() => void approveShown(bulkRows)}>
                  Approve {n(bulkRows.length)} shown
                </button>
              )}
              {shown !== total && (
                <span className="ui-tabbar-note">
                  {n(shown)} of {n(total)}{' '}
                  {isMatchTab(activeTab)
                    ? plural(total, 'match', 'matches') : plural(total, ...tabMeta.noun)}
                  <TextLink onClick={isMatchTab(activeTab) ? clearMatches : clearExc}>Show all</TextLink>
                </span>
              )}
            </div>
            {anyChip && (
              <div className="ui-filterbar"><FilterChips chips={chips} /></div>
            )}


            {isMatchTab(activeTab) && (tabAllMatches.length === 0 ? (
              matchTab === 'review' ? (
                <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />} title="Nothing to review" />
              ) : (
                <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />} title="No matches">
                  <span>Matches are created by incremental reconciliation runs.</span>
                </EmptyState>
              )
            ) : (
            <div className="ledger-wrap is-fill">
            <table className="ledger ledger-matches">
              <thead>
                <tr>
                  <th><HelpLabel k="ui:match">Match</HelpLabel></th>
                  <th>
                    <HelpLabel k="ui:status">Status</HelpLabel>
                    <ColumnFilter label="Status" value={matchStatusFilter} onApply={setMatchStatusFilter}
                                  format={titleCase}
                                  options={buildOptions(tabAllMatches, (m) => m.status)} />
                  </th>
                  <th><HelpLabel k="bank_ref">Reference</HelpLabel></th>
                  <th><HelpLabel k="zone">Zone</HelpLabel></th>
                  <th>
                    <HelpLabel k="ui:segment">Segment</HelpLabel>
                    <ColumnFilter label="Segment" value={segFilter} onApply={setSegFilter}
                                  options={buildOptions(tabAllMatches, segOf)} />
                  </th>
                  <th className="th-sort" onClick={() => setSortAsc((v) => !v)}>
                    <HelpLabel k="value_date">Date</HelpLabel> {sortAsc ? '▲' : '▼'}
                    <DateRangeFilter from={dateFrom} to={dateTo}
                                     onChange={(f, t) => { setDateFrom(f); setDateTo(t) }} />
                  </th>
                  <th className="th-num"><HelpLabel k="amount">Amount</HelpLabel></th>
                  <th><HelpLabel k="ui:bills">Bill</HelpLabel></th>
                  <th>
                    <HelpLabel k="confidence">Confidence</HelpLabel>
                    <ColumnFilter label="Confidence" value={confFilter} onApply={setConfFilter}
                                  options={buildOptions(tabAllMatches, (m) => m.confidence)
                                    .sort((a, b) => confidences.indexOf(a.value) - confidences.indexOf(b.value))} />
                  </th>
                  <th><HelpLabel k="ui:decided_by">Decided by</HelpLabel></th>
                  {showRun && <th><HelpLabel k="ui:run">Run</HelpLabel></th>}
                  <th />
                </tr>
              </thead>
              <tbody>
                {tabMatches.length === 0 && (
                  <tr>
                    <td colSpan={matchCols} className="ui-table-empty">
                      No matches for the current filters.{' '}
                      <TextLink onClick={clearMatches}>Clear filters</TextLink>
                    </td>
                  </tr>
                )}
                {drawnMatches.shown.map((m) => {
                  const picked = m.bills.filter((b) => b.role === 'picked')
                  const candidates = m.bills.length - picked.length
                  // the credit and its bills agree to the rupee on a normal
                  // match; only a gap (a manual or batched match) is flagged
                  const billTotal = picked.reduce((a, b) => a + (b.net_payable_amount ?? 0), 0)
                  const variance = m.txn && picked.length
                    && Math.abs((m.txn.amount ?? 0) - billTotal) >= 1
                    ? (m.txn.amount ?? 0) - billTotal : 0
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
                      <td className="mono nowrap" title={`Created ${fmtWhen(m.created_at)} · run label ${m.match_id}`}>
                        <ChevronRight className="chev chev-ic" size={14}
                                      strokeWidth={2} aria-hidden />
                        {m.seq !== null ? `M-${m.seq}` : m.match_id}
                      </td>
                      <td><span className={`stamp stamp-${m.status}`}>{m.status}</span></td>
                      <td><span className="party-ref">{m.txn?.bank_ref ?? '—'}</span></td>
                      <td>{m.txn?.zone ?? '—'}</td>
                      <SegmentCell z={m.zone_info} />
                      <td className="nowrap">{m.txn?.value_date ? fmtDay(m.txn.value_date) : '—'}</td>
                      <td className="num" title={variance ? `Bills total ${inr(billTotal)} · variance ${inr(variance)}` : undefined}>
                        {inr(m.txn?.amount ?? null)}
                        {variance ? <span className="flag-note"> ⚠</span> : null}
                      </td>
                      <td className="nowrap">
                        <span className="party-ref">
                          {picked.map(billName).join(', ') || '—'}
                        </span>
                        {candidates > 0 && (
                          <span className="chip-note"> +{candidates}</span>
                        )}
                      </td>
                      <td><ConfidenceBadge label={m.confidence} /></td>
                      <td className="nowrap">
                        <DecidedBy match={m} />
                      </td>
                      {showRun && (
                        <td className="run-cell" title={m.run_id ?? 'matched by user — no creating run'}>
                          {m.run_id ? runLabelFor(runs, m.run_id) : <span className="chip-note">manual</span>}
                        </td>
                      )}
                      <td onClick={(e) => e.stopPropagation()}>
                        <MatchDecision match={m} busy={!!busy[m.id]} decide={decide} />
                      </td>
                    </tr>
                    {expanded[m.id] && (
                      <tr className="xq-detail">
                        <td colSpan={matchCols}>
                          <DecisionNote match={m} />
                          {ev === 'loading' || ev === undefined ? (
                            <p className="frame-note"><span className="quill" /> loading evidence…</p>
                          ) : ev === 'missing' || ev === 'manual' ? (
                            <div className="detail-grid">
                              <div className="detail-section">
                                {ev === 'manual'
                                  ? 'Manual match. No engine evidence is available.'
                                  : 'Evidence unavailable. Ledger summary:'}
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
                                      ? (no, note) => {
                                          const b = billByNumber(m, no)
                                          if (b) decide(m.id, 'accept', b.gold_bill_id, note)
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
                          colSpan={matchCols} noun="matches" />
              </tbody>
            </table>
            </div>
            ))}

            {!isMatchTab(activeTab) && (tabAll.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />}
                          title={`No ${tabMeta.label.toLowerCase()}`}>
              </EmptyState>
            ) : exceptions.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />}
                          title={`No ${tabMeta.noun[1]} match ${[
                            excFilter.length ? `status ${excFilter.map(titleCase).join(' / ').toLowerCase()}` : null,
                            excTypeFilter.length ? `type ${excTypeFilter.map(typeLabel).join(' / ').toLowerCase()}` : null,
                            excGapFilter.length ? `gap ${excGapFilter.map(deSnake).join(' / ').toLowerCase()}` : null,
                            excSegFilter.length ? `segment ${excSegFilter.join(' / ')}` : null,
                          ].filter(Boolean).join(' · ') || 'these filters'}${runSet ? ' for the selected runs' : ''}`}>
                <TextLink onClick={clearExc}>Clear filters</TextLink>
              </EmptyState>
            ) : (
              <div className="ledger-wrap is-fill">
              <table className="ledger ledger-exceptions">
                <thead>
                  <tr>
                    <th>
                      <HelpLabel k="exception_type">Type</HelpLabel>
                      <ColumnFilter label="Type" value={excTypeFilter} onApply={setExcTypeFilter}
                                    format={typeLabel}
                                    options={buildOptions(tabAll, (e) => e.exception_type)} />
                    </th>
                    <th>
                      <HelpLabel k="ui:exc_status">Status</HelpLabel>
                      <ColumnFilter label="Status" value={excFilter} onApply={setExcFilter}
                                    format={titleCase}
                                    options={buildOptions(tabAll, (e) => e.status)} />
                    </th>
                    <th><HelpLabel k="ui:reference">Reference</HelpLabel></th>
                    {/* a non-IREPS receipt has no zone by definition — its
                        narrative is what an analyst decides on */}
                    <th>
                      {excTab === 'non_ireps'
                        ? <HelpLabel k="ui:narrative">Narrative</HelpLabel>
                        : <HelpLabel k="zone">Zone</HelpLabel>}
                      {excTab === 'non_ireps' && (
                        <ColumnFilter label="Kind" value={kindFilter} onApply={setKindFilter}
                                      format={receiptKindLabel}
                                      options={buildOptions(tabAll, (e) => receiptKind(e.txn?.narrative).key)} />
                      )}
                    </th>
                    {excTab !== 'non_ireps' && (
                      <th>
                        <HelpLabel k="ui:segment">Segment</HelpLabel>
                        <ColumnFilter label="Segment" value={excSegFilter} onApply={setExcSegFilter}
                                      options={buildOptions(tabAll, segOf)} />
                      </th>
                    )}
                    <th><HelpLabel k="ui:date">Date</HelpLabel></th>
                    <th className="th-num"><HelpLabel k="amount">Amount</HelpLabel></th>
                    {excTab !== 'non_ireps' && (
                      <th>
                        <HelpLabel k="ui:gap">Gap</HelpLabel>
                        <ColumnFilter label="Gap" value={excGapFilter} onApply={setExcGapFilter}
                                      format={deSnake}
                                      options={buildOptions(tabAll, gapOf)} />
                      </th>
                    )}
                    {excTab === 'awaiting' && (
                      <th><HelpLabel k="ui:bill_status">Bill status</HelpLabel></th>
                    )}
                    <th><HelpLabel k="ui:first_seen">First seen</HelpLabel></th>
                    <th><HelpLabel k="ui:resolved_by">Resolved by</HelpLabel></th>
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
                      {excTab === 'non_ireps' ? (
                        <td className="exc-narrative" title={e.txn?.narrative || undefined}>
                          {e.txn?.narrative || '—'}
                        </td>
                      ) : (
                        <td>{c.zone ?? '—'}</td>
                      )}
                      {excTab !== 'non_ireps' && <SegmentCell z={e.zone_info} />}
                      <td className="nowrap" title={c.date ?? undefined}>{c.date ? fmtDay(c.date) : '—'}</td>
                      <td className="num">{inr(c.amount)}</td>
                      {excTab !== 'non_ireps' && <td className="exc-gap">{c.gap ?? '—'}</td>}
                      {excTab === 'awaiting' && (() => {
                        const ab = awaitingBill(e)
                        return (
                          <td className="nowrap" title={ab.title}>
                            <div className="party-cell">
                              <span className="party-meta">{ab.text}</span>
                              {ab.sub && <span className="party-ref">{ab.sub}</span>}
                            </div>
                          </td>
                        )
                      })()}
                      <td className="run-cell" title={e.first_seen_run_id ?? undefined}>
                        {runLabelFor(runs, e.first_seen_run_id)}
                      </td>
                      <td className="run-cell"
                          title={[e.resolved_by, e.resolved_by_run_id ?? e.resolved_by_match_id,
                                  e.resolved_at ? fmtWhen(e.resolved_at) : null]
                                   .filter(Boolean).join(' · ') || undefined}>
                        {resolvedBy(e, runs)}
                      </td>
                      <td onClick={(ev) => ev.stopPropagation()} className="nowrap">
                        {excTab === 'non_ireps' && e.status === 'OPEN' && (
                          <>
                            <button type="button" className="ui-btn is-sm" disabled={deciding === e.id}
                                    title="Confirm as a non-IREPS receipt. Closes it; never matched."
                                    onClick={() => decideSource(e, 'approve')}>
                              Approve
                            </button>{' '}
                            <button type="button" className="ui-btn is-sm" disabled={deciding === e.id}
                                    title="Not non-IREPS: move to IREPS exceptions and include in matching"
                                    onClick={() => decideSource(e, 'reject')}>
                              Reject
                            </button>{' '}
                            <button type="button" className="decide-note" disabled={deciding === e.id}
                                    title="Approve or reject with a note" onClick={() => setNoting(e)}>
                              + note
                            </button>
                          </>
                        )}
                        {excTab === 'non_ireps' && e.resolved_by === 'USER_NON_IREPS' && (
                          <button type="button" className="ui-btn is-sm" disabled={deciding === e.id}
                                  title="Undo the approval and reopen"
                                  onClick={() => decideSource(e, 'undo')}>
                            Undo
                          </button>
                        )}
                        {excTab !== 'non_ireps' && e.status === 'OPEN' && (
                          <button type="button" className="ui-btn is-sm"
                                  title={e.exception_type === 'BANK_ONLY'
                                    ? 'Match this credit to open bills'
                                    : 'Match this bill to an open credit'}
                                  onClick={() => setPicking(picking === e.id ? null : e.id)}>
                            {e.exception_type === 'BANK_ONLY' ? 'Match to bill…' : 'Match to credit…'}
                          </button>
                        )}
                      </td>
                    </tr>
                    {open && (
                      <tr className="xq-detail">
                        <td colSpan={excCols}>
                          <div className="xd">
                          {e.gap_action && <p className="xd-advice">{e.gap_action}</p>}
                          {e.source_decision === 'IREPS' && e.status === 'OPEN' && (
                            <p className="xd-advice">
                              Marked as IREPS.{' '}
                              <TextLink onClick={() => decideSource(e, 'undo')}>Undo</TextLink>
                            </p>
                          )}
                          {(e.source_note || e.source_decided_by) && e.source_decision && (
                            <div className="decision-note-block">
                              <div className="dt-label">
                                {e.source_decision === 'NON_IREPS' ? 'Approved as non-IREPS' : 'Marked as IREPS'}
                                {e.source_decided_by ? ` · ${e.source_decided_by}` : ''}
                              </div>
                              {e.source_note && <p className="decision-note-text">{e.source_note}</p>}
                            </div>
                          )}
                          {/* one label/value line each, values at full width — the
                              row above already carries ref, zone, date and amount */}
                          <dl className="xd-list">
                            {e.txn ? (
                              <>
                                <div>
                                  <dt>Narrative</dt>
                                  <dd className="xd-mono">{e.txn.narrative || '—'}</dd>
                                </div>
                                {(e.gap_bills ?? []).length > 0 && (
                                  <div>
                                    <dt>Same-amount bills</dt>
                                    <dd className="xd-chips">
                                      {(e.gap_bills ?? []).map((b) => (
                                        <span key={b.gold_bill_id} className="xd-bill">
                                          <span className="xd-mono">{b.bill_number ?? b.submission_ref ?? '—'}</span>
                                          <span className="xd-bill-status">{b.bill_status ?? '—'}</span>
                                          {b.bill_date && <span>{fmtDay(b.bill_date)}</span>}
                                        </span>
                                      ))}
                                    </dd>
                                  </div>
                                )}
                              </>
                            ) : (
                              <>
                                <div>
                                  <dt>Submission ref</dt>
                                  <dd className="xd-mono">{e.bill?.submission_ref ?? '—'}</dd>
                                </div>
                                <div>
                                  <dt>Bill status</dt>
                                  <dd>{e.bill?.bill_status ?? '—'}</dd>
                                </div>
                              </>
                            )}
                          </dl>
                          </div>
                        </td>
                      </tr>
                    )}
                    {picking === e.id && (
                      <tr className="xq-detail">
                        <td colSpan={excCols}>
                          <ManualMatchPicker
                            customerId={customerId}
                            anchor={e}
                            candidates={allExceptions.filter((x) => x.status === 'OPEN'
                              && excBucket(x) !== 'non_ireps'
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
                            colSpan={excCols} noun="exceptions" />
                </tbody>
              </table>
              </div>
            ))}

          </section>
        </>
      )}
      {noting && (
        <DecisionDialog
          title={`Decide ${noting.txn?.bank_ref ?? 'receipt'}`}
          message="Approve closes it as a non-IREPS receipt. Reject moves it to IREPS exceptions."
          busy={deciding === noting.id}
          onClose={() => setNoting(null)}
          actions={[
            { label: 'Reject', tone: 'danger',
              run: (note) => { const e = noting; setNoting(null); void decideSource(e, 'reject', note) } },
            { label: 'Approve', tone: 'primary',
              run: (note) => { const e = noting; setNoting(null); void decideSource(e, 'approve', note) } },
          ]} />
      )}
    </section>
  )
}
