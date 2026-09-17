import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle, ArrowRight, ChevronDown, ChevronRight, Clock3, GitMerge, Inbox, ListChecks,
  RotateCw, Search, Upload,
} from 'lucide-react'
import type { CustomerInfo, Overview, OverviewException } from '../types'
import { fetchOperatingUnits, fetchOverview } from '../api'
import { inr, inrCompact } from '../format'
import type { View } from './Sidebar'
import type { LedgerIntent } from './LedgerView'
import type { GoldIntent } from './GoldTable'
import {
  DEFAULT_DATE_FILTER, DateFilter, resolveWindow, windowLabel,
  type DateFilterValue,
} from './DateFilter'
import { RecentActivity } from './RecentActivity'

const FILTER_KEY = (customer: string) => `recon.cc.filter.${customer}`

function loadFilter(customer: string): DateFilterValue {
  try {
    const raw = localStorage.getItem(FILTER_KEY(customer))
    if (raw) return { ...DEFAULT_DATE_FILTER, ...JSON.parse(raw) }
  } catch { /* fall through */ }
  return DEFAULT_DATE_FILTER
}

interface Props {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onNavigate: (v: View) => void
  /** open the Analyst queue already filtered to what the heading names */
  onOpenQueue: (intent: LedgerIntent) => void
  /** open a Data page (Current scope) already filtered to the figure */
  onOpenGold: (intent: GoldIntent) => void
  refreshKey: number
}

/* The presets each figure carries into the Analyst queue. They are
   plain filter values, so each one arrives as a removable chip there —
   the analyst sees WHY the table is narrowed. `[]` clears a filter. */
const Q_SETTLED: LedgerIntent = { section: 'matches', matchStatus: ['LOCKED'] }
const Q_REVIEW: LedgerIntent = { section: 'matches', matchStatus: ['OPEN'] }
const Q_ALL_MATCHES: LedgerIntent = { section: 'matches', matchStatus: [] }
/* Open exceptions are the work that needs an analyst (db/overview.
   open_in_scope): other receipts can never match a bill and credits
   awaiting data cannot have matched yet, so each is counted once
   elsewhere ("other receipts", "waiting on data") and never here. excGap
   is always set so a gap chip left over from an earlier arrival cannot
   narrow these. */
const Q_OPEN_EXC: LedgerIntent = {
  section: 'exceptions', excStatus: ['OPEN'], excType: [], excGap: [], excOpenWork: true,
}
/** the unmatched credits — the credit half of Open exceptions */
const Q_UNMATCHED: LedgerIntent = {
  section: 'exceptions', excStatus: ['OPEN'], excType: ['BANK_ONLY'],
  excGap: ['SIGNAL_BILL_NOT_FOUND'], excOpenWork: false,
}
/** the IREPS credits the rate excuses: bill still in flight, or its
 *  export not ingested yet */
const Q_AWAITING: LedgerIntent = {
  section: 'exceptions', excStatus: ['OPEN'], excType: ['BANK_ONLY'],
  excGap: ['AWAITING_STATUS', 'AWAITING_BILL_DATA'], excOpenWork: false,
}
const Q_RESOLVED_EXC: LedgerIntent = {
  section: 'exceptions', excStatus: ['RESOLVED'], excType: [], excGap: [], excOpenWork: false,
}
/** open exceptions of ONE side — "N credits" / "M bills" */
const qExcSide = (side: string): LedgerIntent =>
  ({ section: 'exceptions', excStatus: ['OPEN'], excType: [side], excGap: [], excOpenWork: true })
/* Open bank-only exceptions narrowed to ONE gap code (LedgerView.gapOf).
   Each figure below owns a distinct code, so a link opens exactly the
   credits it counted. Two of the four are stored on the exception
   (UNRECOGNISED_RECEIPT | SIGNAL_BILL_NOT_FOUND); the awaiting pair is
   computed per row by db/overview.gap_details and ridden to the client
   as `gap_detail` — without that they collapse into
   SIGNAL_BILL_NOT_FOUND and all three land on the same table. */
const qExcGap = (gap: string): LedgerIntent =>
  ({ section: 'exceptions', excStatus: ['OPEN'], excType: ['BANK_ONLY'],
     excGap: [gap], excOpenWork: false })

