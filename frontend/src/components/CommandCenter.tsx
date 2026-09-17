import { useCallback, useEffect, useId, useState, type ReactNode } from 'react'
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronRight, Clock3, GitMerge, RotateCw, Upload,
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
const DAY_SHORT = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' })
/** yyyy-mm-dd -> "21 Aug 2026" (parsed as a calendar day, no timezone shift) */
function fmtDay(iso: string, short = false): string {
  const [y, m, d] = iso.split('-').map(Number)
  return y && m && d ? (short ? DAY_SHORT : DAY).format(new Date(y, m - 1, d)) : iso
}

/** whole days from a yyyy-mm-dd date to the data's own "today" */
function ageDays(date: string, asOf: string): number | null {
  const a = Date.parse(date), b = Date.parse(asOf)
  if (Number.isNaN(a) || Number.isNaN(b)) return null
  return Math.max(0, Math.round((b - a) / 86_400_000))
}

/** A small inline link — teal, so it always reads as one. */
function Link({ children, onClick, title }: {
  children: ReactNode; onClick: () => void; title?: string
}) {
  return (
    <button type="button" className="cc-link" onClick={onClick} title={title}>
      {children}
    </button>
  )
}

/* ---------------------------------------------------------------------
   Flow geometry. Every column of the flow is FLOW_H px tall; block
   positions are computed here and the SVG ribbons are drawn from the
   very same numbers (viewBox height = FLOW_H, stretched horizontally
   only), so a ribbon always lands exactly on its block.
   --------------------------------------------------------------------- */

const FLOW_H = 320

interface Slot { y: number; h: number }

/** Stack blocks with gaps: every block gets `minH`, the rest of the
 *  column is shared out in proportion to the counts (evenly when all
 *  are zero), so a 0 still has a readable block. */
function stack(counts: number[], H: number, gap: number, minH: number): Slot[] {
  const k = counts.length
  const free = Math.max(0, H - gap * (k - 1) - minH * k)
  const total = counts.reduce((a, b) => a + b, 0)
  let y = 0
  return counts.map((c) => {
    const h = minH + (total > 0 ? free * (c / total) : free / k)
    const slot = { y, h }
    y += h + gap
    return slot
  })
}

/** A parent block's edge split into gapless bands by the counts — where
 *  each child's ribbon leaves the parent. */
function bands(counts: number[], parent: Slot): Slot[] {
  const total = counts.reduce((a, b) => a + b, 0)
  let y = parent.y
  return counts.map((c) => {
    const h = total > 0 ? parent.h * (c / total) : 0
    const slot = { y, h }
    y += h
    return slot
  })
}

type FlowKey = 'credits' | 'ireps' | 'other' | 'settled' | 'review' | 'open' | 'awaiting'

interface Ribbon { key: FlowKey; tone: string; from: Slot; to: Slot; count: number }

/** Soft Sankey ribbons between two stage columns. Zero-count ribbons are
 *  not drawn; hovering a block brightens its own ribbons and dims the rest. */
function Ribbons({ ribbons, hot, parentKey }: {
  ribbons: Ribbon[]; hot: FlowKey | null; parentKey: FlowKey
}) {
  const pid = useId().replace(/:/g, '')
  return (
    <svg className="cc-ribbons" viewBox={`0 0 100 ${FLOW_H}`} preserveAspectRatio="none"
         style={{ height: FLOW_H }} aria-hidden="true" focusable="false">
      <defs>
        <pattern id={`${pid}-stripe`} width="8" height="8" patternUnits="userSpaceOnUse"
                 patternTransform="rotate(45)">
          <rect width="8" height="8" className="cc-rib-stripe-bg" />
          <rect width="4" height="8" className="cc-rib-stripe" />
        </pattern>
      </defs>
      {ribbons.filter((r) => r.count > 0).map((r) => {
        const { from: s, to: t } = r
        const d = `M0 ${s.y} C50 ${s.y} 50 ${t.y} 100 ${t.y} L100 ${t.y + t.h} `
          + `C50 ${t.y + t.h} 50 ${s.y + s.h} 0 ${s.y + s.h} Z`
        const state = hot === null ? ''
          : hot === r.key || hot === parentKey ? ' is-hot' : ' is-dim'
        return (
          <path key={r.key} d={d} className={`cc-rib rib-${r.tone}${state}`}
                style={r.tone === 'open' ? { fill: `url(#${pid}-stripe)` } : undefined} />
        )
      })}
    </svg>
  )
}

