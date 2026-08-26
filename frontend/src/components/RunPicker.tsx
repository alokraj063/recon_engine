import { useEffect, useRef, useState } from 'react'
import { Layers } from 'lucide-react'
import type { RunListItem } from '../types'
import { fmtWhen } from '../format'

interface Props {
  runs: RunListItem[]            // succeeded runs, newest first
  selection: string[]            // selected run ids, newest first
  onChange: (ids: string[]) => void
}

type ModeFilter = 'all' | 'snapshot' | 'incremental'

const MODE_FILTERS: Array<[ModeFilter, string]> = [
  ['all', 'All'],
  ['snapshot', 'Snapshot'],
  ['incremental', 'Incremental'],
]

/** Short label used in the picker AND as the Run column value. */
export function runLabel(r: RunListItem): string {
  return `${fmtWhen(r.created_at)} · ${r.mode}`
}

/**
 * Multi-select run filter for the reconciliation-result views: a mode
 * filter (all / snapshot / incremental) over a checkbox list, with an
 * "all shown" master that selects exactly the filtered set. At least
 * one run always stays selected.
 */
export function RunPicker({ runs, selection, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [modeFilter, setModeFilter] = useState<ModeFilter>('all')
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const selected = new Set(selection)
  const visible = runs.filter((r) => modeFilter === 'all' || r.mode === modeFilter)
  const visibleIds = visible.map((r) => r.run_id)
  const outsideCount = selection.filter((id) => !visibleIds.includes(id)).length
  // "all shown": every visible run selected and nothing beyond them
  const allShown = visible.length > 0
    && visible.every((r) => selected.has(r.run_id))
    && outsideCount === 0

  const toggle = (id: string) => {
    if (selected.has(id)) {
      if (selection.length === 1) return          // keep at least one
      onChange(selection.filter((x) => x !== id))
    } else {
      // keep newest-first order by rebuilding from the runs list
      const next = new Set([...selection, id])
      onChange(runs.filter((r) => next.has(r.run_id)).map((r) => r.run_id))
    }
  }

  const toggleAllShown = () => {
    if (allShown) {
      // collapse to the primary run (first selected still visible, else
      // the newest visible run)
      const keep = selection.find((id) => visibleIds.includes(id)) ?? visibleIds[0]
      if (keep) onChange([keep])
    } else {
      // the filter semantic: one click = exactly the filtered set
      onChange(visibleIds)
    }
  }

  const summaryForMode = (mode: ModeFilter): string | null => {
    const ofMode = runs.filter((r) => r.mode === mode).map((r) => r.run_id)
    const exact = ofMode.length > 0 && ofMode.length === selection.length
      && ofMode.every((id) => selected.has(id))
    return exact ? `All ${mode} (${ofMode.length})` : null
  }

  const summaryText =
    runs.length > 1 && selection.length === runs.length ? `All runs (${runs.length})`
    : summaryForMode('incremental') ?? summaryForMode('snapshot')
    ?? (selection.length === 1 ? 'This run' : `${selection.length} runs`)

  return (
    <div className="run-picker" ref={wrap}>
      <button className="run-picker-btn btn-ic" onClick={() => setOpen((o) => !o)}>
        <Layers size={13} strokeWidth={1.75} /> {summaryText}
        <span className="run-picker-caret">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="run-picker-panel">
          <div className="seg run-picker-seg">
            {MODE_FILTERS.map(([m, label]) => (
              <button key={m} className={modeFilter === m ? 'on' : ''}
                      onClick={() => setModeFilter(m)}>
                {label}
              </button>
            ))}
          </div>
          <label className="run-picker-row run-picker-all">
            <input
              type="checkbox"
              checked={allShown}
              disabled={visible.length === 0}
              onChange={toggleAllShown}
            />
            <span className="run-picker-when">
              {modeFilter === 'all' ? 'All runs' : `All ${modeFilter}`}
            </span>
            <span className="chip-note">{visible.length} shown</span>
          </label>
          <div className="run-picker-list">
            {visible.length === 0 && (
              <p className="run-picker-empty">no {modeFilter} runs yet</p>
            )}
            {visible.map((r) => (
              <label key={r.run_id} className="run-picker-row">
                <input
                  type="checkbox"
                  checked={selected.has(r.run_id)}
                  disabled={selected.has(r.run_id) && selection.length === 1}
                  onChange={() => toggle(r.run_id)}
                />
                <span className="run-picker-when">{fmtWhen(r.created_at)}</span>
                <span className="stamp">{r.mode}</span>
                <span className="chip-note">
                  {r.counts
                    ? `${r.counts.matched} / ${r.counts.bank_only} / ${r.counts.bill_only}`
                    : '—'}
                </span>
              </label>
            ))}
          </div>
          <p className="run-picker-foot">
            {outsideCount > 0 && (
              <span className="run-picker-outside">
                {outsideCount} selected outside this filter ·{' '}
              </span>
            )}
            matched / bank only / bill only
          </p>
        </div>
      )}
    </div>
  )
}