/* The same, for figures whose rows live in the gold bank table. The
   credit funnel levels are the read-time `credit_scope` column
   (db/overview.credit_scopes), whose codes partition every credit
   exactly the way the overview counts them. */
const G_CREDITS: GoldIntent = { frame: 'bank', filters: { used_in_recon: ['true'] } }
const G_IN_SCOPE: GoldIntent = {
  frame: 'bank',
  filters: { credit_scope: ['RECOGNISED', 'AWAITING_STATUS', 'AWAITING_BILL_DATA'] },
}
const G_RECOGNISED: GoldIntent = { frame: 'bank', filters: { credit_scope: ['RECOGNISED'] } }
/** every bill in the window — the Gold pool figure */
const G_BILLS: GoldIntent = { frame: 'bills', filters: {} }

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
const n = (v: number) => v.toLocaleString('en-IN')
const plural = (v: number, one: string, many: string) => (v === 1 ? one : many)

const DAY = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
/** yyyy-mm-dd -> "21 Aug 2026" (parsed as a calendar day, no timezone shift) */
function fmtDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return y && m && d ? DAY.format(new Date(y, m - 1, d)) : iso
}

/** whole days from a yyyy-mm-dd date to the data's own "today" */
function ageDays(date: string, asOf: string): number | null {
  const a = Date.parse(date), b = Date.parse(asOf)
  if (Number.isNaN(a) || Number.isNaN(b)) return null
  return Math.max(0, Math.round((b - a) / 86_400_000))
}
const ageText = (d: number) => (d === 0 ? 'today' : `${d} ${plural(d, 'day', 'days')}`)

