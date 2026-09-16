import { useCallback, useEffect, useState } from 'react'
import { GitMerge, RotateCw, Upload } from 'lucide-react'
import type { CustomerInfo, Overview } from '../types'
import { fetchOperatingUnits, fetchOverview } from '../api'
import { inr } from '../format'
import type { View } from './Sidebar'
import type { LedgerIntent } from './LedgerView'
import type { GoldIntent } from './GoldTable'
import {
  DEFAULT_DATE_FILTER, DateFilter, resolveWindow, unitsLabel, windowLabel,
  type DateFilterValue,
} from './DateFilter'

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

/* The presets each heading carries into the Analyst queue. They are
   plain filter values, so each one arrives as a removable chip there —
   the analyst sees WHY the table is narrowed. `[]` clears a filter. */
const Q_SETTLED: LedgerIntent = { section: 'matches', matchStatus: ['LOCKED'] }
const Q_REVIEW: LedgerIntent = { section: 'matches', matchStatus: ['OPEN'] }
const Q_ALL_MATCHES: LedgerIntent = { section: 'matches', matchStatus: [] }
/* Open exceptions are IREPS-only (db/overview.open_in_scope): other
   receipts can never match a bill and are counted once, under Match
   performance's "Other receipts". excGap is always set so a gap chip
   left over from an earlier arrival cannot narrow these. */
const Q_OPEN_EXC: LedgerIntent = {
  section: 'exceptions', excStatus: ['OPEN'], excType: [], excGap: [], excInScope: true,
}
const Q_RESOLVED_EXC: LedgerIntent = {
  section: 'exceptions', excStatus: ['RESOLVED'], excType: [], excGap: [], excInScope: false,
}
/** open exceptions of ONE side — the tile's "N bank only" / "M bill only" */
const qExcSide = (side: string): LedgerIntent =>
  ({ section: 'exceptions', excStatus: ['OPEN'], excType: [side], excGap: [], excInScope: true })
/* Open bank-only exceptions narrowed to ONE gap code (LedgerView.gapOf).
   Each key row below owns a distinct code, so a row opens exactly the
   credits it counted. Two of the four are stored on the exception
   (UNRECOGNISED_RECEIPT | SIGNAL_BILL_NOT_FOUND); the awaiting pair is
   computed per row by db/overview.gap_details and ridden to the client
   as `gap_detail` — without that they collapse into
   SIGNAL_BILL_NOT_FOUND and all three rows land on the same table. */
const qExcGap = (gap: string): LedgerIntent =>
  ({ section: 'exceptions', excStatus: ['OPEN'], excType: ['BANK_ONLY'],
     excGap: [gap], excInScope: false })

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

/** Donut ring in the letterpress palette (inline SVG, no deps). */
function MatchDonut({ rate }: { rate: number | null }) {
  const value = rate === null ? 0 : Math.max(0, Math.min(1, rate))
  const c = 2 * Math.PI * 42
  return (
    <div className="cc-donut">
      <svg viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="42" fill="none"
                stroke="var(--rule)" strokeWidth="9" />
        <circle cx="50" cy="50" r="42" fill="none"
                stroke="var(--green-bright)" strokeWidth="9"
                strokeLinecap="round"
                strokeDasharray={`${value * c} ${(1 - value) * c}`}
                transform="rotate(-90 50 50)" />
      </svg>
      <div className="cc-donut-label">
        <span className="cc-donut-value">{pct(rate)}</span>
        <span className="cc-donut-sub">recognised credits settled</span>
      </div>
    </div>
  )
}

/* One bucket of the credit funnel. The buckets inside a section are
   mutually exclusive and sum to that section's total, so the bar above
   them is an honest part-to-whole — unlike the five overlapping meters
   this replaces, where AUTO_HIGH + USER simply WAS `settled` and USER
   already contained the manual matches.
   Colour: only the acted-on buckets carry a fill, the remainder is left
   as bare track. That holds every bar to a pair that survives colour-
   blind simulation — green/gold is OKLab dE 13.7 (deuteranopia), 20.8
   (normal vision), while the obvious third fill, gold/sienna, is 1.7
   and 12.4 and would be unreadable. */
interface Bucket {
  key: string
  label: string
  count: number
  /** '' = no fill: this bucket IS the bar's remainder, keyed hollow */
  tone: string
  /** why the bucket exists, on hover — three lines instead of three paragraphs */
  hint: string
  value?: number
  /** sits BELOW the bar's total, excluded from it (the "not rated" credits) */
  aside?: boolean
  onOpen?: () => void
}

