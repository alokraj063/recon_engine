import { useCallback, useEffect, useState } from 'react'
import { GitMerge, RotateCw, Upload } from 'lucide-react'
import type { CustomerInfo, Overview } from '../types'
import { fetchOverview } from '../api'
import { fmtWhen, inr } from '../format'
import type { View } from './Sidebar'

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
        <span className="cc-donut-sub">credits settled</span>
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

  const load = useCallback(() => {
    setError(null)
    fetchOverview(customerId)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  useEffect(load, [load, refreshKey])

  const openTotal = data
    ? data.open_exceptions.BANK_ONLY + data.open_exceptions.BILL_ONLY
    : 0
  const credits = data?.gold.credits ?? 0
  const unmatched = data ? Math.max(0, credits - data.matched_credits) : 0

  return (
    <section className="intake cc">
      <div className="ingest-head">
        <div>
          <h2>Command Center</h2>
          <p className="strap-note">
            {data?.last_run
              ? `last run ${fmtWhen(data.last_run.created_at)} · ${data.last_run.mode}`
              : 'no runs yet'}
            {data?.last_ingestion &&
              ` · last ingest ${fmtWhen(data.last_ingestion.at)}`}
          </p>
        </div>
        <span className="cc-head-right">
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
          <div className="tiles cc-tiles">
            <div className="tile tone-neutral">
              <div className="tile-label">Gold pool</div>
              <div className="tile-count">{data.gold.bills.toLocaleString('en-IN')}</div>
              <div className="tile-amount">bills · {data.gold.credits} credits</div>
              <div className="tile-delta">{data.gold.lineage_docs.toLocaleString('en-IN')} lineage docs</div>
            </div>
            <div className="tile">
              <div className="tile-label">Matched</div>
              <div className="tile-count">{data.matched_credits}</div>
              <div className="tile-amount">{pct(data.match_rate)} of credits</div>
              <div className="tile-delta">{data.matches.LOCKED} locked · {data.matches.REJECTED} rejected</div>
            </div>
            <div className="tile tone-review">
              <div className="tile-label">Analyst queue</div>
              <div className="tile-count">{data.matches.OPEN}</div>
              <div className="tile-amount">matches awaiting review</div>
              <div className="tile-delta">accept or reject to settle</div>
            </div>
            <div className="tile tone-bank">
              <div className="tile-label">Open exceptions</div>
              <div className="tile-count">{openTotal.toLocaleString('en-IN')}</div>
              <div className="tile-amount">{inr(data.open_value.total)}</div>
              <div className="tile-delta">
                {data.open_exceptions.BANK_ONLY} bank only · {data.open_exceptions.BILL_ONLY.toLocaleString('en-IN')} bill only
              </div>
            </div>
            <div className="tile tone-bill">
              <div className="tile-label">Resolved</div>
              <div className="tile-count">{data.resolved_exceptions}</div>
              <div className="tile-amount">exceptions closed by later runs</div>
              <div className="tile-delta">&nbsp;</div>
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
                <p className="frame-note">nothing open — run an incremental reconcile to populate the ledger</p>
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
              <div className="cc-meters">
                <Meter label="Auto-locked (HIGH)" count={data.locked_by.AUTO_HIGH}
                       total={credits} tone="fill-green" />
                <Meter label="Locked by user" count={data.locked_by.USER}
                       total={credits} tone="fill-green-soft" />
                <Meter label="Open review" count={data.matches.OPEN}
                       total={credits} tone="fill-gold" />
                <Meter label="Unmatched credits" count={unmatched}
                       total={credits} tone="fill-sienna" />
              </div>
            </div>
          </div>

          <div className="cc-panel">
            <div className="cc-panel-head">
              <h3 className="ledger-h">Pipeline</h3>
              <span className="cc-actions">
                <button className="btn-open btn-ic" onClick={() => onNavigate('ingest')}>
                  <Upload size={13} strokeWidth={1.75} /> Ingest files
                </button>
                <button className="btn-open btn-ic" onClick={() => onNavigate('reconcile')}>
                  <GitMerge size={13} strokeWidth={1.75} /> Run reconciliation
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
