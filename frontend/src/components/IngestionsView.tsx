import { useCallback, useEffect, useState } from 'react'
import { fetchIngestions } from '../api'
import { ApiError, type IngestionListItem } from '../types'
import { fmtWhen } from '../format'
import {
  IngestStatsSummary, fileOutcomeLabel, fileOutcomeShort, sourceRoleTag,
} from './IngestStatsSummary'

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
          <table className="ledger ingestions">
            <colgroup>
              <col className="col-when" />
              <col className="col-files" />
              <col className="col-gold" />
              <col className="col-check" />
            </colgroup>
            <thead>
              <tr>
                <th>When</th>
                <th>Files</th>
                <th>What landed in gold</th>
                <th>Self-check</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => {
                const allDup = i.files.length > 0 && i.files.every((f) => f.outcome === 'deduped')
                return (
                <tr key={i.id}>
                  <td className="when-cell">{fmtWhen(i.at)}</td>
                  <td className="files-cell">
                    {i.files.map((f) => {
                      const name = f.original_name ?? `file #${f.bronze_file_id}`
                      const isNew = f.outcome === 'registered'
                      return (
                        <span key={f.bronze_file_id}
                              className={`chip file-chip${isNew ? ' chip-settled' : ''}`}
                              title={`${name} · ${fileOutcomeLabel(f.outcome)}`}>
                          <span className="file-chip-role">{sourceRoleTag(f.source_type)}</span>
                          <span className="file-chip-name">{name}</span>
                          <span className="file-chip-outcome">{fileOutcomeShort(f.outcome)}</span>
                        </span>
                      )
                    })}
                    {allDup && (
                      <span className="files-all-dup">
                        all files identical to earlier uploads
                      </span>
                    )}
                  </td>
                  <td className="gold-cell"><IngestStatsSummary compact stats={i.stats} /></td>
                  <td className="check-cell">
                    {i.selfcheck_passed === true && <span className="stamp stamp-succeeded">passed</span>}
                    {i.selfcheck_passed === false && <span className="stamp stamp-failed">failed</span>}
                    {i.selfcheck_passed == null && (
                      <span className="empty-cell" title="no bank statement in this ingestion">—</span>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <p className="footer-note">
        Every ingestion is idempotent: an identical file is recognised and not ingested twice, and
        a newer bills export updates existing bills in place instead of duplicating them — so an
        export can carry thousands of bills and add none as new (they count as duplicates, not
        added). Each file stays browsable as its own ingestion either way. Bills consumed by a
        LOCKED ledger match are never silently changed — attempted changes land in conflicts.
      </p>
    </>
  )
}
