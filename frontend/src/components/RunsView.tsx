import { useCallback, useEffect, useState } from 'react'
import { RotateCw } from 'lucide-react'
import { fetchRuns } from '../api'
import { ApiError, type RunListItem } from '../types'
import { fmtWhen } from '../format'

interface Props {
  customerId: string
  activeRunId: string | null
  onOpenRun: (runId: string) => void
}

function errorText(e: RunListItem['error']): string {
  if (!e) return ''
  if (typeof e === 'string') return e
  return e.detail ?? e.error ?? ''
}

export function RunsView({ customerId, activeRunId, onOpenRun }: Props) {
  const [items, setItems] = useState<RunListItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    fetchRuns(customerId)
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [customerId])

  useEffect(load, [load])

  return (
    <>
      <div className="result-head">
        <h2>Runs</h2>
        <span className="file-note">
          customer: {customerId}
          <button className="btn-refresh btn-ic" onClick={load}>
            <RotateCw size={13} strokeWidth={1.75} /> refresh
          </button>
        </span>
      </div>

      <div className="view-card">
        {error && <p className="frame-note">Could not load runs: {error}</p>}
        {items && items.length === 0 && (
          <p className="frame-note">No runs yet for this customer.</p>
        )}
        {items && items.length > 0 && (
          <table className="ledger">
            <thead>
              <tr>
                <th>When</th>
                <th>Mode</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Matched</th>
                <th style={{ textAlign: 'right' }}>Bank only</th>
                <th style={{ textAlign: 'right' }}>Bill only</th>
                <th>Detail</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.run_id} className={r.run_id === activeRunId ? 'active-run' : ''}>
                  <td>{fmtWhen(r.created_at)}</td>
                  <td><span className="stamp">{r.mode}</span></td>
                  <td><span className={`stamp stamp-${r.status}`}>{r.status}</span></td>
                  <td className="num">{r.counts?.matched ?? '—'}</td>
                  <td className="num">{r.counts?.bank_only ?? '—'}</td>
                  <td className="num">{r.counts?.bill_only ?? '—'}</td>
                  <td className="run-error">{errorText(r.error).slice(0, 90)}</td>
                  <td>
                    {r.status === 'succeeded' && (
                      <button
                        className="btn-open"
                        disabled={r.run_id === activeRunId}
                        onClick={() => onOpenRun(r.run_id)}
                      >
                        {r.run_id === activeRunId ? 'loaded' : 'Open'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="footer-note">
        Runs are persisted — a page refresh or restart never loses them. Opening a run restores its
        summary, matched rows, exception queue and source tabs exactly as produced.
      </p>
    </>
  )
}
