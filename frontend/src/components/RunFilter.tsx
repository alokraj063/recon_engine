import { useEffect, useRef, useState } from 'react'
import { History } from 'lucide-react'
import type { RunListItem } from '../types'
import { fmtWhen, inDayRange, localDay } from '../format'

/**
 * Run filter for the live workspace views (Analyst queue, AR
 * Reconciliation): unlike RunPicker — which LOADS runs into the result
 * views and always keeps one selected — this only narrows rows that
 * already carry a run id, and its empty value means "every run".
 *
 * Three ways to narrow, composable:
 *   mode      all / snapshot / incremental
 *   from/to   the run's date (the day shown in every Runs list)
 *   runIds    explicitly ticked runs — when any are ticked they win
 *             outright; otherwise mode + dates define the set
 */
export type ModeFilter = 'all' | 'snapshot' | 'incremental'

export interface RunFilterValue {
  runIds: string[]
  mode: ModeFilter
  from: string
  to: string
}

export const EMPTY_RUN_FILTER: RunFilterValue = { runIds: [], mode: 'all', from: '', to: '' }

const MODE_FILTERS: Array<[ModeFilter, string]> = [
  ['all', 'All'],
  ['snapshot', 'Snapshot'],
  ['incremental', 'Incremental'],
]

export function isRunFilterActive(v: RunFilterValue): boolean {
  return v.runIds.length > 0 || v.mode !== 'all' || !!v.from || !!v.to
}

/** Runs inside the filter's mode + date scope (ignores explicit ticks). */
export function runsInScope(v: RunFilterValue, runs: RunListItem[]): RunListItem[] {
  return runs.filter((r) =>
    (v.mode === 'all' || r.mode === v.mode)
    && inDayRange(localDay(r.created_at), v.from, v.to))
}

/** The run ids rows must belong to, or null when nothing is filtered. */
export function runFilterSet(v: RunFilterValue, runs: RunListItem[]): Set<string> | null {
  if (v.runIds.length) return new Set(v.runIds)
  if (!isRunFilterActive(v)) return null
  return new Set(runsInScope(v, runs).map((r) => r.run_id))
}

/** Short run label: "7 Sep, 10:42 · incremental" — falls back to the
 *  id prefix for a run the list does not know (deleted, other
 *  customer, still loading). */
export function runLabelFor(runs: RunListItem[], runId: string | null | undefined): string {
  if (!runId) return '—'
  const r = runs.find((x) => x.run_id === runId)
  return r ? `${fmtWhen(r.created_at)} · ${r.mode}` : runId.slice(0, 8)
}

const DAY = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' })
const fmtDay = (ymd: string) => DAY.format(new Date(`${ymd}T00:00:00`))

function summaryText(v: RunFilterValue, runs: RunListItem[]): string {
  if (v.runIds.length === 1) return runLabelFor(runs, v.runIds[0])
  if (v.runIds.length > 1) return `${v.runIds.length} runs`
  const parts: string[] = []
  if (v.mode !== 'all') parts.push(v.mode)
  if (v.from && v.to) parts.push(v.from === v.to ? fmtDay(v.from) : `${fmtDay(v.from)} – ${fmtDay(v.to)}`)
  else if (v.from) parts.push(`from ${fmtDay(v.from)}`)
  else if (v.to) parts.push(`to ${fmtDay(v.to)}`)
  return parts.length ? parts.join(' · ') : 'All runs'
}

interface Props {
  runs: RunListItem[]            // the customer's runs, newest first
  value: RunFilterValue
  onChange: (v: RunFilterValue) => void
  /** rows-in-scope note shown in the footer, e.g. "12 of 40 matches" */
  note?: string
}

export function RunFilter({ runs, value, onChange, note }: Props) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const visible = runsInScope(value, runs)
  const visibleIds = visible.map((r) => r.run_id)
  const ticked = new Set(value.runIds)
  const allShown = visible.length > 0 && visible.every((r) => ticked.has(r.run_id))
    && value.runIds.every((id) => visibleIds.includes(id))
  const active = isRunFilterActive(value)

  const toggle = (id: string) => {
    const next = new Set(value.runIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    // keep newest-first order by rebuilding from the runs list
    onChange({ ...value, runIds: runs.filter((r) => next.has(r.run_id)).map((r) => r.run_id) })
  }

  return (
    <div className="run-picker run-filter" ref={wrap}>
      <button className={`run-picker-btn btn-ic${active ? ' on' : ''}`}
              onClick={() => setOpen((o) => !o)}
              title="Limit rows to particular runs, a mode, or a date range">
        <History size={13} strokeWidth={1.75} /> Runs · {summaryText(value, runs)}
        <span className="run-picker-caret">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="run-picker-panel">
          <div className="seg run-picker-seg">
            {MODE_FILTERS.map(([m, label]) => (
              <button key={m} className={value.mode === m ? 'on' : ''}
                      onClick={() => onChange({ ...value, mode: m })}>
                {label}
              </button>
            ))}
          </div>
          <div className="run-picker-dates">
            <label>
              <span className="chip-note">from</span>
              <input type="date" value={value.from} aria-label="runs from date"
                     max={value.to || undefined}
                     onChange={(e) => onChange({ ...value, from: e.target.value })} />
            </label>
            <label>
              <span className="chip-note">to</span>
              <input type="date" value={value.to} aria-label="runs to date"
                     min={value.from || undefined}
                     onChange={(e) => onChange({ ...value, to: e.target.value })} />
            </label>
          </div>
          <label className="run-picker-row run-picker-all">
            <input type="checkbox" checked={allShown} disabled={visible.length === 0}
                   onChange={() => onChange({
                     ...value, runIds: allShown ? [] : visibleIds })} />
            <span className="run-picker-when">
              {value.mode === 'all' ? 'All runs' : `All ${value.mode}`}
              {(value.from || value.to) && ' in range'}
            </span>
            <span className="chip-note">{visible.length} shown</span>
          </label>
          <div className="run-picker-list">
            {visible.length === 0 && (
              <p className="run-picker-empty">no runs match this mode / date range</p>
            )}
            {visible.map((r) => (
              <label key={r.run_id} className="run-picker-row">
                <input type="checkbox" checked={ticked.has(r.run_id)}
                       onChange={() => toggle(r.run_id)} />
                <span className="run-picker-when">{fmtWhen(r.created_at)}</span>
                <span className="stamp">{r.mode}</span>
                {r.status !== 'succeeded' && (
                  <span className={`stamp stamp-${r.status}`}>{r.status}</span>
                )}
                <span className="chip-note">
                  {r.counts
                    ? `${r.counts.matched} / ${r.counts.bank_only} / ${r.counts.bill_only}`
                    : '—'}
                </span>
              </label>
            ))}
          </div>
          <p className="run-picker-foot">
            {note && <span>{note} · </span>}
            {active ? (
              <button className="link-btn" onClick={() => onChange(EMPTY_RUN_FILTER)}>
                clear filter
              </button>
            ) : 'matched / bank only / bill only'}
          </p>
        </div>
      )}
    </div>
  )
}
