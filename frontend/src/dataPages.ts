import type { View } from './components/Sidebar'

/**
 * The sidebar's single "Data" group: one page per data kind, each with
 * two SCOPES that are simply the two pre-existing views for that kind —
 *   Current   the live gold layer (GoldTable, the old gold_* views)
 *   As of run the frozen frame one reconciliation used (SourceTable,
 *             the old Run data views, named by the run frame)
 * Pairing the existing view keys (rather than inventing new ones) keeps
 * the URL hash format, the run auto-load gate and every legacy link
 * untouched: `#view=gold_bills` IS "Bills, Current".
 *
 * run: null = Current only — a run persists no lineage snapshot (only
 * bank / bills / bills_enriched / recoveries, see routes._frame_records).
 * runTrail is the "With lineage trail" toggle inside Bills in run scope:
 * bills_enriched is a run artifact with no gold counterpart.
 */
export type DataScope = 'current' | 'run'

export interface DataPage {
  id: 'bank' | 'bills' | 'recoveries' | 'lineage'
  label: string
  current: View
  run: View | null
  runTrail?: View
}

export const DATA_PAGES: readonly DataPage[] = [
  { id: 'bank', label: 'Bank Transactions', current: 'gold_bank', run: 'bank' },
  { id: 'bills', label: 'Bills', current: 'gold_bills', run: 'bills', runTrail: 'bills_enriched' },
  { id: 'recoveries', label: 'Recoveries', current: 'gold_recoveries', run: 'recoveries' },
  { id: 'lineage', label: 'Lineage docs', current: 'gold_lineage', run: null },
]

/** The data page a view belongs to, in either scope (undefined = not a data view). */
export function dataPageOf(view: View): DataPage | undefined {
  return DATA_PAGES.find(
    (p) => p.current === view || p.run === view || p.runTrail === view)
}

export function scopeOf(view: View): DataScope {
  const page = dataPageOf(view)
  return page && page.current === view ? 'current' : 'run'
}

/** The view a sidebar click on `page` opens under the user's scope preference. */
export function viewForScope(page: DataPage, scope: DataScope): View {
  return scope === 'run' && page.run ? page.run : page.current
}

export const DATA_SCOPE_KEY = 'recon.dataScope'

export function readScopePref(): DataScope {
  try {
    return localStorage.getItem(DATA_SCOPE_KEY) === 'run' ? 'run' : 'current'
  } catch {
    return 'current'
  }
}

export function writeScopePref(scope: DataScope): void {
  try { localStorage.setItem(DATA_SCOPE_KEY, scope) } catch { /* ignore */ }
}