/** Part-to-whole bar: filled buckets in order, remainder left as track. */
function CompositionBar({ buckets, total }: { buckets: Bucket[]; total: number }) {
  return (
    <div className="cc-comp" role="img"
         aria-label={buckets.map((b) => `${b.label} ${b.count}`).join(', ')}>
      {buckets.filter((b) => b.tone && b.count > 0).map((b) => (
        <span key={b.key} className={`cc-comp-seg ${b.tone}`}
              title={`${b.label} · ${b.count.toLocaleString('en-IN')}`}
              style={{ width: `${total > 0 ? (b.count / total) * 100 : 0}%` }} />
      ))}
    </div>
  )
}

/** The bar's legend row — identity in text, never colour alone. */
function KeyRow({ bucket, total }: { bucket: Bucket; total: number }) {
  const body = (
    <>
      <span className={`cc-key-dot ${bucket.tone || 'seg-track'}`} />
      <span className="cc-key-label">{bucket.label}</span>
      <span className="cc-key-n">{bucket.count.toLocaleString('en-IN')}</span>
      <span className="cc-key-pct">
        {bucket.aside ? 'not rated' : pct(total > 0 ? bucket.count / total : null)}
      </span>
      {bucket.value !== undefined && <span className="cc-key-v">{inr(bucket.value)}</span>}
    </>
  )
  const cls = `cc-key-row${bucket.aside ? ' is-aside' : ''}`
  return bucket.onOpen
    ? <button type="button" className={`${cls} is-link`} title={bucket.hint}
              onClick={bucket.onOpen}>{body}</button>
    : <div className={cls} title={bucket.hint}>{body}</div>
}

/** The level of the funnel a bar divides, and its total. Both levels
 *  count CREDITS, so the label opens the page the credits live on — the
 *  precise outcome drill-downs (settled, in review) stay on the key rows
 *  below, where the filter can actually be exact. */
function SectionHead({ label, hint, count, value, onOpen }: {
  label: string; hint: string; count: number; value?: number
  onOpen?: () => void
}) {
  return (
    <div className="cc-sec-head" title={hint}>
      {onOpen
        ? <button type="button" className="cc-sec-label cc-h-link" onClick={onOpen}>
            {label}
          </button>
        : <span className="cc-sec-label">{label}</span>}
      <span className="cc-sec-n">{count.toLocaleString('en-IN')}</span>
      {value !== undefined && <span className="cc-sec-v">{inr(value)}</span>}
    </div>
  )
}

function PipeNode({ label, state, value, onOpen }: {
  label: string; state: 'done' | 'active' | 'idle'; value?: string
  /** omit to leave the stage as a plain label */
  onOpen?: () => void
}) {
  return (
    <div className={`pipe-node pipe-${state}`}>
      <span className="pipe-dot">{state === 'done' ? '✓' : value ?? '·'}</span>
      {onOpen
        ? <button type="button" className="pipe-label cc-h-link" onClick={onOpen}>{label}</button>
        : <span className="pipe-label">{label}</span>}
    </div>
  )
}

