import { useCallback, useEffect, useState } from 'react'
import { GitMerge, RotateCw, Upload } from 'lucide-react'
import type { CustomerInfo, Overview } from '../types'
import { fetchOperatingUnits, fetchOverview } from '../api'
import { inr } from '../format'
import type { View } from './Sidebar'
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
  refreshKey: number
}

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

function Meter({ label, count, total, tone }: {
  label: string; count: number; total: number; tone: string
}) {
  const w = total > 0 ? (count / total) * 100 : 0
  return (
    <div className="cc-meter-row">
      <span className="cc-meter-label">{label}</span>
      <div className="cc-meter-track">
        <div className={`cc-meter-fill ${tone}`} style={{ width: `${w}%` }} />
      </div>
      <span className="cc-meter-count">{count}</span>
    </div>
  )
}

function PipeNode({ label, state, value }: {
  label: string; state: 'done' | 'active' | 'idle'; value?: string
}) {
  return (
    <div className={`pipe-node pipe-${state}`}>
      <span className="pipe-dot">{state === 'done' ? '✓' : value ?? '·'}</span>
      <span className="pipe-label">{label}</span>
    </div>
  )
}

export function CommandCenter({
  customers, customerId, onCustomerChange, onNavigate, refreshKey,
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

  const openTotal = data
    ? data.open_exceptions.BANK_ONLY + data.open_exceptions.BILL_ONLY
    : 0
  const credits = data?.gold.credits ?? 0
  // unrecognised receipts (no match signal) are not matchable: every
  // performance figure is over the RECOGNISED credits, and they are
  // reported on their own line
  const unrecognised = data?.unrecognised_credits ?? 0
  const recognised = data?.recognised_credits ?? Math.max(0, credits - unrecognised)
  const unmatched = data ? Math.max(0, recognised - data.matched_credits) : 0
  const settled = data?.settled_credits ?? data?.matches.LOCKED ?? 0
  const manual = data?.manual_matches ?? 0
  // locked_by.USER counts every user-locked match, manual ones included
  const accepted = Math.max(0, (data?.locked_by.USER ?? 0) - manual)

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
              <div className="tile-label">Gold pool</div>
              <div className="tile-count">{data.gold.bills.toLocaleString('en-IN')}</div>
              <div className="tile-amount">bills · {data.gold.credits} credits</div>
              <div className="tile-delta">{data.gold.lineage_docs.toLocaleString('en-IN')} lineage docs · {scope}</div>
            </div>
            <div className="tile">
              <div className="tile-label">Settled</div>
              <div className="tile-count">{settled.toLocaleString('en-IN')}</div>
              <div className="tile-amount">{pct(data.match_rate)} of recognised credits · {windowLabel(filter)}</div>
              <div className="tile-delta">{data.matches.OPEN} awaiting review · {data.matches.REJECTED} rejected</div>
            </div>
            <div className="tile tone-review">
              <div className="tile-label">Analyst queue</div>
              <div className="tile-count">{data.matches.OPEN}</div>
              <div className="tile-amount">matches awaiting review</div>
              <div className="tile-delta">accept or reject to settle · {scope}</div>
            </div>
            <div className="tile tone-bank">
              <div className="tile-label">Open exceptions</div>
              <div className="tile-count">{openTotal.toLocaleString('en-IN')}</div>
              <div className="tile-amount">{inr(data.open_value.total)}</div>
              <div className="tile-delta">
                {data.open_exceptions.BANK_ONLY} bank only
                {unrecognised > 0 && ` (${unrecognised} unrecognised)`}
                {' · '}{data.open_exceptions.BILL_ONLY.toLocaleString('en-IN')} bill only · {scope}
              </div>
            </div>
            <div className="tile tone-bill">
              <div className="tile-label">Resolved</div>
              <div className="tile-count">{data.resolved_exceptions}</div>
              <div className="tile-amount">exceptions closed by runs or decisions</div>
              <div className="tile-delta">{scope}</div>
            </div>
          </div>

          <div className="cc-grid">
            <div className="cc-panel">
              <div className="cc-panel-head">
                <h3 className="ledger-h">Largest open exceptions</h3>
                <button className="btn-open" onClick={() => onNavigate('ledger')}>
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
                      <tr key={e.id} className="cc-row" onClick={() => onNavigate('ledger')}>
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
                <h3 className="ledger-h">Match performance</h3>
              </div>
              <MatchDonut rate={data.match_rate} />
              <p className="cc-settled-split">
                <strong>{settled.toLocaleString('en-IN')}</strong> settled ·{' '}
                {data.locked_by.AUTO_HIGH.toLocaleString('en-IN')} auto-locked ·{' '}
                {accepted.toLocaleString('en-IN')} accepted by user ·{' '}
                {manual.toLocaleString('en-IN')} matched by user
              </p>
              <div className="cc-meters">
                <Meter label="Auto-locked (HIGH)" count={data.locked_by.AUTO_HIGH}
                       total={recognised} tone="fill-green" />
                <Meter label="Locked by user" count={data.locked_by.USER}
                       total={recognised} tone="fill-green-soft" />
                <Meter label="Matched by user (manual)" count={data.manual_matches ?? 0}
                       total={recognised} tone="fill-green-soft" />
                <Meter label="Open review" count={data.matches.OPEN}
                       total={recognised} tone="fill-gold" />
                <Meter label="Unmatched credits" count={unmatched}
                       total={recognised} tone="fill-sienna" />
              </div>
              <p className="frame-note cc-unrec">
                Unrecognised receipts · <strong>{unrecognised.toLocaleString('en-IN')}</strong>
                {' '}— no match signal in the narrative; not counted in the rate
                ({recognised.toLocaleString('en-IN')} recognised of {credits.toLocaleString('en-IN')} credits)
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
              <PipeNode label="Ingest" state={data.gold.bank_txns > 0 ? 'done' : 'active'} />
              <span className="pipe-link" />
              <PipeNode label="Gold layer"
                        state={data.gold.bills > 0 ? 'done' : 'idle'}
                        value={data.gold.bills ? undefined : '·'} />
              <span className="pipe-link" />
              <PipeNode label="Reconcile" state={data.last_run ? 'done' : 'idle'} />
              <span className="pipe-link" />
              <PipeNode label="Analyst review"
                        state={data.matches.OPEN > 0 ? 'active' : data.last_run ? 'done' : 'idle'}
                        value={data.matches.OPEN > 0 ? String(data.matches.OPEN) : undefined} />
              <span className="pipe-link" />
              <PipeNode label="Resolved"
                        state={data.resolved_exceptions > 0 ? 'done' : 'idle'}
                        value={data.resolved_exceptions > 0 ? String(data.resolved_exceptions) : undefined} />
            </div>
          </div>
        </>
      )}
    </section>
  )
}
