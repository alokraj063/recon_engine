import { useState } from 'react'
import type { Row } from '../types'
import { fetchFrame } from '../api'
import { LineageTimeline } from './LineageTimeline'

/**
 * Click-to-open document timeline for one bill, anywhere a bill shows up
 * (candidate cards, matched evidence, ledger rows). The full lineage row
 * — TRAIL columns, earlier Attempts, Settled_* closure — lives in the
 * creating run's persisted bills_enriched frame (grouped, one row per
 * bill), so that is the source of truth; the caller's own row is only a
 * partial fallback when the frame can't be read.
 */

// one bills_enriched fetch per run no matter how many bills open
const frameCache = new Map<string, Promise<Row[]>>()

function enrichedRows(runId: string): Promise<Row[]> {
  let p = frameCache.get(runId)
  if (!p) {
    p = fetchFrame(runId, 'bills_enriched').then((d) => d.rows)
    // a failed fetch must not poison the cache
    p.catch(() => frameCache.delete(runId))
    frameCache.set(runId, p)
  }
  return p
}

const norm = (v: unknown): string => String(v ?? '').trim()

interface Props {
  runId?: string | null
  billNumber: unknown
  /** partial render source when the frame row can't be found */
  fallbackRow?: Row
}

type State = 'closed' | 'loading' | 'ready'

export function BillLineage({ runId, billNumber, fallbackRow }: Props) {
  const [state, setState] = useState<State>('closed')
  const [row, setRow] = useState<Row | null>(null)

  const open = async () => {
    setState('loading')
    let found: Row | null = null
    if (runId && norm(billNumber)) {
      try {
        const rows = await enrichedRows(runId)
        found = rows.find((r) => norm(r.bill_number) === norm(billNumber)) ?? null
      } catch {
        found = null
      }
    }
    setRow(found)
    setState('ready')
  }

  if (state === 'closed') {
    return (
      <div className="lineage-toggle">
        <button className="btn-open" onClick={open}>Bill lineage ▾</button>
      </div>
    )
  }
  if (state === 'loading') {
    return (
      <div className="lineage-toggle">
        <p className="frame-note"><span className="quill" /> loading lineage…</p>
      </div>
    )
  }
  const source = row ?? fallbackRow ?? null
  return (
    <div className="lineage-open">
      <div className="detail-section">
        Bill lineage
        {!row && fallbackRow && (
          <span className="chip-note"> partial — from the match evidence</span>
        )}
        <button className="btn-open lineage-close" onClick={() => setState('closed')}>
          hide ▴
        </button>
      </div>
      {source ? (
        <div className="timeline-wrap">
          <LineageTimeline row={source} />
        </div>
      ) : (
        <p className="frame-note">no lineage recorded for this bill</p>
      )}
    </div>
  )
}
