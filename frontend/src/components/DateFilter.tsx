import { useEffect, useRef, useState } from 'react'
import { CalendarDays } from 'lucide-react'

/**
 * Date window + operating-unit filter for the aggregate views (Command
 * Center, AR Reconciliation). Quick picks Today / Yesterday / This month
 * / All time plus a custom from–to pair; units are chips (all on = no
 * unit filter sent). The value is plain data so the host can persist it
 * per customer in localStorage.
 */
export type QuickPick = 'today' | 'yesterday' | 'month' | 'all' | 'custom'

export interface DateFilterValue {
  pick: QuickPick
  from: string   // yyyy-mm-dd, '' = unbounded
  to: string
  /** operating units in scope; null = every unit (nothing sent) */
  units: string[] | null
}

export const UNASSIGNED_UNIT = 'UNASSIGNED'

export const DEFAULT_DATE_FILTER: DateFilterValue = { pick: 'month', from: '', to: '', units: null }

const pad = (n: number) => String(n).padStart(2, '0')
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

/** The concrete [from, to] a value resolves to today. */
export function resolveWindow(v: DateFilterValue, now = new Date()): { from: string; to: string } {
  const today = ymd(now)
  switch (v.pick) {
    case 'today': return { from: today, to: today }
    case 'yesterday': {
      const y = new Date(now); y.setDate(y.getDate() - 1)
      return { from: ymd(y), to: ymd(y) }
    }
    case 'month': return { from: ymd(new Date(now.getFullYear(), now.getMonth(), 1)), to: today }
    case 'all': return { from: '', to: '' }
    default: return { from: v.from, to: v.to }
  }
}

const DAY = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' })
const fmtDay = (s: string) => DAY.format(new Date(`${s}T00:00:00`))

/** Short human window, e.g. "this month", "7 Sep", "1 Mar – 31 Mar". */
export function windowLabel(v: DateFilterValue): string {
  if (v.pick === 'today') return 'today'
  if (v.pick === 'yesterday') return 'yesterday'
  if (v.pick === 'month') return 'this month'
  if (v.pick === 'all') return 'all time'
  if (v.from && v.to) return v.from === v.to ? fmtDay(v.from) : `${fmtDay(v.from)} – ${fmtDay(v.to)}`
  if (v.from) return `from ${fmtDay(v.from)}`
  if (v.to) return `to ${fmtDay(v.to)}`
  return 'all time'
}

/** "Friction, Hosur" / "all units" / "Friction + unassigned". */
export function unitsLabel(v: DateFilterValue, known: string[]): string {
  if (!v.units) return 'all units'
  const named = v.units.filter((u) => u !== UNASSIGNED_UNIT)
  const parts = named.length === known.length && known.length > 0 ? ['all units'] : named
  if (v.units.includes(UNASSIGNED_UNIT)) parts.push(named.length ? 'unassigned' : 'unassigned only')
  return parts.length ? parts.join(', ') : 'no units'
}

const PICKS: Array<[QuickPick, string]> = [
  ['today', 'Today'], ['yesterday', 'Yesterday'], ['month', 'This month'], ['all', 'All time'],
]

export function DateFilter({ value, onChange, units, unitCounts }: {
  value: DateFilterValue
  onChange: (v: DateFilterValue) => void
  /** the customer's operating units (from /operating-units) */
  units: string[]
  unitCounts?: Record<string, number>
}) {
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

  const all = [...units, UNASSIGNED_UNIT]
  const on = new Set(value.units ?? all)
  const toggleUnit = (u: string) => {
    const next = new Set(on)
    if (next.has(u)) next.delete(u)
    else next.add(u)
    const arr = all.filter((x) => next.has(x))
    // every chip on = no filter at all
    onChange({ ...value, units: arr.length === all.length ? null : arr })
  }
  const active = value.pick !== 'all' || value.units !== null

  return (
    <div className="run-picker date-filter" ref={wrap}>
      <button className={`run-picker-btn btn-ic${active ? ' on' : ''}`}
              onClick={() => setOpen((o) => !o)}
              title="Limit the figures to a date window and/or operating units">
        <CalendarDays size={13} strokeWidth={1.75} /> {windowLabel(value)}
        {units.length > 0 && ` · ${unitsLabel(value, units)}`}
        <span className="run-picker-caret">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="run-picker-panel date-filter-panel">
          <div className="seg run-picker-seg">
            {PICKS.map(([p, label]) => (
              <button key={p} className={value.pick === p ? 'on' : ''}
                      onClick={() => onChange({ ...value, pick: p })}>
                {label}
              </button>
            ))}
          </div>
          <div className="run-picker-dates">
            <label>
              <span className="chip-note">from</span>
              <input type="date" value={value.pick === 'custom' ? value.from : resolveWindow(value).from}
                     aria-label="from date" max={value.to || undefined}
                     onChange={(e) => onChange({ ...value, pick: 'custom',
                       from: e.target.value, to: value.pick === 'custom' ? value.to : resolveWindow(value).to })} />
            </label>
            <label>
              <span className="chip-note">to</span>
              <input type="date" value={value.pick === 'custom' ? value.to : resolveWindow(value).to}
                     aria-label="to date" min={value.from || undefined}
                     onChange={(e) => onChange({ ...value, pick: 'custom',
                       to: e.target.value, from: value.pick === 'custom' ? value.from : resolveWindow(value).from })} />
            </label>
          </div>
          {units.length > 0 && (
            <div className="date-filter-units">
              <span className="chip-note">operating units</span>
              <div className="stat-chips">
                {all.map((u) => (
                  <button key={u} type="button"
                          className={`chip unit-chip${on.has(u) ? ' on' : ''}`}
                          title={u === UNASSIGNED_UNIT
                            ? 'rows with no operating unit — every unmatched bank credit, and bills whose PartyCode named no unit'
                            : unitCounts?.[u] != null ? `${unitCounts[u]} bills` : undefined}
                          onClick={() => toggleUnit(u)}>
                    {u === UNASSIGNED_UNIT ? 'Unassigned' : u}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
