import { useCallback, useEffect, useState } from 'react'
import { fetchIngestions } from '../api'
import { ApiError, type IngestionListItem } from '../types'
import { fmtWhen } from '../format'

interface Props {
  customerId: string
  refreshKey: number
}

export function IngestionsView({ customerId, refreshKey }: Props) {
  const [items, setItems] = useState<IngestionListItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    fetchIngestions(customerId)
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [customerId])

  useEffect(load, [load, refreshKey])

  return (
    <>
      <div className="result-head">
        <h2>Ingestions</h2>
        <span className="file-note">
          customer: {customerId}
          <button className="btn-refresh" onClick={load}>↻ refresh</button>
        </span>
      </div>

      <div className="view-card">
        {error && <p className="frame-note">Could not load ingestions: {error}</p>}
        {items && items.length === 0 && (
          <p className="frame-note">
            No ingestions yet for this customer — use the Ingest files view to load source
            documents into the gold layer.
          </p>
        )}
        {items && items.length > 0 && (
          <table className="ledger">
            <thead>
              <tr>
                <th>When</th>
                <th>Files</th>
                <th style={{ textAlign: 'right' }}>Rows inserted</th>
                <th style={{ textAlign: 'right' }}>Bills updated</th>
                <th style={{ textAlign: 'right' }}>Reused</th>
                <th style={{ textAlign: 'right' }}>Conflicts</th>
                <th>Self-check</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id}>
                  <td>{fmtWhen(i.at)}</td>
                  <td>
                    {i.files.map((f) => (
                      <span key={f.bronze_file_id}
                            className={`chip${f.outcome === 'registered' ? ' chip-settled' : ''}`}>
                        {f.original_name ?? `file #${f.bronze_file_id}`} · {f.outcome}
                      </span>
                    ))}
                  </td>
                  <td className="num">{i.stats?.rows_inserted ?? '—'}</td>
                  <td className="num">{i.stats?.bills_updated ?? '—'}</td>
                  <td className="num">
                    {i.stats ? i.stats.rows_reused + i.stats.files_reused : '—'}
                  </td>
                  <td className="num">{i.stats?.conflicts ?? '—'}</td>
                  <td>
                    {i.selfcheck_passed === true && <span className="stamp stamp-succeeded">passed</span>}
                    {i.selfcheck_passed === false && <span className="stamp stamp-failed">failed</span>}
                    {i.selfcheck_passed == null && '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="footer-note">
        Every ingestion is idempotent: re-uploading identical bytes dedups at the file level, and
        a newer bills export updates existing bills in place instead of duplicating them. Bills
        consumed by a LOCKED ledger match are never silently changed — attempted changes land in
        conflicts.
      </p>
    </>
  )
}