/** One block of the flow — a real button, positioned by its slot. */
function FlowNode({ k, slot, tone, label, count, share, value, valueTitle, badge, meta, onOpen, setHot }: {
  k: FlowKey
  slot?: Slot
  tone: string
  label: string
  count: number
  /** share of the parent block */
  share?: string
  value?: number
  valueTitle?: string
  badge?: ReactNode
  meta?: ReactNode
  onOpen: () => void
  setHot: (k: FlowKey | null) => void
}) {
  // short blocks keep label + count on one line and drop the extras
  const size = !slot ? 'full' : slot.h < 64 ? 'xs' : slot.h < 96 ? 'sm' : 'full'
  return (
    <button type="button"
            className={`cc-node tone-${tone} size-${size}${count === 0 ? ' is-zero' : ''}`}
            style={slot ? { top: slot.y, height: slot.h } : undefined}
            onClick={onOpen}
            onMouseEnter={() => setHot(k)} onMouseLeave={() => setHot(null)}
            onFocus={() => setHot(k)} onBlur={() => setHot(null)}>
      <span className="cc-node-head">
        <span className="cc-node-label">{label}</span>
        {share && <span className="cc-node-share">{share}</span>}
      </span>
      <span className="cc-node-figures">
        <span className="cc-node-count">{n(count)}</span>
        {value !== undefined && (
          <span className="cc-node-value" title={valueTitle}>{inrCompact(value)}</span>
        )}
      </span>
      {badge && <span className="cc-node-badge">{badge}</span>}
      {meta && <span className="cc-node-meta">{meta}</span>}
    </button>
  )
}

