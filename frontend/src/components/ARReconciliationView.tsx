import { useCallback, useEffect, useMemo, useState } from 'react'
import { RotateCw } from 'lucide-react'
import type { ArRow, ArStatus, ArView, RunListItem } from '../types'
import { fetchAr, fetchOperatingUnits, fetchRuns } from '../api'
import { inDayRange, inr } from '../format'
import { FitText } from './FitText'
import {
  EMPTY_RUN_FILTER, RunFilter, runFilterSet, runLabelFor, type RunFilterValue,
} from './RunFilter'
import {
  DateFilter, UNASSIGNED_UNIT, resolveWindow, unitsLabel, windowLabel,
  type DateFilterValue,
} from './DateFilter'

/** AR opens on the whole working set; the quick picks are one click away. */
const AR_DEFAULT_FILTER: DateFilterValue = { pick: 'all', from: '', to: '', units: null }

/** The day a row is filtered on: the credit's value date for settled /
 *  in-review rows, the bill's aging anchor (advice → order → submission,
 *  already resolved server-side as due_date) for outstanding ones. */
function rowDay(r: ArRow): string | null {
  if (r.status === 'SETTLED' || r.status === 'IN_REVIEW') return r.pay?.value_date ?? r.due_date
  return r.due_date
}

function inUnits(r: ArRow, units: string[] | null): boolean {
  if (!units) return true
  const u = r.org_unit_operating ?? null
  return u ? units.includes(u) : units.includes(UNASSIGNED_UNIT)
}

interface Props {
  customerId: string
  refreshKey: number
  /** settled / in-review rows link into the Analyst queue */
  onOpenInQueue: (matchLedgerId: string) => void
}

type Filter = 'ALL' | ArStatus

const FILTERS: Array<[Filter, string]> = [
  ['ALL', 'All'],
  ['OVERDUE', 'Overdue'],
  ['AWAITING', 'Awaiting'],
  ['IN_REVIEW', 'In review'],
  ['SETTLED', 'Settled'],
]

const STATUS_LABEL: Record<ArStatus, string> = {
  SETTLED: 'SETTLED',
  IN_REVIEW: 'IN REVIEW',
  AWAITING: 'AWAITING',
  OVERDUE: 'OVERDUE',
}

const pct = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)

const BUCKET_TONE: Record<string, string> = {
  '0-30': 'fill-green', '31-60': 'fill-gold', '61-90': 'fill-gold',
  '90+': 'fill-sienna', undated: 'fill-green-soft',
}

