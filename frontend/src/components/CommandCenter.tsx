import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle, ArrowRight, CheckCircle2, Clock3, FileClock, FileSearch, GitMerge, ListChecks,
  RotateCw, Upload,
} from 'lucide-react'
import type { CustomerInfo, Overview } from '../types'
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
   elsewhere ("other receipts", "awaiting data") and never here. excGap
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

/** A small inline link — teal, so it always reads as one. */
function Link({ children, onClick, title, quiet }: {
  children: ReactNode; onClick: () => void; title?: string
  /** keeps the surrounding text colour until hovered (figures inside prose) */
  quiet?: boolean
}) {
  return (
    <button type="button" className={`cc-link${quiet ? ' is-quiet' : ''}`}
            onClick={onClick} title={title}>
      {children}
    </button>
  )
}

/* One headline metric of the executive band. The label is a stretched
   button — the whole cell opens its drill-down — while links in the
   sub-line sit above the stretch and open narrower slices. */
function Metric({ label, value, unit, sub, onOpen, title, tone, muted }: {
  label: string
  value: ReactNode
  unit?: ReactNode
  sub?: ReactNode
  onOpen: () => void
  title?: string
  /** a small swatch naming the bar segment this metric corresponds to */
  tone?: 'settled' | 'open' | 'awaiting'
  muted?: boolean
}) {
  return (
    <div className={`cc-metric${muted ? ' is-muted' : ''}`}>
      <button type="button" className="cc-metric-open" onClick={onOpen} title={title}>
        {tone && <span className={`cc-swatch sw-${tone}`} />}
        {label}
      </button>
      <div className="cc-metric-value">
        {value}
        {unit && <span className="cc-metric-unit">{unit}</span>}
      </div>
      {sub && <div className="cc-metric-sub">{sub}</div>}
    </div>
  )
}

type Tab = 'exceptions' | 'review' | 'awaiting'

function Skeleton() {
  return (
    <div aria-busy="true" aria-label="Loading overview" className="cc-body">
      <div className="cc-band sk" style={{ minHeight: 172 }} />
      <div className="cc-split">
        <div className="cc-card sk" style={{ minHeight: 320 }} />
        <div className="cc-card sk" style={{ minHeight: 320 }} />
      </div>
    </div>
  )
}

