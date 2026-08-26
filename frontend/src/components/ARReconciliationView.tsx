import { useCallback, useEffect, useState } from 'react'
import { RotateCw } from 'lucide-react'
import type { ArRow, ArStatus, ArView } from '../types'
import { fetchAr } from '../api'
import { inr } from '../format'

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

  const load = useCallback(() => {
    setError(null)
    fetchAr(customerId)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  useEffect(load, [load, refreshKey])

  const rows = (data?.rows ?? []).filter(
    (r) => filter === 'ALL' || r.status === filter)
  const agingMax = Math.max(1, ...(data?.aging ?? []).map((b) => b.value))
  const statusCounts = (data?.rows ?? []).reduce<Record<string, number>>(
    (acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {})

  const rowClick = (r: ArRow) => {
    if (r.match_ledger_id) onOpenInQueue(r.match_ledger_id)
  }

  return (
    <section className="intake">
      <div className="ingest-head">
        <div>
          <h2>AR Reconciliation</h2>
          <p className="strap-note">
            customer payments · what settled, what is owed, and for how long
            {data && ` · as of ${data.as_of}`}
          </p>
        </div>
        <button className="btn-refresh btn-ic" onClick={load}>
          <RotateCw size={13} strokeWidth={1.75} /> refresh
        </button>
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
          <div className="tiles cc-tiles">
            <div className="tile tone-bill">
              <div className="tile-label">AR outstanding</div>
              <div className="tile-count">{inr(data.kpis.outstanding.value)}</div>
              <div className="tile-amount">{data.kpis.outstanding.count.toLocaleString('en-IN')} bills awaiting credit</div>
            </div>
            <div className="tile">
              <div className="tile-label">Received</div>
              <div className="tile-count">{inr(data.kpis.received.value)}</div>
              <div className="tile-amount">{data.kpis.received.count} credits settled</div>
              <div className="tile-delta">{inr(data.kpis.received.mtd_value)} this month</div>
            </div>
            <div className="tile tone-neutral">
              <div className="tile-label">Match rate</div>
              <div className="tile-count">{pct(data.kpis.match_rate)}</div>
              <div className="tile-amount">of statement credits</div>
            </div>
            <div className="tile tone-bank">
              <div className="tile-label">Overdue &gt; 30d</div>
              <div className="tile-count">{inr(data.kpis.overdue.value)}</div>
              <div className="tile-amount">{data.kpis.overdue.count.toLocaleString('en-IN')} bills</div>
            </div>
          </div>

          <div className="ar-grid">
            <div className="cc-panel">
              <div className="cc-panel-head">
                <h3 className="ledger-h">Bills ↔ payments</h3>
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
                <p className="frame-note">nothing with this status</p>
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
                        <th>Match</th><th>Status</th><th>Age</th>
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
                          <td>{r.due_date ?? '—'}</td>
                          <td className="num">{r.net_payable_amount !== null ? inr(r.net_payable_amount) : '—'}</td>
                          <td className="mono-cell">{r.pay?.bank_ref ?? '—'}</td>
                          <td className="num">{r.pay?.amount != null ? inr(r.pay.amount) : '—'}</td>
                          <td>{r.pay?.value_date ?? '—'}</td>
                          <td className="num">
                            {r.variance === null ? '—'
                              : r.variance === 0 ? <span className="empty-cell">₹0</span>
                              : <span className="ar-variance">{inr(r.variance)}</span>}
                          </td>
                          <td>{r.match_seq !== null ? `M-${r.match_seq}` : '—'}</td>
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
                {data.aging.filter((b) => b.count > 0 || b.bucket !== 'undated').map((b) => (
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
