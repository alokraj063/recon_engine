import { useState, type ReactNode } from 'react'
import type { Cell } from '../types'
import { fmtCell } from '../format'
import { DecisionDialog } from './ui'
import { COLUMN_HELP } from '../columnHelp'

/**
 * Candidate bills side by side — one column per bill, one row per field —
 * so an ambiguous match is decided by reading ACROSS a row instead of
 * scrolling between stacked cards. The bill number is each column's title
 * (never repeated as a row). Fields where the bills disagree are the ones
 * that can tell them apart: they come first, are highlighted, and are
 * named in the "Differs on" line; identical fields fold away. A cell that
 * equals the credit's own value is marked ✓, and on a "best" field (the
 * date gap) the closest bill is marked.
 */

export interface CompareField {
  key: string
  label: string
  /** the credit value this field is compared with, if any */
  refKey?: string
  /** a numeric field where the smallest value is the better evidence */
  best?: 'min'
}

export interface CompareColumn {
  key: string
  /** the column title — the bill number */
  title: string
  values: Record<string, Cell | undefined>
  /** the matcher's pick; hosts pass false when the choice was arbitrary */
  picked?: boolean
  pickNote?: string
  /** present only where the analyst can decide: accept THIS bill */
  onAccept?: (note?: string) => void
  /** extra per-bill content under the fields (e.g. the lineage opener) */
  footer?: ReactNode
}

const norm = (v: Cell | undefined): string => {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') return String(Math.round(v * 100) / 100)
  return String(v).trim().toUpperCase()
}

function isEmpty(v: Cell | undefined) {
  const s = norm(v)
  return s === '' || s === 'NAN' || s === '----' || s === '—'
}

export function CandidateCompare({ fields, columns, credit, busy, titleLabel = 'Bill number',
                                   acceptLabel = 'Accept this bill' }: {
  fields: CompareField[]
  columns: CompareColumn[]
  /** the credit's values, for the ✓ "same as the credit" marks */
  credit?: Record<string, Cell | undefined>
  busy?: boolean
  /** what the column titles are, for the corner and the "Differs on" line */
  titleLabel?: string
  acceptLabel?: string
}) {
  // one bill has nothing to compare against: show every field, no folding
  const compare = columns.length > 1
  const [showSame, setShowSame] = useState(!compare)
  const [noteFor, setNoteFor] = useState<CompareColumn | null>(null)

  // a field with no value on any bill says nothing — drop it entirely
  const present = fields.filter((f) => columns.some((c) => !isEmpty(c.values[f.key])))
  const differs = (f: CompareField) =>
    compare && new Set(columns.map((c) => norm(c.values[f.key]))).size > 1
  const diff = present.filter(differs)
  const same = present.filter((f) => !differs(f))
  const titlesDiffer = compare && new Set(columns.map((c) => norm(c.title))).size > 1
  const diffNames = [...(titlesDiffer ? [titleLabel] : []), ...diff.map((f) => f.label)]

  const bestKey = (f: CompareField): string | null => {
    if (f.best !== 'min' || !differs(f)) return null
    const nums = columns
      .map((c) => ({ k: c.key, v: Number(c.values[f.key]) }))
      .filter((x) => !Number.isNaN(x.v))
    if (!nums.length) return null
    const min = Math.min(...nums.map((x) => Math.abs(x.v)))
    const hits = nums.filter((x) => Math.abs(x.v) === min)
    return hits.length === 1 ? hits[0].k : null
  }

  const row = (f: CompareField, isDiff: boolean) => {
    const best = bestKey(f)
    const refVal = f.refKey && credit ? credit[f.refKey] : undefined
    return (
      <tr key={f.key} className={isDiff ? 'cmp-diff' : undefined}>
        <th scope="row" title={COLUMN_HELP[f.key]}>
          <span className={COLUMN_HELP[f.key] ? 'has-help' : undefined}>{f.label}</span>
        </th>
        {columns.map((c) => {
          const v = c.values[f.key]
          const agrees = !!f.refKey && !isEmpty(refVal) && !isEmpty(v) && norm(v) === norm(refVal)
          const isBest = best === c.key
          const text = fmtCell(f.key, (v ?? null) as Cell)
          return (
            <td key={c.key}
                className={[isDiff && !isEmpty(v) ? 'cmp-hot' : '', agrees ? 'cmp-agree' : '',
                            isBest ? 'cmp-best' : ''].filter(Boolean).join(' ') || undefined}
                title={agrees ? 'Same as the credit' : isBest ? 'Closest to the credit' : undefined}>
              {text === '—' ? <span className="empty-cell">—</span> : text}
              {agrees && <span className="cmp-mark"> ✓</span>}
              {isBest && <span className="cmp-mark"> closest</span>}
            </td>
          )
        })}
      </tr>
    )
  }

  const anyPick = columns.some((c) => c.picked)
  return (
    <div className="cmp">
      {compare && (
        <div className="cmp-summary">
          {diffNames.length > 0 ? (
            <>
              <span className="cmp-summary-label">Differs on</span>
              {diffNames.map((l) => <span key={l} className="cmp-chip">{l}</span>)}
            </>
          ) : (
            <span className="cmp-summary-label">No field tells these bills apart</span>
          )}
        </div>
      )}
      <div className="cmp-scroll">
        <table className="cmp-table">
          <colgroup>
            <col className="cmp-col-label" />
            {columns.map((c) => <col key={c.key} />)}
          </colgroup>
          <thead>
            <tr>
              <th className="cmp-corner">
                <span className="has-help" title={COLUMN_HELP.bill_number}>{titleLabel}</span>
              </th>
              {columns.map((c) => (
                <th key={c.key} className={c.picked ? 'cmp-picked' : undefined}>
                  <div className={`cmp-title${titlesDiffer ? ' is-hot' : ''}`}>{c.title}</div>
                  {/* the badge slot keeps every title on one line, picked or not */}
                  {anyPick && (
                    <div className="cmp-badge">
                      {c.picked && (
                        <span className="chip chip-picked">
                          Matcher's pick{c.pickNote ? ` · ${c.pickNote}` : ''}
                        </span>
                      )}
                    </div>
                  )}
                  {c.onAccept && (
                    <div className="cmp-actions">
                      <button className="btn-accept" disabled={busy} onClick={() => c.onAccept?.()}>
                        {acceptLabel}
                      </button>
                      <button type="button" className="decide-note" disabled={busy}
                              title="Accept with a note" onClick={() => setNoteFor(c)}>
                        + note
                      </button>
                    </div>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {diff.map((f) => row(f, true))}
            {compare && same.length > 0 && (
              <tr className="cmp-fold">
                <td colSpan={columns.length + 1}>
                  <button type="button" className="decide-note" onClick={() => setShowSame((v) => !v)}>
                    {showSame ? 'Hide' : 'Show'} {same.length} identical field{same.length === 1 ? '' : 's'}
                  </button>
                </td>
              </tr>
            )}
            {showSame && same.map((f) => row(f, false))}
            {columns.some((c) => c.footer) && (
              <tr className="cmp-footer">
                <th scope="row" />
                {columns.map((c) => <td key={c.key}>{c.footer}</td>)}
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {noteFor && (
        <DecisionDialog
          title={`Accept bill ${noteFor.title}`}
          message="This bill settles the credit and the match locks."
          busy={busy}
          onClose={() => setNoteFor(null)}
          actions={[{ label: acceptLabel, tone: 'primary',
                      run: (note) => { noteFor.onAccept?.(note); setNoteFor(null) } }]}
        />
      )}
    </div>
  )
}
