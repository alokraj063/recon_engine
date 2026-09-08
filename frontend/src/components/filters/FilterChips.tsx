import { X } from 'lucide-react'

export interface FilterChip {
  key: string
  /** column / filter name shown before the colon */
  label: string
  /** applied values (raw); empty chips are not rendered */
  values: string[]
  format?: (v: string) => string
  /** remove one value, or the whole filter when called with no value */
  onRemove: (value?: string) => void
}

/**
 * The active filters of a table, top-left, as accent chips:
 *   Confidence: HIGH ×      Status: OPEN × LOCKED × ×
 * One vocabulary for every page — a table's own column filters and any
 * page-level filter it wants to surface render the same way.
 */
export function FilterChips({ chips, onClearAll }: {
  chips: FilterChip[]
  onClearAll?: () => void
}) {
  const live = chips.filter((c) => c.values.length > 0)
  if (live.length === 0) return null
  const clearAll = onClearAll ?? (() => live.forEach((c) => c.onRemove()))
  return (
    <div className="filter-chips" role="list" aria-label="active filters">
      {live.map((c) => {
        const fmt = c.format ?? ((v: string) => v)
        return (
          <span key={c.key} className="filter-chip" role="listitem">
            <span className="filter-chip-k">{c.label}:</span>
            {c.values.map((v) => (
              <span key={v} className="filter-chip-v">
                {fmt(v)}
                {c.values.length > 1 && (
                  <button className="filter-chip-x" title={`remove ${fmt(v)}`}
                          onClick={() => c.onRemove(v)}>
                    <X size={10} strokeWidth={2.5} />
                  </button>
                )}
              </span>
            ))}
            <button className="filter-chip-x filter-chip-x-all" title={`clear ${c.label} filter`}
                    onClick={() => c.onRemove()}>
              <X size={11} strokeWidth={2.5} />
            </button>
          </span>
        )
      })}
      {live.length > 1 && (
        <button className="link-btn" onClick={clearAll}>clear all</button>
      )}
    </div>
  )
}
