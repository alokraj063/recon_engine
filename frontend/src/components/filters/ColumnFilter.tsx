import { useRef, useState } from 'react'
import { ListFilter } from 'lucide-react'
import { FilterPopover } from './FilterPopover'
import type { FacetOption } from './facets'

/**
 * The header filter control: a list-filter icon (faint until a filter is
 * active, then accent) opening a checklist of the column's values with
 * counts. Selection is PENDING until Apply; Escape / outside click
 * discards it. An empty selection means "no filter" (every row).
 */
export function ColumnFilter({ label, options, value, onApply, format }: {
  label: string
  options: FacetOption[]
  /** the applied selection (empty = all) */
  value: string[]
  onApply: (values: string[]) => void
  format?: (v: string) => string
}) {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<string[]>(value)
  const [q, setQ] = useState('')
  const btn = useRef<HTMLButtonElement>(null)
  const active = value.length > 0
  const fmt = format ?? ((v: string) => v)

  const toggleOpen = () => {
    if (!open) { setPending(value); setQ('') }
    setOpen((o) => !o)
  }
  const shown = q
    ? options.filter((o) => fmt(o.value).toLowerCase().includes(q.toLowerCase()))
    : options
  const allOn = pending.length === 0
  const toggle = (v: string) =>
    setPending((p) => (p.includes(v) ? p.filter((x) => x !== v) : [...p, v]))

  return (
    <span className="col-filter" onClick={(e) => e.stopPropagation()}>
      <button ref={btn} type="button"
              className={`col-filter-btn${active ? ' on' : ''}`}
              onClick={toggleOpen}
              title={active ? `${label}: ${value.map(fmt).join(', ')}` : `filter ${label}`}
              aria-label={`filter ${label}`}>
        <ListFilter size={12} strokeWidth={2} />
        {active && <span className="col-filter-dot" aria-hidden />}
      </button>
      <FilterPopover anchorRef={btn} open={open} onClose={() => setOpen(false)}>
        <div className="filter-pop-head">{label}</div>
        {options.length > 12 && (
          <input className="filter-pop-search" placeholder="find a value…" value={q}
                 autoFocus onChange={(e) => setQ(e.target.value)} />
        )}
        <div className="filter-pop-list">
          <label className="filter-opt filter-opt-all">
            <input type="checkbox" checked={allOn} onChange={() => setPending([])} />
            <span>All</span>
            <span className="filter-opt-n">{options.reduce((s, o) => s + o.count, 0).toLocaleString('en-IN')}</span>
          </label>
          {shown.map((o) => (
            <label key={o.value} className="filter-opt">
              <input type="checkbox" checked={pending.includes(o.value)}
                     onChange={() => toggle(o.value)} />
              <span className="filter-opt-v" title={fmt(o.value)}>{fmt(o.value)}</span>
              <span className="filter-opt-n">{o.count.toLocaleString('en-IN')}</span>
            </label>
          ))}
          {shown.length === 0 && <span className="chip-note">no values match</span>}
        </div>
        <div className="filter-pop-foot">
          <button className="link-btn" onClick={() => { onApply([]); setOpen(false) }}>Clear</button>
          <button className="filter-apply" onClick={() => { onApply(pending); setOpen(false) }}>
            Apply
          </button>
        </div>
      </FilterPopover>
    </span>
  )
}
