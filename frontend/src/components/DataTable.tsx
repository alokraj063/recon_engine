import { useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import type { Row } from '../types'
import { DATE_HINT } from '../format'
import { ColumnFilter } from './filters/ColumnFilter'
import { FilterChips, type FilterChip } from './filters/FilterChips'
import { buildOptions, facetKey } from './filters/facets'

interface Props {
  rows: Row[]
  columns: ColumnDef<Row>[]
  numericIds?: Set<string>
  initialHidden?: string[]
  /** extra controls rendered to the left of the search box */
  toolbar?: React.ReactNode
  /** parent-owned filters shown in the same chip row as the column
   *  filters (e.g. GoldTable's ingestion pick) */
  externalChips?: FilterChip[]
  /** renders an extra <tr> under a row when it is expanded */
  renderDetail?: (row: Row) => React.ReactNode
  /** denominator for the row counter when the caller pre-filters `rows`
   *  (defaults to rows.length) */
  totalRows?: number
  /** shown INSTEAD of the table when `rows` is empty — the caller knows
   *  why (an empty run, a segment with nothing in it); a typed filter
   *  that matches nothing gets a built-in note, not this one */
  emptyNote?: React.ReactNode
}

/**
 * Column filters: any column whose def carries `meta.facet` gets a
 * checklist in its header (ColumnFilter) and its applied values as chips
 * above the table (FilterChips). Rows are filtered here, BEFORE TanStack,
 * so the global search and the row counter see the narrowed set.
 */
export function DataTable({ rows, columns, numericIds, initialHidden, toolbar, externalChips,
                            renderDetail, totalRows, emptyNote }: Props) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    Object.fromEntries((initialHidden ?? []).map((c) => [c, false])),
  )
  const [openRow, setOpenRow] = useState<string | null>(null)
  const [colFilters, setColFilters] = useState<Record<string, string[]>>({})

  const facetCols = useMemo(() => columns
    .filter((c) => c.meta?.facet && typeof c.id === 'string')
    .map((c) => ({
      id: c.id as string,
      label: c.meta?.facetLabel ?? (typeof c.header === 'string' ? c.header : (c.id as string)),
      format: c.meta?.facetFormat,
      get: (r: Row) => ('accessorFn' in c && c.accessorFn ? c.accessorFn(r, 0) : r[c.id as string]),
    })), [columns])

  const options = useMemo(() =>
    Object.fromEntries(facetCols.map((c) => [c.id, buildOptions(rows, c.get)])),
    [rows, facetCols])

  const activeFilters = useMemo(
    () => facetCols.filter((c) => (colFilters[c.id] ?? []).length > 0), [facetCols, colFilters])

  const filteredRows = useMemo(() => {
    if (activeFilters.length === 0) return rows
    return rows.filter((r) => activeFilters.every((c) => colFilters[c.id].includes(facetKey(c.get(r)))))
  }, [rows, activeFilters, colFilters])

  const setFilter = (id: string, values: string[]) =>
    setColFilters((prev) => ({ ...prev, [id]: values }))

  const chips: FilterChip[] = [
    ...(externalChips ?? []),
    ...activeFilters.map((c) => ({
      key: c.id,
      label: c.label,
      values: colFilters[c.id],
      format: c.format,
      onRemove: (v?: string) =>
        setFilter(c.id, v === undefined ? [] : colFilters[c.id].filter((x) => x !== v)),
    })),
  ]

  const table = useReactTable({
    data: filteredRows,
    columns,
    state: { sorting, globalFilter, columnVisibility },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: (row, _colId, filterValue) => {
      const needle = String(filterValue).toLowerCase()
      return Object.values(row.original).some((v) =>
        v != null && String(typeof v === 'object' ? JSON.stringify(v) : v).toLowerCase().includes(needle),
      )
    },
  })

  const visible = table.getRowModel().rows
  const filtersHide = rows.length > 0 && filteredRows.length === 0

  return (
    <div>
      <div className="table-tools">
        {toolbar}
        <FilterChips chips={chips} />
        <input
          type="search"
          placeholder="filter rows…"
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
        />
        <details className="advanced" style={{ margin: 0, borderTop: 'none', paddingTop: 0 }}>
          <summary>columns</summary>
          <div style={{ position: 'absolute', zIndex: 5, background: 'var(--paper-card)', border: '1px solid var(--rule-strong)', padding: '10px 14px', maxHeight: 300, overflowY: 'auto' }}>
            {table.getAllLeafColumns().map((col) => (
              <label key={col.id} style={{ display: 'block', fontSize: 12, whiteSpace: 'nowrap' }}>
                <input
                  type="checkbox"
                  checked={col.getIsVisible()}
                  onChange={col.getToggleVisibilityHandler()}
                />{' '}
                {col.id}
              </label>
            ))}
          </div>
        </details>
        <span className="row-count">
          {visible.length} of {totalRows ?? rows.length} rows
        </span>
      </div>

      {visible.length === 0 ? (
        // no bordered box around nothing: the note stands alone under
        // the toolbar (kept, so a filter can still be cleared)
        <div className="table-empty">
          {filtersHide ? (
            <p className="frame-note">
              no rows match the active filters —{' '}
              <button className="link-btn" onClick={() => setColFilters({})}>clear all</button>
            </p>
          ) : rows.length > 0 && globalFilter
            ? <p className="frame-note">no rows match “{globalFilter}”</p>
            : (emptyNote ?? <p className="frame-note">no rows</p>)}
        </div>
      ) : (
      <div className="table-scroll">
        <table className="data">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {renderDetail && <th style={{ width: 24 }} />}
                {hg.headers.map((h) => {
                  const facet = facetCols.find((c) => c.id === h.column.id)
                  return (
                    <th key={h.id} onClick={h.column.getToggleSortingHandler()}>
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {facet && (
                        <ColumnFilter label={facet.label} options={options[facet.id] ?? []}
                                      value={colFilters[facet.id] ?? []}
                                      format={facet.format}
                                      onApply={(vs) => setFilter(facet.id, vs)} />
                      )}
                      <span className="sort-mark">
                        {{ asc: '▲', desc: '▼' }[h.column.getIsSorted() as string] ?? ''}
                      </span>
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {visible.map((row) => {
              const open = openRow === row.id
              return (
                <FragmentRow
                  key={row.id}
                  open={open}
                  clickable={Boolean(renderDetail)}
                  onToggle={() => setOpenRow(open ? null : row.id)}
                  detail={open && renderDetail ? renderDetail(row.original) : null}
                  colSpan={row.getVisibleCells().length + 1}
                >
                  {renderDetail && (
                    <td className="mono">
                      <ChevronRight className="chev chev-ic" size={14} strokeWidth={2} aria-hidden />
                    </td>
                  )}
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}
                        className={numericIds?.has(cell.column.id) ? 'num'
                          : DATE_HINT.test(cell.column.id) ? 'date' : ''}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </FragmentRow>
              )
            })}
          </tbody>
        </table>
      </div>
      )}
    </div>
  )
}

function FragmentRow({
  open,
  clickable,
  onToggle,
  detail,
  colSpan,
  children,
}: {
  open: boolean
  clickable: boolean
  onToggle: () => void
  detail: React.ReactNode
  colSpan: number
  children: React.ReactNode
}) {
  return (
    <>
      <tr className={clickable ? `xq-row${open ? ' open' : ''}` : ''} onClick={clickable ? onToggle : undefined}>
        {children}
      </tr>
      {detail && (
        <tr className="xq-detail">
          <td colSpan={colSpan}>{detail}</td>
        </tr>
      )}
    </>
  )
}