function greeting(): string {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

/** A small inline link — teal, so it always reads as one. */
function Link({ children, onClick, title, quiet }: {
  children: ReactNode; onClick: () => void; title?: string
  /** keeps the surrounding text colour until hovered */
  quiet?: boolean
}) {
  return (
    <button type="button" className={`cc-link${quiet ? ' is-quiet' : ''}`}
            onClick={onClick} title={title}>
      {children}
    </button>
  )
}

/* One task in the inbox. The Open button is stretched over the card, so
   the whole card is one target while staying a real, focusable button. */
function TaskCard({ tone, icon, title, meta, age, onOpen, action = 'Open' }: {
  tone: 'review' | 'credit' | 'bill'
  icon: ReactNode
  title: ReactNode
  meta: ReactNode
  /** days old; omitted for tasks without a date */
  age?: number | null
  onOpen: () => void
  action?: string
}) {
  return (
    <li className={`cc-task tone-${tone}`}>
      <span className="cc-task-icon">{icon}</span>
      <div className="cc-task-body">
        <div className="cc-task-title">{title}</div>
        <div className="cc-task-meta">{meta}</div>
      </div>
      {age !== undefined && (
        <span className={`cc-age${age !== null && age > 30 ? ' is-late' : ''}`}
              title={age !== null && age > 30 ? 'older than 30 days' : undefined}>
          {age === null ? '—' : ageText(age)}
        </span>
      )}
      <button type="button" className="cc-task-open" onClick={onOpen}>
        {action} <ArrowRight size={14} strokeWidth={2} />
      </button>
    </li>
  )
}

/** One row of the scorecard's definition list — the whole row is a link. */
function ScoreRow({ label, value, sub, onOpen, muted, title }: {
  label: string; value: ReactNode; sub?: ReactNode; onOpen: () => void
  muted?: boolean; title?: string
}) {
  return (
    <button type="button" className={`cc-dl-row${muted ? ' is-muted' : ''}`}
            onClick={onOpen} title={title}>
      <span className="cc-dl-label">{label}</span>
      <span className="cc-dl-value">
        <span>{value}</span>
        {sub && <span className="cc-dl-sub">{sub}</span>}
      </span>
      <ChevronRight className="cc-dl-chev" size={14} strokeWidth={2} />
    </button>
  )
}

function Skeleton() {
  return (
    <div className="cc-layout" aria-busy="true" aria-label="Loading overview">
      <div className="cc-inbox">
        {Array.from({ length: 4 }, (_, i) => <div key={i} className="sk" style={{ height: 68 }} />)}
      </div>
      <div className="cc-card sk" style={{ minHeight: 420 }} />
    </div>
  )
}

export function CommandCenter({
  customers, customerId, onCustomerChange, onNavigate, onOpenQueue, onOpenGold, refreshKey,
}: Props) {
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [waitingOpen, setWaitingOpen] = useState(false)
  // date window + operating units (item 2.1), remembered per customer
  const [filter, setFilter] = useState<DateFilterValue>(() => loadFilter(customerId))
  const [units, setUnits] = useState<string[]>([])
  const [unitCounts, setUnitCounts] = useState<Record<string, number>>({})

  useEffect(() => { setFilter(loadFilter(customerId)) }, [customerId])
  useEffect(() => {
    try { localStorage.setItem(FILTER_KEY(customerId), JSON.stringify(filter)) } catch { /* ignore */ }
  }, [customerId, filter])
  useEffect(() => {
    fetchOperatingUnits(customerId)
      .then((r) => {
        setUnits(r.units.map((u) => u.unit))
        setUnitCounts(Object.fromEntries(r.units.map((u) => [u.unit, u.bills])))
      })
      .catch(() => setUnits([]))
  }, [customerId, refreshKey])

  const load = useCallback(() => {
    setError(null)
    setLoading(true)
    const win = resolveWindow(filter)
    fetchOverview(customerId, {
      from: win.from || undefined, to: win.to || undefined,
      operating_units: filter.units ?? undefined,
    })
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false))
  }, [customerId, filter])

  useEffect(load, [load, refreshKey])
  const filtered = !!data?.filters_applied
  const quiet = filtered && data && data.gold.credits === 0 && data.gold.bills === 0
  const customerName = customers.find((c) => c.key === customerId)?.name ?? customerId

  const credits = data?.gold.credits ?? 0
  // unrecognised receipts (no match signal) are not matchable: every
  // performance figure is over the RECOGNISED credits. This IS
  // out_of_scope_credits — shown once, as "Other receipts"
  const unrecognised = data?.unrecognised_credits ?? 0
  // the matching bill is still in flight in the source system, so the
  // credit could not have matched — reported, not rated
  const awaitingStatus = data?.awaiting_status_credits ?? 0
  // the bill export covering this credit's advice has not been ingested
  // yet — excused only until it goes stale (server-side cap)
  const awaitingBillData = data?.awaiting_bill_data_credits ?? 0
  const awaiting = awaitingStatus + awaitingBillData
  const billOnly = data?.open_in_scope.bill_only ?? 0
  const openCreditValue = Math.max(0,
    (data?.open_in_scope.value ?? 0) - (data?.open_value.bill_only ?? 0))
  const recognised = data?.recognised_credits
    ?? Math.max(0, credits - unrecognised - awaitingStatus - awaitingBillData)
  // every credit is either this source's money (IREPS) or a receipt from
  // somewhere else; the rate's denominator is carved out of the first
  const inScope = data?.in_scope_credits ?? Math.max(0, credits - unrecognised)
  const outOfScope = data?.out_of_scope_credits ?? unrecognised
  const unmatched = data ? Math.max(0, recognised - data.matched_credits) : 0
  const settled = data?.settled_credits ?? data?.matches.LOCKED ?? 0
  const manual = data?.manual_matches ?? 0
  // locked_by.USER counts every user-locked match, manual ones included
  const accepted = Math.max(0, (data?.locked_by.USER ?? 0) - manual)
  // a CREDIT count — matches.OPEN is a MATCH count
  const inReview = data ? Math.max(0, data.matched_credits - settled) : 0

  // Every figure routes to where it actually LIVES. Every number on this
  // page comes from /api/overview — live gold + ledger state — so the
  // targets are the Analyst queue and the Data pages' CURRENT scope
  // (gold_*), never a run-scoped frame, which may be empty or from an
  // unrelated run. Opening a gold page deliberately does not touch the
  // user's recon.dataScope preference (App owns that, and only the scope
  // switch itself writes it).

  /** Every trip to the queue carries this page's date window, so the
   *  table lands on the same credits/bills the figure counted (the queue
   *  applies it on the dates db/overview uses). "All time" sends '' and
   *  clears any window left over from an earlier visit. Operating units
   *  are NOT carried: the ledger payload has no unit to filter on. */
  const openQueue = (intent: LedgerIntent) => {
    const win = resolveWindow(filter)
    onOpenQueue({ ...intent, from: win.from, to: win.to })
  }

  /** The same for a Data page: the window rides along, applied on the
   *  date db/overview counted that frame with (GoldTable.DATE_FIELD). */
  const openGold = (intent: GoldIntent) => {
    const win = resolveWindow(filter)
    onOpenGold({ ...intent, from: win.from, to: win.to })
  }

  /* ONE figure, ONE place. The credit funnel as db/overview.py computes it
       credits       = other receipts + IREPS credits
       IREPS credits = settled + in review + unmatched + awaiting data
       recognised    = settled + in review + unmatched   (the rate's base)
     The inbox owns the WORK (review, open exceptions, waiting on data);
     the scorecard owns the STATE (rate, settled, what came in, the pool).
     The header's "N items need you" is their sum, never shown elsewhere. */
  const asOf = data?.data_as_of ?? new Date().toISOString().slice(0, 10)
  const needYou = data ? data.matches.OPEN + data.open_in_scope.count : 0
  const more = data ? Math.max(0, data.open_in_scope.count - data.top_exceptions.length) : 0
  const rated = (c: number) => (recognised > 0 ? (c / recognised) * 100 : 0)

  const exceptionTask = (e: OverviewException) => {
    const credit = e.exception_type === 'BANK_ONLY'
    const age = e.date ? ageDays(e.date, asOf) : null
    return (
      <TaskCard key={e.id} tone={credit ? 'credit' : 'bill'}
        icon={credit ? <Search size={16} strokeWidth={2} /> : <AlertTriangle size={16} strokeWidth={2} />}
        title={<>
          {credit ? 'Find the bill for credit ' : 'Find the payment for bill '}
          <span className="cc-ref">{e.ref ?? '—'}</span>
        </>}
        meta={<>
          <span className="cc-task-amt" title={inr(e.amount)}>{inrCompact(e.amount)}</span>
          {e.zone && <><span className="cc-dot" />{e.zone}</>}
          {e.date && <><span className="cc-dot" />{credit ? 'valued' : 'advised'} {fmtDay(e.date)}</>}
        </>}
        age={age}
        onOpen={() => openQueue(qExcSide(e.exception_type))} />
    )
  }

  return (
    <section className="cc-page">
      <header className="cc-head">
        <div className="cc-head-title">
          <span className="cc-eyebrow">Command Center</span>
          <h2 className="page-title">{greeting()}</h2>
          <p className="cc-context">
            {data
              ? needYou > 0
                ? <strong>{n(needYou)} {plural(needYou, 'item needs', 'items need')} you</strong>
                : <strong className="is-clear">You’re all caught up</strong>
              : 'Loading your work…'}
            <span className="cc-dot" />{customerName}
            {data?.data_as_of && <><span className="cc-dot" />data through {fmtDay(data.data_as_of)}</>}
          </p>
        </div>
        <div className="cc-head-tools">
          <DateFilter value={filter} onChange={setFilter} units={units} unitCounts={unitCounts} />
          {customers.length > 1 && (
            <label className="cc-customer">
              <span>Customer</span>
              <select value={customerId} onChange={(e) => onCustomerChange(e.target.value)}>
                {customers.map((c) => (
                  <option key={c.key} value={c.key}>{c.name} ({c.key})</option>
                ))}
              </select>
            </label>
          )}
          <button type="button" className="cc-icon-btn" onClick={load}
                  title="Refresh figures" aria-label="Refresh figures">
            <RotateCw size={15} strokeWidth={1.75} className={loading ? 'spin' : undefined} />
          </button>
          <span className="cc-head-sep" />
          <button type="button" className="cc-btn" onClick={() => onNavigate('ingest')}>
            <Upload size={15} strokeWidth={1.75} /> Ingest
          </button>
          <button type="button" className="cc-btn cc-btn-primary" onClick={() => onNavigate('reconcile')}>
            <GitMerge size={15} strokeWidth={1.75} /> Reconcile
          </button>
        </div>
      </header>

      {error && (
        <div className="cc-notice is-error" role="alert">
          <AlertTriangle size={15} strokeWidth={2} />
          Could not load the overview: {error}
          <Link onClick={load}>Try again</Link>
        </div>
      )}
      {!data && !error && <Skeleton />}

      {data && quiet && (
        <div className="cc-notice">
          Nothing in {windowLabel(filter)}. Widen the date range, or pick “All” in the filter.
        </div>
      )}
      {data && filtered && data.filters_applied && data.filters_applied.bank_only_unassigned > 0
        && !data.filters_applied.unassigned_included && (
        <div className="cc-notice is-warn">
          <AlertTriangle size={15} strokeWidth={2} />
          {n(data.filters_applied.bank_only_unassigned)} bank-only{' '}
          {plural(data.filters_applied.bank_only_unassigned, 'credit has', 'credits have')} no
          operating unit and {plural(data.filters_applied.bank_only_unassigned, 'is', 'are')} hidden
          by the unit filter — tick “Unassigned” to include them.
        </div>
      )}

      {data && (
        <div className={`cc-layout${loading ? ' is-loading' : ''}`}>
          {/* ---- the inbox: work, most urgent first ---- */}
          <div className="cc-inbox">
            {data.matches.OPEN > 0 && (
              <section className="cc-group">
                <header className="cc-group-head">
                  <h3>Decide</h3>
                </header>
                <ul className="cc-task-list">
                  <TaskCard tone="review" icon={<ListChecks size={16} strokeWidth={2} />}
                    title={<>Review {n(data.matches.OPEN)} weak {plural(data.matches.OPEN, 'match', 'matches')}</>}
                    meta={<>{n(inReview)} {plural(inReview, 'credit waits', 'credits wait')} for accept or reject before settling</>}
                    onOpen={() => openQueue(Q_REVIEW)} action="Review" />
                </ul>
              </section>
            )}

            {data.top_exceptions.length > 0 && (
              <section className="cc-group">
                <header className="cc-group-head">
                  <h3>Resolve</h3>
                  <span className="cc-group-meta">
                    <Link onClick={() => openQueue(Q_UNMATCHED)} title={inr(openCreditValue)}>
                      {n(unmatched)} {plural(unmatched, 'credit', 'credits')} · {inrCompact(openCreditValue)}
                    </Link>
                    <Link onClick={() => openQueue(qExcSide('BILL_ONLY'))} title={inr(data.open_value.bill_only)}>
                      {n(billOnly)} {plural(billOnly, 'bill', 'bills')} · {inrCompact(data.open_value.bill_only)}
                    </Link>
                  </span>
                </header>
                <ul className="cc-task-list">
                  {data.top_exceptions.map(exceptionTask)}
                </ul>
                {more > 0 && (
                  <button type="button" className="cc-more" onClick={() => openQueue(Q_OPEN_EXC)}>
                    {n(more)} more open {plural(more, 'exception', 'exceptions')} in the Analyst queue
                    <ArrowRight size={14} strokeWidth={2} />
                  </button>
                )}
              </section>
            )}

            {data.matches.OPEN === 0 && data.top_exceptions.length === 0 && (
              <div className="cc-zero">
                <span className="cc-zero-icon"><Inbox size={26} strokeWidth={1.75} /></span>
                <strong>Inbox zero</strong>
                <span>No matches to decide and no open exceptions in {windowLabel(filter)}.</span>
                <button type="button" className="cc-btn" onClick={() => onNavigate('reconcile')}>
                  <GitMerge size={15} strokeWidth={1.75} /> Run a reconciliation
                </button>
              </div>
            )}

            {awaiting > 0 && (
              <section className={`cc-waiting${waitingOpen ? ' is-open' : ''}`}>
                <button type="button" className="cc-waiting-toggle" aria-expanded={waitingOpen}
                        onClick={() => setWaitingOpen((o) => !o)}>
                  <Clock3 size={15} strokeWidth={2} />
                  <span className="cc-waiting-title">Waiting on data ({n(awaiting)})</span>
                  {data.open_in_scope.awaiting_value !== undefined && (
                    <span className="cc-waiting-value" title={inr(data.open_in_scope.awaiting_value)}>
                      {inrCompact(data.open_in_scope.awaiting_value)}
                    </span>
                  )}
                  <span className="cc-waiting-hint">nothing to do yet — clears as data arrives</span>
                  <ChevronDown className="cc-waiting-chev" size={16} strokeWidth={2} />
                </button>
                {waitingOpen && (
                  <ul className="cc-waiting-list">
                    <li>
                      <Link onClick={() => openQueue(qExcGap('AWAITING_STATUS'))}>
                        {n(awaitingStatus)} awaiting source status
                      </Link>
                      <span>The same-amount bill is still passed or registered in IREPS, not yet
                        advised — the credit could not have matched.</span>
                    </li>
                    <li>
                      <Link onClick={() => openQueue(qExcGap('AWAITING_BILL_DATA'))}>
                        {n(awaitingBillData)} awaiting bill data
                      </Link>
                      <span>Valued after the latest bill export{data.bills_covered_through
                        ? ` (advices through ${fmtDay(data.bills_covered_through)})` : ''} — ingest a
                        newer export to match them.</span>
                    </li>
                    <li>
                      <Link onClick={() => openQueue(Q_AWAITING)}>
                        See every waiting credit <ArrowRight size={13} strokeWidth={2} />
                      </Link>
                    </li>
                  </ul>
                )}
              </section>
            )}
          </div>

          {/* ---- the scorecard: state, not work ---- */}
          <aside className="cc-side">
            <section className="cc-card cc-score">
              <header className="cc-card-head">
                <h3>Scorecard</h3>
                <Link onClick={() => openQueue(Q_ALL_MATCHES)}>
                  All matches <ArrowRight size={13} strokeWidth={2} />
                </Link>
              </header>

              <div className="cc-gauge">
                <div className="cc-gauge-top">
                  <span className="cc-gauge-value">{pct(data.match_rate)}</span>
                  <span className="cc-eyebrow">match rate</span>
                </div>
                <div className="cc-gauge-bar" role="img"
                     aria-label={`${settled} settled and ${inReview} in review of ${recognised} recognised`}>
                  <span className="seg seg-settled" style={{ width: `${rated(settled)}%` }} />
                  {inReview > 0 && <span className="seg seg-review" style={{ width: `${rated(inReview)}%` }} />}
                </div>
                <div className="cc-gauge-caption">
                  <Link quiet onClick={() => openQueue(Q_SETTLED)}>{n(settled)} settled</Link>
                  &nbsp;of&nbsp;
                  <Link quiet onClick={() => openGold(G_RECOGNISED)}
                        title="IREPS credits that could already have matched">
                    {n(recognised)} recognised
                  </Link>
                </div>
                <div className="cc-gauge-by">
                  auto {n(data.locked_by.AUTO_HIGH)}<span className="cc-dot" />
                  accepted {n(accepted)}<span className="cc-dot" />manual {n(manual)}
                </div>
              </div>

              <div className="cc-dl">
                <ScoreRow label="IREPS credits received" onOpen={() => openGold(G_IN_SCOPE)}
                  value={n(inScope)} sub={inrCompact(data.in_scope_value)} title={inr(data.in_scope_value)} />
                <ScoreRow label="Other receipts" muted
                  onOpen={() => openQueue(qExcGap('UNRECOGNISED_RECEIPT'))}
                  title="no match signal — interest, sweeps, payers outside IREPS; never in the rate"
                  value={n(outOfScope)}
                  sub={data.out_of_scope_value !== undefined ? inrCompact(data.out_of_scope_value) : undefined} />
                <ScoreRow label="Credits in window" onOpen={() => openGold(G_CREDITS)} value={n(credits)} />
                <ScoreRow label="Gold pool" onOpen={() => openGold(G_BILLS)}
                  value={<>{n(data.gold.bills)} bills</>}
                  sub={<>{n(data.gold.lineage_docs)} lineage docs</>} />
                <ScoreRow label="Resolved in window" onOpen={() => openQueue(Q_RESOLVED_EXC)}
                  value={n(data.resolved_exceptions)} />
              </div>
            </section>

            <RecentActivity customerId={customerId} refreshKey={refreshKey}
                            onOpenAudit={() => onNavigate('audit')} />
          </aside>
        </div>
      )}
    </section>
  )
}