export function CommandCenter({
  customers, customerId, onCustomerChange, onNavigate, onOpenQueue, onOpenGold, refreshKey,
}: Props) {
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
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
    const win = resolveWindow(filter)
    fetchOverview(customerId, {
      from: win.from || undefined, to: win.to || undefined,
      operating_units: filter.units ?? undefined,
    })
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
  }, [customerId, filter])

  useEffect(load, [load, refreshKey])
  const scope = `${windowLabel(filter)}${units.length ? ` · ${unitsLabel(filter, units)}` : ''}`
  const filtered = !!data?.filters_applied
  const quiet = filtered && data && data.gold.credits === 0 && data.gold.bills === 0

  // IREPS-only: other receipts are counted once, in Match performance
  const openInScope = data?.open_in_scope
  const credits = data?.gold.credits ?? 0
  // unrecognised receipts (no match signal) are not matchable: every
  // performance figure is over the RECOGNISED credits. This IS
  // out_of_scope_credits — the panel below shows it once, as "Other
  // receipts"; here it only qualifies the bank-only exception count
  const unrecognised = data?.unrecognised_credits ?? 0
  // the matching bill is still in flight in the source system, so the
  // credit could not have matched — reported, not rated (same treatment
  // as an unrecognised receipt, different reason)
  const awaitingStatus = data?.awaiting_status_credits ?? 0
  // the bill export covering this credit's advice has not been ingested
  // yet — excused only until it goes stale (server-side cap)
  const awaitingBillData = data?.awaiting_bill_data_credits ?? 0
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
  // a CREDIT count, like every other figure in the performance panel —
  // matches.OPEN is a MATCH count and belongs to the tiles above
  const inReview = data ? Math.max(0, data.matched_credits - settled) : 0
  // the panel's top line: the server reports the two halves of the
  // window's money, never the whole, so add them back up here
  const creditsValue =
    data && data.in_scope_value !== undefined && data.out_of_scope_value !== undefined
      ? data.in_scope_value + data.out_of_scope_value
      : undefined

  // A heading routes to where its figure actually LIVES. Every number on
  // this page comes from /api/overview — live gold + ledger state — so the
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

  /** A heading whose figure is a gold table: opens it windowed + filtered. */
  const goldLink = (text: string, intent: GoldIntent) => (
    <button type="button" className="cc-h-link" onClick={() => openGold(intent)}>
      {text}
    </button>
  )

  /** A heading whose figure lives in the ledger: same link, but it also
   *  carries the filter that makes the queue show THAT figure. */
  const queueLink = (text: string, intent: LedgerIntent) => (
    <button type="button" className="cc-h-link" onClick={() => openQueue(intent)}>
      {text}
    </button>
  )

  /* The credit funnel exactly as db/overview.py computes it:
       credits       = other receipts + IREPS credits
       IREPS credits = awaiting status + awaiting bill data + recognised
       recognised    = settled + in review + unmatched
     Each section below renders one level, which is why no figure needs to
     appear twice (unrecognised_credits IS out_of_scope_credits — the old
     panel printed that one number as both "Unrecognised receipts" and the
     "other receipts" half of "Credit mix"). */
  const scopeBuckets: Bucket[] = [
    { key: 'ireps', label: 'IREPS credits', count: inScope, tone: 'seg-ireps',
      value: data?.in_scope_value,
      hint: "credits carrying this customer's match signal — the money a bill can settle; opens the credits themselves",
      onOpen: () => openGold(G_IN_SCOPE) },
    { key: 'other', label: 'Other receipts', count: outOfScope, tone: '',
      value: data?.out_of_scope_value,
      hint: 'no match signal in the narrative — interest, sweeps and payers outside IREPS; never matchable, so never in the rate',
      onOpen: () => openQueue(qExcGap('UNRECOGNISED_RECEIPT')) },
  ]
  const outcomeBuckets: Bucket[] = [
    { key: 'settled', label: 'Settled', count: settled, tone: 'seg-settled',
      hint: 'credits with a LOCKED match — auto-locked on HIGH confidence, accepted by a user, or matched by hand',
      onOpen: () => openQueue(Q_SETTLED) },
    { key: 'review', label: 'In review', count: inReview, tone: 'seg-review',
      hint: 'credits whose match is still open — accept or reject it to settle them',
      onOpen: () => openQueue(Q_REVIEW) },
    { key: 'unmatched', label: 'Unmatched', count: unmatched, tone: '',
      hint: 'recognised credits with no match at all — the real gap',
      onOpen: () => openQueue(qExcGap('SIGNAL_BILL_NOT_FOUND')) },
  ]
  const notRated: Bucket[] = [
    { key: 'awaiting_status', label: 'Awaiting source status', tone: '', aside: true,
      count: awaitingStatus,
      hint: 'the same-amount bill is still passed/registered, not advised — the credit could not have matched yet',
      onOpen: () => openQueue(qExcGap('AWAITING_STATUS')) },
    { key: 'awaiting_bill_data', label: 'Awaiting bill data', tone: '', aside: true,
      count: awaitingBillData,
      hint: `valued after the latest bill export${data?.bills_covered_through
        ? ` (advices through ${data.bills_covered_through})` : ''} — excused until it goes stale`,
      onOpen: () => openQueue(qExcGap('AWAITING_BILL_DATA')) },
  ].filter((b) => b.count > 0)

  return (
    <section className="intake cc">
      <div className="ingest-head">
        <div>
          <h2 className="page-title">Command Center</h2>
        </div>
        <span className="cc-head-right">
          <DateFilter value={filter} onChange={setFilter} units={units} unitCounts={unitCounts} />
          <label className="ctx-field">
            <span className="slot-label">Customer</span>
            <select value={customerId} onChange={(e) => onCustomerChange(e.target.value)}>
              {customers.map((c) => (
                <option key={c.key} value={c.key}>{c.name} ({c.key})</option>
              ))}
            </select>
          </label>
          <button className="btn-refresh btn-ic" onClick={load}>
            <RotateCw size={13} strokeWidth={1.75} /> refresh
          </button>
        </span>
      </div>

      {error && <p className="frame-note">could not load overview: {error}</p>}
      {!data && !error && <p className="frame-note"><span className="quill" /> loading…</p>}

      {data && (
        <>
          {quiet && (
            <p className="frame-note">
              nothing in this window ({scope}) — widen the date range or switch to
              “All time” in the filter to see the whole customer.
            </p>
          )}
          {filtered && data.filters_applied && data.filters_applied.bank_only_unassigned > 0
            && !data.filters_applied.unassigned_included && (
            <p className="chip-note cc-filter-note">
              {data.filters_applied.bank_only_unassigned} bank-only credit
              {data.filters_applied.bank_only_unassigned === 1 ? ' has' : 's have'} no operating unit
              and {data.filters_applied.bank_only_unassigned === 1 ? 'is' : 'are'} hidden by the unit filter — tick “Unassigned” to include them.
            </p>
          )}
          <div className="tiles cc-tiles">
            <div className="tile tone-neutral">
              <div className="tile-label">{goldLink('Gold pool', G_BILLS)}</div>
              <div className="tile-count">{data.gold.bills.toLocaleString('en-IN')}</div>
              <div className="tile-amount">bills · {data.gold.credits} credits</div>
              <div className="tile-delta">{data.gold.lineage_docs.toLocaleString('en-IN')} lineage docs · {scope}</div>
            </div>
            <div className="tile">
              <div className="tile-label">{queueLink('Settled', Q_SETTLED)}</div>
              <div className="tile-count">{settled.toLocaleString('en-IN')}</div>
              <div className="tile-amount">{pct(data.match_rate)} of recognised credits · {windowLabel(filter)}</div>
              <div className="tile-delta">{data.matches.OPEN} awaiting review · {data.matches.REJECTED} rejected</div>
            </div>
            <div className="tile tone-review">
              <div className="tile-label">{queueLink('Analyst queue', Q_REVIEW)}</div>
              <div className="tile-count">{data.matches.OPEN}</div>
              <div className="tile-amount">matches awaiting review</div>
              <div className="tile-delta">accept or reject to settle · {scope}</div>
            </div>
            <div className="tile tone-bank">
              <div className="tile-label">{queueLink('Open exceptions', Q_OPEN_EXC)}</div>
              <div className="tile-count">{(openInScope?.count ?? 0).toLocaleString('en-IN')}</div>
              <div className="tile-amount">{inr(openInScope?.value ?? 0)}</div>
              <div className="tile-delta">
                {queueLink(
                  `${(openInScope?.bank_only ?? 0).toLocaleString('en-IN')} bank only`,
                  qExcSide('BANK_ONLY'))}
                {' · '}
                {queueLink(
                  `${(openInScope?.bill_only ?? 0).toLocaleString('en-IN')} bill only`,
                  qExcSide('BILL_ONLY'))}
                {/* not in the count above — the same receipts Match
                    performance shows as "Other receipts" */}
                {outOfScope > 0 && (
                  <>
                    {' · '}
                    {queueLink(
                      `${outOfScope.toLocaleString('en-IN')} not IREPS, excluded`,
                      qExcGap('UNRECOGNISED_RECEIPT'))}
                  </>
                )}
                {' · '}{scope}
              </div>
            </div>
            <div className="tile tone-bill">
              <div className="tile-label">{queueLink('Resolved', Q_RESOLVED_EXC)}</div>
              <div className="tile-count">{data.resolved_exceptions}</div>
              <div className="tile-amount">exceptions closed by runs or decisions</div>
              <div className="tile-delta">{scope}</div>
            </div>
          </div>

          <div className="cc-grid">
            <div className="cc-panel">
              <div className="cc-panel-head">
                <h3 className="ledger-h">{queueLink('Largest open exceptions', Q_OPEN_EXC)}</h3>
                <button className="btn-open" onClick={() => openQueue(Q_OPEN_EXC)}>
                  open Analyst queue →
                </button>
              </div>
              {data.top_exceptions.length === 0 ? (
                <p className="frame-note">
                  nothing open —{' '}
                  <button className="link-btn" onClick={() => onNavigate('reconcile')}>
                    initiate an incremental reconciliation →
                  </button>
                </p>
              ) : (
                <table className="ledger">
                  <thead>
                    <tr>
                      <th>Type</th><th>Ref</th><th>Zone</th><th>Date</th>
                      <th style={{ textAlign: 'right' }}>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_exceptions.map((e) => (
                      <tr key={e.id} className="cc-row"
                          onClick={() => openQueue(qExcSide(e.exception_type))}>
                        <td><span className={`stamp stamp-${e.exception_type}`}>
                          {e.exception_type.replace('_', ' ')}</span></td>
                        <td className="mono-cell">{e.ref ?? '—'}</td>
                        <td>{e.zone ?? '—'}</td>
                        <td className="date">{e.date || '—'}</td>
                        <td className="num">{inr(e.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="cc-panel">
              <div className="cc-panel-head">
                <h3 className="ledger-h">{queueLink('Match performance', Q_ALL_MATCHES)}</h3>
              </div>
              <MatchDonut rate={data.match_rate} />

              <SectionHead label="Credits in window" count={credits}
                           value={creditsValue}
                           hint="every credit the reconciliation saw in this window"
                           onOpen={() => openGold(G_CREDITS)} />
              <CompositionBar buckets={scopeBuckets} total={credits} />
              {scopeBuckets.map((b) => (
                <KeyRow key={b.key} bucket={b} total={credits} />
              ))}

              <SectionHead label="Recognised" count={recognised}
                           hint="IREPS credits that could already have matched — the rate's denominator"
                           onOpen={() => openGold(G_RECOGNISED)} />
              <CompositionBar buckets={outcomeBuckets} total={recognised} />
              {outcomeBuckets.map((b) => (
                <KeyRow key={b.key} bucket={b} total={recognised} />
              ))}
              {notRated.length > 0 && (
                <div className="cc-aside">
                  {notRated.map((b) => (
                    <KeyRow key={b.key} bucket={b} total={recognised} />
                  ))}
                </div>
              )}

              <p className="cc-perf-foot">
                settled by · auto {data.locked_by.AUTO_HIGH.toLocaleString('en-IN')}
                {' · '}accepted {accepted.toLocaleString('en-IN')}
                {' · '}manual {manual.toLocaleString('en-IN')}
              </p>
            </div>
          </div>

          <div className="cc-panel">
            <div className="cc-panel-head">
              <h3 className="ledger-h">Pipeline</h3>
              <span className="cc-actions">
                <button className="btn-open btn-ic" onClick={() => onNavigate('ingest')}>
                  <Upload size={13} strokeWidth={1.75} /> Ingest documents
                </button>
                <button className="btn-open btn-ic" onClick={() => onNavigate('reconcile')}>
                  <GitMerge size={13} strokeWidth={1.75} /> Initiate Reconciliation
                </button>
              </span>
            </div>
            <div className="pipe">
              <PipeNode label="Ingest" state={data.gold.bank_txns > 0 ? 'done' : 'active'}
                        onOpen={() => onNavigate('ingest')} />
              <span className="pipe-link" />
              <PipeNode label="Gold layer"
                        state={data.gold.bills > 0 ? 'done' : 'idle'}
                        value={data.gold.bills ? undefined : '·'}
                        onOpen={() => onNavigate('gold_bills')} />
              <span className="pipe-link" />
              <PipeNode label="Reconcile" state={data.last_run ? 'done' : 'idle'}
                        onOpen={() => onNavigate('reconcile')} />
              <span className="pipe-link" />
              <PipeNode label="Analyst review"
                        state={data.matches.OPEN > 0 ? 'active' : data.last_run ? 'done' : 'idle'}
                        value={data.matches.OPEN > 0 ? String(data.matches.OPEN) : undefined}
                        onOpen={() => openQueue(Q_REVIEW)} />
              <span className="pipe-link" />
              <PipeNode label="Resolved"
                        state={data.resolved_exceptions > 0 ? 'done' : 'idle'}
                        value={data.resolved_exceptions > 0 ? String(data.resolved_exceptions) : undefined}
                        onOpen={() => openQueue(Q_RESOLVED_EXC)} />
            </div>
          </div>
        </>
      )}
    </section>
  )
}
