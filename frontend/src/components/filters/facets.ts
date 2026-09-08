import type { RowData } from '@tanstack/react-table'
import type { Row } from '../../types'

/**
 * Column-filter vocabulary shared by every table.
 *
 * A "facet" column offers a checklist of its distinct values in the
 * header (ColumnFilter) and shows the chosen values as chips above the
 * table (FilterChips). Declared per column via TanStack column `meta`.
 */
declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    /** offer a value checklist in this column's header */
    facet?: boolean
    /** chip / popover label (defaults to the header text) */
    facetLabel?: string
    /** display transform for option and chip text (values stay raw) */
    facetFormat?: (value: string) => string
  }
}

/** one bucket for every spelling of "nothing here" so a checklist shows
 *  a single "(blank)" entry, matching fmtCell's em-dash */
export const BLANK = '(blank)'

export const facetKey = (v: unknown): string =>
  v == null || v === '' ? BLANK : String(v)

export interface FacetOption {
  value: string
  count: number
}

/** distinct values of `get(row)` over the UNFILTERED rows with counts,
 *  sorted A→Z with (blank) last — every option stays visible (with its
 *  count) while another filter is active */
export function buildOptions<T = Row>(rows: T[], get: (r: T) => unknown): FacetOption[] {
  const counts = new Map<string, number>()
  for (const r of rows) {
    const v = facetKey(get(r))
    counts.set(v, (counts.get(v) ?? 0) + 1)
  }
  return [...counts.entries()]
    .sort(([a], [b]) => (a === BLANK ? 1 : b === BLANK ? -1 : a.localeCompare(b)))
    .map(([value, count]) => ({ value, count }))
}

/** SCREAMING_SNAKE code -> "screaming snake" for chips and options */
export const deSnake = (v: string) => v.replace(/_/g, ' ')