export function CommandCenter({
  customers, customerId, onCustomerChange, onNavigate, onOpenQueue, onOpenGold, refreshKey,
}: Props) {
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // date window + operating units (item 2.1), remembered per customer
  const [filter, setFilter] = useState<DateFilterValue>(() => loadFilter(customerId))
  const [units, setUnits] = useState<string[]>([])
  const [unitCounts, setUnitCounts] = useState<Record<string, number>>({})
  // the work-queue tab (local only): null = the first tab with work in it
  const [tab, setTab] = useState<Tab | null>(null)

  useEffect(() => { setFilter(loadFilter(customerId)); setTab(null) }, [customerId])
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
  const openCount = data?.open_in_scope.count ?? 0
  const reviewCount = data?.matches.OPEN ?? 0

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
     The band owns the HEADLINE of each level (rate, received, open,
     awaiting, other receipts) and its bar draws the IREPS partition
     (identity + share in the legend, counts on hover). Each work-queue
     tab owns the BREAKDOWN behind a headline — credits vs bills, source
     status vs bill data, weak matches — never the headline itself; a tab
     badge only says how much sits behind the tab. */
  const partition = [
    { key: 'settled', label: 'Settled', count: settled, onOpen: () => openQueue(Q_SETTLED) },
    { key: 'review', label: 'In review', count: inReview, onOpen: () => openQueue(Q_REVIEW) },
    { key: 'open', label: 'Unmatched', count: unmatched, onOpen: () => openQueue(Q_UNMATCHED) },
    { key: 'awaiting', label: 'Awaiting data', count: awaiting, onOpen: () => openQueue(Q_AWAITING) },
  ]

  const tabs: { key: Tab; label: string; count: number; icon: ReactNode }[] = [
    { key: 'exceptions', label: 'Open exceptions', count: openCount,
      icon: <AlertTriangle size={14} strokeWidth={2} /> },
    { key: 'review', label: 'Needs review', count: reviewCount,
      icon: <ListChecks size={14} strokeWidth={2} /> },
    { key: 'awaiting', label: 'Awaiting data', count: awaiting,
      icon: <Clock3 size={14} strokeWidth={2} /> },
  ]
  const activeTab: Tab = tab ?? tabs.find((t) => t.count > 0)?.key ?? 'exceptions'

  const asOf = data?.data_as_of ?? new Date().toISOString().slice(0, 10)

  return (
    <section className="cc-page">
      <header className="cc-head">
        <div className="cc-head-title">
          <h2 className="page-title">Command Center</h2>
          <p className="cc-context">
            {customerName}
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
        <div className={`cc-body${loading ? ' is-loading' : ''}`}>
          {/* ---- executive band: one headline per funnel level ---- */}
          <section className="cc-band" aria-label="Reconciliation summary">
            <div className="cc-metrics">
              <Metric label="Match rate" onOpen={() => openQueue(Q_ALL_MATCHES)}
                title="settled credits over recognised credits — opens every match"
                value={pct(data.match_rate)}
                sub={<>
                  <span>
                    <Link quiet onClick={() => openQueue(Q_SETTLED)}>{n(settled)} settled</Link>
                    {' of '}
                    <Link quiet onClick={() => openGold(G_RECOGNISED)}
                          title="IREPS credits that could already have matched">
                      {n(recognised)} recognised
                    </Link>
                  </span>
                  <span className="cc-metric-fine">
                    auto {n(data.locked_by.AUTO_HIGH)} · accepted {n(accepted)} · manual {n(manual)}
                  </span>
                </>} />
              <Metric label="IREPS credits received" onOpen={() => openGold(G_IN_SCOPE)}
                title={inr(data.in_scope_value)}
                value={inrCompact(data.in_scope_value)}
                sub={<span>
                  {n(inScope)} {plural(inScope, 'credit', 'credits')}
                  <span className="cc-dot" />
                  <Link quiet onClick={() => openGold(G_CREDITS)}>{n(credits)} in window</Link>
                </span>} />
              <Metric label="Open exceptions" tone="open" onOpen={() => openQueue(Q_OPEN_EXC)}
                title={inr(data.open_in_scope.value)}
                value={n(openCount)} unit="open"
                sub={<span>{inrCompact(data.open_in_scope.value)} exposure</span>} />
              <Metric label="Awaiting data" tone="awaiting" onOpen={() => openQueue(Q_AWAITING)}
                title={data.open_in_scope.awaiting_value !== undefined
                  ? inr(data.open_in_scope.awaiting_value) : undefined}
                value={n(awaiting)} unit={plural(awaiting, 'credit', 'credits')}
                sub={<span>{data.open_in_scope.awaiting_value !== undefined
                  ? `${inrCompact(data.open_in_scope.awaiting_value)} · not rated yet` : 'not rated yet'}</span>} />
              <Metric label="Other receipts" muted
                onOpen={() => openQueue(qExcGap('UNRECOGNISED_RECEIPT'))}
                title="no match signal — interest, sweeps, payers outside IREPS; never in the rate"
                value={n(outOfScope)}
                sub={<span>{data.out_of_scope_value !== undefined
                  ? `${inrCompact(data.out_of_scope_value)} · not matchable` : 'not matchable'}</span>} />
            </div>

            <div className="cc-band-bar">
              <div className="cc-bar" role="img"
                   aria-label={partition.map((p) => `${p.label} ${p.count}`).join(', ')}>
                {partition.filter((p) => p.count > 0).map((p) => (
                  <span key={p.key} className={`seg seg-${p.key}`} style={{ flexGrow: p.count }}
                        title={`${p.label} · ${n(p.count)} ${plural(p.count, 'credit', 'credits')}`} />
                ))}
              </div>
              <div className="cc-legend">
                <span className="cc-legend-caption">IREPS credits</span>
                {partition.map((p) => (
                  <button key={p.key} type="button" className="cc-legend-item" onClick={p.onOpen}
                          title={`${n(p.count)} ${plural(p.count, 'credit', 'credits')}`}>
                    <span className={`cc-swatch sw-${p.key}`} />
                    {p.label}
                    <span className="cc-legend-pct">{inScope > 0 ? pct(p.count / inScope) : '—'}</span>
                  </button>
                ))}
                <span className="cc-legend-pool">
                  Gold pool&nbsp;
                  <Link quiet onClick={() => openGold(G_BILLS)}>{n(data.gold.bills)} bills</Link>
                  &nbsp;·&nbsp;{n(data.gold.lineage_docs)} lineage docs
                </span>
              </div>
            </div>
          </section>

          <div className="cc-split">
            {/* ---- work queue: the breakdown behind each headline ---- */}
            <section className="cc-card cc-queue">
              <header className="cc-queue-head">
                <h3>Work queue</h3>
                <div className="cc-tabs" role="tablist" aria-label="Work queue">
                  {tabs.map((t) => (
                    <button key={t.key} type="button" role="tab"
                            id={`cc-tab-${t.key}`} aria-controls={`cc-panel-${t.key}`}
                            aria-selected={activeTab === t.key}
                            className={`cc-tab${activeTab === t.key ? ' is-active' : ''}`}
                            onClick={() => setTab(t.key)}>
                      {t.icon}
                      {t.label}
                      <span className={`cc-tab-badge${t.count > 0 ? ' has-work' : ''}`}>{n(t.count)}</span>
                    </button>
                  ))}
                </div>
              </header>

              {activeTab === 'exceptions' && (
                <div role="tabpanel" id="cc-panel-exceptions" aria-labelledby="cc-tab-exceptions"
                     className="cc-panel">
                  <div className="cc-panel-strip">
                    <Link onClick={() => openQueue(Q_UNMATCHED)} title={inr(openCreditValue)}>
                      {n(unmatched)} {plural(unmatched, 'credit', 'credits')} · {inrCompact(openCreditValue)}
                    </Link>
                    <Link onClick={() => openQueue(qExcSide('BILL_ONLY'))} title={inr(data.open_value.bill_only)}>
                      {n(billOnly)} {plural(billOnly, 'bill', 'bills')} · {inrCompact(data.open_value.bill_only)}
                    </Link>
                    <span className="cc-panel-strip-end">
                      <Link quiet onClick={() => openQueue(Q_RESOLVED_EXC)}>
                        {n(data.resolved_exceptions)} resolved in window
                      </Link>
                    </span>
                  </div>
                  {data.top_exceptions.length === 0 ? (
                    <div className="cc-empty">
                      <CheckCircle2 size={24} strokeWidth={1.75} />
                      <strong>Nothing open</strong>
                      <span>Every recognised credit and advised bill is accounted for.</span>
                      <Link onClick={() => onNavigate('reconcile')}>
                        Run an incremental reconciliation <ArrowRight size={13} strokeWidth={2} />
                      </Link>
                    </div>
                  ) : (
                    <>
                      <div className="cc-table-wrap">
                        <table className="cc-table">
                          <thead>
                            <tr>
                              <th>Type</th><th>Reference</th><th>Zone</th><th>Date</th>
                              <th className="num">Age</th><th className="num">Amount</th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.top_exceptions.map((e) => {
                              const age = e.date ? ageDays(e.date, asOf) : null
                              const open = () => openQueue(qExcSide(e.exception_type))
                              return (
                                <tr key={e.id} onClick={open} tabIndex={0}
                                    onKeyDown={(k) => { if (k.key === 'Enter') open() }}>
                                  <td>
                                    <span className={`cc-type type-${e.exception_type}`}>
                                      {e.exception_type === 'BANK_ONLY' ? 'Credit' : 'Bill'}
                                    </span>
                                  </td>
                                  <td className="mono">{e.ref ?? '—'}</td>
                                  <td>{e.zone ?? '—'}</td>
                                  <td className="nowrap">{e.date ? fmtDay(e.date) : '—'}</td>
                                  <td className="num">
                                    {age === null ? '—'
                                      : <span className={`cc-age${age > 30 ? ' is-late' : ''}`}>{age}d</span>}
                                  </td>
                                  <td className="num strong" title={inr(e.amount)}>{inrCompact(e.amount)}</td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                      <footer className="cc-panel-foot">
                        <span>Largest {n(data.top_exceptions.length)} by amount</span>
                        <Link onClick={() => openQueue(Q_OPEN_EXC)}>
                          Open all in Analyst queue <ArrowRight size={13} strokeWidth={2} />
                        </Link>
                      </footer>
                    </>
                  )}
                </div>
              )}

              {activeTab === 'review' && (
                <div role="tabpanel" id="cc-panel-review" aria-labelledby="cc-tab-review"
                     className="cc-panel">
                  {reviewCount === 0 ? (
                    <div className="cc-empty">
                      <CheckCircle2 size={24} strokeWidth={1.75} />
                      <strong>No matches waiting</strong>
                      <span>Weak matches (ambiguous, low, amount-only, batched) land here for a decision.</span>
                      <Link onClick={() => openQueue(Q_SETTLED)}>
                        Browse settled matches <ArrowRight size={13} strokeWidth={2} />
                      </Link>
                    </div>
                  ) : (
                    <div className="cc-cta">
                      <span className="cc-cta-icon"><ListChecks size={22} strokeWidth={1.75} /></span>
                      <div className="cc-cta-body">
                        <strong>
                          {n(inReview)} {plural(inReview, 'credit is', 'credits are')} waiting for a decision
                        </strong>
                        <span>
                          Weak matches stay open until someone accepts or rejects them. Accepting locks
                          the credit and its bills; rejecting releases both back to the pool.
                        </span>
                      </div>
                      <button type="button" className="cc-btn cc-btn-primary" onClick={() => openQueue(Q_REVIEW)}>
                        Review in Analyst queue <ArrowRight size={15} strokeWidth={1.75} />
                      </button>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'awaiting' && (
                <div role="tabpanel" id="cc-panel-awaiting" aria-labelledby="cc-tab-awaiting"
                     className="cc-panel">
                  <p className="cc-panel-intro">
                    IREPS money that could not have matched yet. These credits are left out of the
                    match rate and clear on their own as the data arrives.
                  </p>
                  <ul className="cc-wait">
                    <li>
                      <span className="cc-wait-icon"><FileSearch size={16} strokeWidth={1.75} /></span>
                      <div className="cc-wait-body">
                        <strong>Awaiting source status</strong>
                        <span>The same-amount bill is still passed or registered in IREPS, not yet advised.</span>
                      </div>
                      <button type="button" className="cc-wait-count"
                              onClick={() => openQueue(qExcGap('AWAITING_STATUS'))}>
                        <span>{n(awaitingStatus)}</span>
                        <small>{plural(awaitingStatus, 'credit', 'credits')}</small>
                      </button>
                    </li>
                    <li>
                      <span className="cc-wait-icon"><FileClock size={16} strokeWidth={1.75} /></span>
                      <div className="cc-wait-body">
                        <strong>Awaiting bill data</strong>
                        <span>
                          Valued after the latest bill export
                          {data.bills_covered_through && <> (advices through {fmtDay(data.bills_covered_through)})</>}
                          . Ingest a newer Bill Status export — excused only until it goes stale.
                        </span>
                      </div>
                      <button type="button" className="cc-wait-count"
                              onClick={() => openQueue(qExcGap('AWAITING_BILL_DATA'))}>
                        <span>{n(awaitingBillData)}</span>
                        <small>{plural(awaitingBillData, 'credit', 'credits')}</small>
                      </button>
                    </li>
                  </ul>
                  <footer className="cc-panel-foot">
                    <span>Clears automatically once the data is ingested</span>
                    <Link onClick={() => onNavigate('ingest')}>
                      Ingest documents <ArrowRight size={13} strokeWidth={2} />
                    </Link>
                  </footer>
                </div>
              )}
            </section>

            {/* ---- what changed ---- */}
            <RecentActivity customerId={customerId} refreshKey={refreshKey}
                            onOpenAudit={() => onNavigate('audit')} />
          </div>
        </div>
      )}
    </section>
  )
}
