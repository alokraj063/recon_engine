import { useCallback, useEffect, useState } from 'react'
import { fetchIngestions } from '../api'
import { ApiError, type IngestionListItem } from '../types'
import { fmtWhen } from '../format'
import {
  IngestStatsSummary, fileOutcomeLabel, fileOutcomeShort, sourceRoleTag,
} from './IngestStatsSummary'
import { Card, EmptyState, Notice, RefreshButton } from './ui'

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
    <Card title="All ingestions" ruled
          action={<RefreshButton onClick={load} label="Refresh ingestions" />}>
        {error && <div className="dt-state"><Notice tone="error">Could not load ingestions: {error}</Notice></div>}
        {!items && !error && <div className="dt-loading"><span className="quill" /> Loading…</div>}
        {items && items.length === 0 && (
          <EmptyState title="No ingestions" />
        )}
        {items && items.length > 0 && (
          <div className="ledger-wrap ingestions-wrap">
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
                <th>Records</th>
                <th>Statement check</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => {
                const allDup = i.files.length > 0 && i.files.every((f) => f.outcome === 'deduped')
                return (
                <tr key={i.id}>
                  <td className="when-cell">{fmtWhen(i.at)}</td>
                  <td className="files-cell">
                    {i.files.map((f, k) => {
                      const name = f.original_name ?? `file #${f.bronze_file_id}`
                      const isNew = f.outcome === 'registered'
                      return (
                        <span key={`${f.bronze_file_id}-${k}`}
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
                    {i.selfcheck_passed === true && <span className="ui-pill tone-ok">passed</span>}
                    {i.selfcheck_passed === false && <span className="ui-pill tone-bad">failed</span>}
                    {i.selfcheck_passed == null && (
                      <span className="empty-cell" title="no bank statement in this ingestion">—</span>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        )}
    </Card>
  )
}