function Skeleton() {
  return (
    <div className="cc-stack" aria-busy="true" aria-label="Loading overview">
      <div className="cc-card sk" style={{ minHeight: 420 }} />
      <div className="cc-trio">
        <div className="cc-card sk" style={{ minHeight: 280 }} />
        <div className="cc-card sk" style={{ minHeight: 280 }} />
        <div className="cc-card sk" style={{ minHeight: 280 }} />
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
  const [hot, setHot] = useState<FlowKey | null>(null)
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
  // the server reports the two halves of the window's money, never the
  // whole, so add them back up here
  const creditsValue =
    data && data.in_scope_value !== undefined && data.out_of_scope_value !== undefined
      ? data.in_scope_value + data.out_of_scope_value
      : undefined

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

  /* ONE figure, ONE place. The flow draws the credit funnel exactly as
     db/overview.py computes it, one level per column:
       credits       = IREPS credits + other receipts
       IREPS credits = settled + in review + unmatched + awaiting data
       recognised    = settled + in review + unmatched   (the rate's base)
     The cards underneath carry only what the flow cannot: the bills side
     of open exceptions, the awaiting-data split and resolutions. */
  const stage2 = stack([inScope, outOfScope], FLOW_H, 12, 96)
  const outcomes: Array<{ key: FlowKey; tone: string; count: number }> = [
    { key: 'settled', tone: 'settled', count: settled },
    { key: 'review', tone: 'review', count: inReview },
    { key: 'open', tone: 'open', count: unmatched },
    { key: 'awaiting', tone: 'awaiting', count: awaiting },
  ]
  const stage3 = stack(outcomes.map((o) => o.count), FLOW_H, 8, 56)
  const creditBands = bands([inScope, outOfScope], { y: 0, h: FLOW_H })
  const irepsBands = bands(outcomes.map((o) => o.count), stage2[0])
  const ribbons1: Ribbon[] = [
    { key: 'ireps', tone: 'ireps', from: creditBands[0], to: stage2[0], count: inScope },
    { key: 'other', tone: 'other', from: creditBands[1], to: stage2[1], count: outOfScope },
  ]
  const ribbons2: Ribbon[] = outcomes.map((o, i) =>
    ({ key: o.key, tone: o.tone, from: irepsBands[i], to: stage3[i], count: o.count }))
  const shareOf = (c: number, total: number) => (total > 0 ? pct(c / total) : undefined)

  const flowSummary = `${n(credits)} credits in window: ${n(inScope)} IREPS credits `
    + `(${n(settled)} settled, ${n(inReview)} in review, ${n(unmatched)} unmatched, `
    + `${n(awaiting)} awaiting data) and ${n(outOfScope)} other receipts. `
    + `Match rate ${pct(data?.match_rate)}.`

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
        <div className={`cc-stack${loading ? ' is-loading' : ''}`}>
          {/* ---- the money, left to right ---- */}
          <section className="cc-card cc-flow-card" aria-label="Reconciliation flow">
            <header className="cc-card-head">
              <div>
                <h3>Reconciliation flow</h3>
                <p className="cc-card-sub">Where this window’s credits went</p>
              </div>
              <div className="cc-flow-head-links">
                <span className="cc-muted">
                  Gold pool <Link onClick={() => openGold(G_BILLS)}>{n(data.gold.bills)} bills</Link>
                  {' · '}{n(data.gold.lineage_docs)} lineage docs
                </span>
                <Link onClick={() => openQueue(Q_ALL_MATCHES)}>
                  All matches <ArrowRight size={13} strokeWidth={2} />
                </Link>
              </div>
            </header>

            <div className="cc-flow-stages" aria-hidden="true">
              <span>Received</span><span /><span>Source</span><span /><span>Outcome</span>
            </div>
            <div className="cc-flow" role="group" aria-label={flowSummary}>
              <div className="cc-stage cc-stage-1" style={{ height: FLOW_H }}>
                <FlowNode k="credits" tone="credits" label="Credits in window" count={credits}
                  value={creditsValue} valueTitle={creditsValue !== undefined ? inr(creditsValue) : undefined}
                  meta="every credit the reconciliation saw"
                  onOpen={() => openGold(G_CREDITS)} setHot={setHot} />
              </div>

              <Ribbons ribbons={ribbons1} hot={hot} parentKey="credits" />

              <div className="cc-stage" style={{ height: FLOW_H }}>
                <FlowNode k="ireps" slot={stage2[0]} tone="ireps" label="IREPS credits" count={inScope}
                  share={shareOf(inScope, credits)}
                  value={data.in_scope_value} valueTitle={inr(data.in_scope_value)}
                  meta="carry this customer’s match signal"
                  onOpen={() => openGold(G_IN_SCOPE)} setHot={setHot} />
                <FlowNode k="other" slot={stage2[1]} tone="other" label="Other receipts" count={outOfScope}
                  share={shareOf(outOfScope, credits)}
                  value={data.out_of_scope_value} valueTitle={inr(data.out_of_scope_value)}
                  meta="not matchable — interest, sweeps, other payers"
                  onOpen={() => openQueue(qExcGap('UNRECOGNISED_RECEIPT'))} setHot={setHot} />
              </div>

              <Ribbons ribbons={ribbons2} hot={hot} parentKey="ireps" />

              <div className="cc-stage" style={{ height: FLOW_H }}>
                <FlowNode k="settled" slot={stage3[0]} tone="settled" label="Settled" count={settled}
                  share={shareOf(settled, inScope)}
                  badge={<><strong>{pct(data.match_rate)}</strong> match rate</>}
                  meta={<>auto {n(data.locked_by.AUTO_HIGH)} · accepted {n(accepted)} · manual {n(manual)}</>}
                  onOpen={() => openQueue(Q_SETTLED)} setHot={setHot} />
                <FlowNode k="review" slot={stage3[1]} tone="review" label="In review" count={inReview}
                  share={shareOf(inReview, inScope)}
                  meta={`${n(data.matches.OPEN)} ${plural(data.matches.OPEN, 'match', 'matches')} to accept or reject`}
                  onOpen={() => openQueue(Q_REVIEW)} setHot={setHot} />
                <FlowNode k="open" slot={stage3[2]} tone="open" label="Unmatched" count={unmatched}
                  share={shareOf(unmatched, inScope)}
                  value={openCreditValue} valueTitle={inr(openCreditValue)}
                  meta="recognised, no bill found"
                  onOpen={() => openQueue(Q_UNMATCHED)} setHot={setHot} />
                <FlowNode k="awaiting" slot={stage3[3]} tone="awaiting" label="Awaiting data" count={awaiting}
                  share={shareOf(awaiting, inScope)}
                  value={data.open_in_scope.awaiting_value}
                  valueTitle={data.open_in_scope.awaiting_value !== undefined
                    ? inr(data.open_in_scope.awaiting_value) : undefined}
                  meta="could not have matched yet"
                  onOpen={() => openQueue(Q_AWAITING)} setHot={setHot} />
              </div>
            </div>

            <p className="cc-flow-foot">
              Shares are of the block each one flows from. The match rate is settled ÷{' '}
              <Link onClick={() => openGold(G_RECOGNISED)}
                    title="settled + in review + unmatched">
                {n(recognised)} recognised credits
              </Link>
              {' '}— credits awaiting data are not rated until their data arrives.
            </p>
          </section>

          <div className="cc-trio">
            {/* ---- open exceptions: the bills side lives only here ---- */}
            <section className="cc-card cc-exc">
              <header className="cc-card-head">
                <div>
                  <h3>Largest open exceptions</h3>
                  <p className="cc-card-sub">
                    <Link onClick={() => openQueue(qExcSide('BILL_ONLY'))} title={inr(data.open_value.bill_only)}>
                      {n(billOnly)} open {plural(billOnly, 'bill', 'bills')} · {inrCompact(data.open_value.bill_only)}
                    </Link>
                    {' '}besides the unmatched credits
                  </p>
                </div>
                <Link onClick={() => openQueue(Q_OPEN_EXC)}>
                  Queue <ArrowRight size={13} strokeWidth={2} />
                </Link>
              </header>
              {data.top_exceptions.length === 0 ? (
                <div className="cc-empty">
                  <CheckCircle2 size={22} strokeWidth={1.75} />
                  <strong>Nothing open</strong>
                  <span>Every recognised credit and advised bill is accounted for.</span>
                  <Link onClick={() => onNavigate('reconcile')}>
                    Run an incremental reconciliation <ArrowRight size={13} strokeWidth={2} />
                  </Link>
                </div>
              ) : (
                <ul className="cc-list">
                  {data.top_exceptions.map((e) => {
                    const age = e.date ? ageDays(e.date, asOf) : null
                    const side = e.exception_type === 'BANK_ONLY' ? 'Credit' : 'Bill'
                    return (
                      <li key={e.id}>
                        <button type="button" className="cc-list-row"
                                onClick={() => openQueue(qExcSide(e.exception_type))}
                                aria-label={`${side} ${e.ref ?? ''} ${inr(e.amount)}`}>
                          <span className={`cc-side side-${e.exception_type}`} title={side} />
                          <span className="cc-list-main">
                            <span className="cc-list-ref">{e.ref ?? '—'}</span>
                            <span className="cc-list-meta">
                              {side}{e.zone && <> · {e.zone}</>}
                              {e.date && <> · {fmtDay(e.date, true)}</>}
                              {age !== null && (
                                <> · <span className={age > 30 ? 'is-late' : undefined}>{age}d</span></>
                              )}
                            </span>
                          </span>
                          <span className="cc-list-amt" title={inr(e.amount)}>{inrCompact(e.amount)}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>

            {/* ---- why the awaiting credits have not matched yet ---- */}
            <section className="cc-card cc-wait">
              <header className="cc-card-head">
                <div>
                  <h3>Awaiting data</h3>
                  <p className="cc-card-sub">Clears on its own as the data arrives</p>
                </div>
                <Clock3 size={16} strokeWidth={1.75} className="cc-muted" />
              </header>
              <ul className="cc-reasons">
                <li>
                  <button type="button" className="cc-reason" onClick={() => openQueue(qExcGap('AWAITING_STATUS'))}>
                    <span className="cc-reason-text">
                      <span className="cc-reason-title">Source status</span>
                      <span className="cc-reason-why">
                        The same-amount bill is still passed or registered in IREPS — not advised yet.
                      </span>
                    </span>
                    <span className="cc-reason-n">{n(awaitingStatus)}</span>
                    <ChevronRight size={15} strokeWidth={2} className="cc-chev" />
                  </button>
                </li>
                <li>
                  <button type="button" className="cc-reason" onClick={() => openQueue(qExcGap('AWAITING_BILL_DATA'))}>
                    <span className="cc-reason-text">
                      <span className="cc-reason-title">Bill data</span>
                      <span className="cc-reason-why">
                        Valued after the latest bill export
                        {data.bills_covered_through
                          ? ` (advices through ${fmtDay(data.bills_covered_through)})` : ''} — ingest a newer one.
                      </span>
                    </span>
                    <span className="cc-reason-n">{n(awaitingBillData)}</span>
                    <ChevronRight size={15} strokeWidth={2} className="cc-chev" />
                  </button>
                </li>
              </ul>
              <button type="button" className="cc-resolved" onClick={() => openQueue(Q_RESOLVED_EXC)}>
                <CheckCircle2 size={16} strokeWidth={2} />
                <span>
                  <strong>{n(data.resolved_exceptions)}</strong>{' '}
                  {plural(data.resolved_exceptions, 'exception', 'exceptions')} resolved in this window
                </span>
                <ChevronRight size={15} strokeWidth={2} className="cc-chev" />
              </button>
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