export function ARReconciliationView({ customerId, refreshKey, onOpenInQueue }: Props) {
  const [data, setData] = useState<ArView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('ALL')
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [runFilter, setRunFilter] = useState<RunFilterValue>(EMPTY_RUN_FILTER)
  const [dateFilter, setDateFilter] = useState<DateFilterValue>(AR_DEFAULT_FILTER)
  const [units, setUnits] = useState<string[]>([])

  const load = useCallback(() => {
    setError(null)
    fetchAr(customerId)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
    fetchRuns(customerId, 200).then(setRuns).catch(() => setRuns([]))
  }, [customerId])

  useEffect(load, [load, refreshKey])
  useEffect(() => { setRunFilter(EMPTY_RUN_FILTER); setDateFilter(AR_DEFAULT_FILTER) }, [customerId])
  useEffect(() => {
    fetchOperatingUnits(customerId)
      .then((r) => setUnits(r.units.map((u) => u.unit)))
      .catch(() => setUnits([]))
  }, [customerId, refreshKey])

  const runSet = useMemo(() => runFilterSet(runFilter, runs), [runFilter, runs])
  // item 2.2: the run filter AND the date/unit filter narrow the working
  // set, and the KPI tiles + aging bars follow it — the page reads as one
  // scope (the whole-customer position is one "All time · all runs" away)
  const win = resolveWindow(dateFilter)
  const inScope = (data?.rows ?? []).filter((r) => {
    if (runSet && !(!!r.run_id && runSet.has(r.run_id))) return false
    if (win.from || win.to) {
      const day = rowDay(r)
      if (!day || !inDayRange(day, win.from, win.to)) return false
    }
    return inUnits(r, dateFilter.units)
  })
  const rows = inScope.filter((r) => filter === 'ALL' || r.status === filter)
  const statusCounts = inScope.reduce<Record<string, number>>(
    (acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {})
  const scoped = !!runSet || win.from !== '' || win.to !== '' || dateFilter.units !== null
  const runNote = data && scoped ? `${inScope.length} of ${data.rows.length} bills` : undefined

  // KPIs + aging over the rows in scope (server KPIs are whole-customer)
  const kpis = useMemo(() => {
    const open = inScope.filter((r) => r.status === 'AWAITING' || r.status === 'OVERDUE')
    const overdue = open.filter((r) => r.status === 'OVERDUE')
    const settledTxns = new Map<string, { amount: number; value_date: string | null }>()
    for (const r of inScope) {
      if (r.status === 'SETTLED' && r.pay?.bank_ref) {
        settledTxns.set(`${r.pay.bank_ref}|${r.pay.value_date}`,
          { amount: r.pay.amount ?? 0, value_date: r.pay.value_date })
      }
    }
    const now = new Date()
    const ym = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    const received = [...settledTxns.values()]
    const sum = (xs: Array<number | null>) => xs.reduce<number>((s, v) => s + (v ?? 0), 0)
    // denominator: statement credits of the runs in scope (all runs when
    // no run filter), from the per-run counts the payload carries
    const runsInScope = (data?.runs ?? []).filter((r) => !runSet || runSet.has(r.run_id))
    const credits = runsInScope.reduce<number>((s, r) => s + (r.credits ?? 0), 0)
    return {
      outstanding: { count: open.length, value: sum(open.map((r) => r.net_payable_amount)) },
      overdue: { count: overdue.length, value: sum(overdue.map((r) => r.net_payable_amount)) },
      received: {
        count: received.length,
        value: sum(received.map((t) => t.amount)),
        mtd_value: sum(received.filter((t) => (t.value_date ?? '').startsWith(ym)).map((t) => t.amount)),
      },
      match_rate: credits > 0 ? Math.min(1, received.length / credits) : null,
      aging: (() => {
        const buckets: Array<[string, number, number | null]> = [
          ['0-30', 0, 30], ['31-60', 31, 60], ['61-90', 61, 90], ['90+', 91, null]]
        const out = buckets.map(([bucket, lo, hi]) => {
          const hit = open.filter((r) => r.age_days !== null && r.age_days >= lo && (hi === null || r.age_days <= hi))
          return { bucket, count: hit.length, value: sum(hit.map((r) => r.net_payable_amount)) }
        })
        const undated = open.filter((r) => r.age_days === null)
        out.push({ bucket: 'undated', count: undated.length, value: sum(undated.map((r) => r.net_payable_amount)) })
        return out
      })(),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inScope, data, runSet])
  const agingMax = Math.max(1, ...kpis.aging.map((b) => b.value))
  const scopeLabel = `${windowLabel(dateFilter)}${units.length ? ` · ${unitsLabel(dateFilter, units)}` : ''}${runSet ? ' · selected runs' : ''}`

  const rowClick = (r: ArRow) => {
    if (r.match_ledger_id) onOpenInQueue(r.match_ledger_id)
  }

  return (
    <section className="intake">
      <div className="ingest-head">
        <h2 className="page-title">AR Reconciliation</h2>
        <span className="file-note">
          <button className="btn-refresh btn-ic" onClick={load}>
            <RotateCw size={13} strokeWidth={1.75} /> refresh
          </button>
          <DateFilter value={dateFilter} onChange={setDateFilter} units={units} />
          {runs.length > 0 && (
            <RunFilter runs={runs} value={runFilter} onChange={setRunFilter}
                       note={runNote} />
          )}
        </span>
      </div>

      {error && <p className="frame-note">could not load AR view: {error}</p>}
      {!data && !error && <p className="frame-note"><span className="quill" /> loading…</p>}

      {data && data.rows.length === 0 && (
        <p className="frame-note">
          The AR working set fills up when you run in incremental mode — settled matches and
          outstanding bills will appear here.
        </p>
      )}

      {data && data.rows.length > 0 && (
        <>
          <div className="tiles cc-tiles ar-tiles">
            <div className="tile tone-bill">
              <div className="tile-label">AR outstanding</div>
              <div className="tile-count"><FitText>{inr(kpis.outstanding.value)}</FitText></div>
              <div className="tile-amount">{kpis.outstanding.count.toLocaleString('en-IN')} bills awaiting credit</div>
              <div className="tile-delta">{scopeLabel}</div>
            </div>
            <div className="tile">
              <div className="tile-label">Received</div>
              <div className="tile-count"><FitText>{inr(kpis.received.value)}</FitText></div>
              <div className="tile-amount">{kpis.received.count} credits settled</div>
              <div className="tile-delta">{inr(kpis.received.mtd_value)} this month · {scopeLabel}</div>
            </div>
            <div className="tile tone-neutral">
              <div className="tile-label">Match rate</div>
              <div className="tile-count"><FitText>{pct(kpis.match_rate)}</FitText></div>
              <div className="tile-amount">of statement credits{runSet ? ' in the selected runs' : ''}</div>
              <div className="tile-delta">{scopeLabel}</div>
            </div>
            <div className="tile tone-bank">
              <div className="tile-label">Overdue &gt; 30d</div>
              <div className="tile-count"><FitText>{inr(kpis.overdue.value)}</FitText></div>
              <div className="tile-amount">{kpis.overdue.count.toLocaleString('en-IN')} bills</div>
              <div className="tile-delta">{scopeLabel}</div>
            </div>
          </div>

          <div className="ar-grid">
            <div className="cc-panel">
              <div className="cc-panel-head">
                <h3 className="ledger-h">
                  Bills ↔ payments
                  {scoped && <span className="chip-note"> {runNote}</span>}
                </h3>
                <span className="seg">
                  {FILTERS.map(([f, label]) => (
                    <button key={f} className={filter === f ? 'on' : ''}
                            onClick={() => setFilter(f)}>
                      {label}
                    </button>
                  ))}
                </span>
              </div>
              {rows.length === 0 ? (
                <p className="frame-note">
                  nothing with this status{scoped ? ' in this scope' : ''}
                </p>
              ) : (
                <div className="ledger-wrap ar-table-wrap">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>Bill no.</th><th>Zone</th><th>Due date</th>
                        <th style={{ textAlign: 'right' }}>Net payable</th>
                        <th>Pay ref</th>
                        <th style={{ textAlign: 'right' }}>Paid amt</th>
                        <th>Value date</th>
                        <th style={{ textAlign: 'right' }}>Variance</th>
                        <th>Match</th><th>Run</th><th>Status</th><th>Age</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={r.match_ledger_id ?? r.exception_id ?? i}
                            className={r.match_ledger_id ? 'ar-row-link' : ''}
                            title={r.match_ledger_id ? 'open in Analyst queue' : undefined}
                            onClick={() => rowClick(r)}>
                          <td className="mono-cell">{r.bill_number ?? '—'}</td>
                          <td>{r.zone ?? '—'}</td>
                          <td className="date">{r.due_date ?? '—'}</td>
                          <td className="num">{r.net_payable_amount !== null ? inr(r.net_payable_amount) : '—'}</td>
                          <td className="mono-cell">{r.pay?.bank_ref ?? '—'}</td>
                          <td className="num">{r.pay?.amount != null ? inr(r.pay.amount) : '—'}</td>
                          <td className="date">{r.pay?.value_date ?? '—'}</td>
                          <td className="num">
                            {r.variance === null ? '—'
                              : r.variance === 0 ? <span className="empty-cell">₹0</span>
                              : <span className="ar-variance">{inr(r.variance)}</span>}
                          </td>
                          <td>{r.match_seq !== null ? `M-${r.match_seq}` : '—'}</td>
                          <td className="run-cell" title={r.run_id ?? undefined}>
                            {runLabelFor(runs, r.run_id)}
                          </td>
                          <td><span className={`stamp stamp-ar-${r.status}`}>
                            {STATUS_LABEL[r.status]}</span></td>
                          <td>
                            {r.age_days !== null && r.status !== 'SETTLED' && r.status !== 'IN_REVIEW'
                              ? <span className={r.status === 'OVERDUE' ? 'ar-age-hot' : ''}>{r.age_days}d</span>
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="ar-side">
              <div className="cc-panel">
                <div className="cc-panel-head">
                  <h3 className="ledger-h">Aging analysis</h3>
                </div>
                {kpis.aging.filter((b) => b.count > 0 || b.bucket !== 'undated').map((b) => (
                  <div key={b.bucket} className="cc-meter-row">
                    <span className="cc-meter-label">{b.bucket === 'undated' ? 'no due date' : `${b.bucket} days`}</span>
                    <div className="cc-meter-track">
                      <div className={`cc-meter-fill ${BUCKET_TONE[b.bucket]}`}
                           style={{ width: `${(b.value / agingMax) * 100}%` }} />
                    </div>
                    <span className="cc-meter-count">{b.count.toLocaleString('en-IN')}</span>
                  </div>
                ))}
                <p className="chip-note ar-aging-note">
                  bars = ₹ value per bucket · counts at right · age runs from the
                  advice / payment-order date
                </p>
              </div>

              <div className="cc-panel">
                <div className="cc-panel-head">
                  <h3 className="ledger-h">Status breakdown</h3>
                </div>
                {(['OVERDUE', 'AWAITING', 'IN_REVIEW', 'SETTLED'] as ArStatus[]).map((s) => (
                  <div key={s} className="ar-status-row">
                    <span className={`stamp stamp-ar-${s}`}>{STATUS_LABEL[s]}</span>
                    <span className="ar-status-count">{(statusCounts[s] ?? 0).toLocaleString('en-IN')}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}

      <p className="footer-note">
        Every row is live AR state: settled and in-review rows come from the durable match
        ledger (click one to decide it in the Analyst queue); outstanding rows are open
        bill-side exceptions aged from the day the money was advised.
      </p>
    </section>
  )
}
