import { useState } from 'react'
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

interface Props {
  rows: Row[]
  columns: ColumnDef<Row>[]
  numericIds?: Set<string>
  initialHidden?: string[]
  /** extra controls rendered to the left of the search box */
  toolbar?: React.ReactNode
  /** renders an extra <tr> under a row when it is expanded */
  renderDetail?: (row: Row) => React.ReactNode
}

export function DataTable({ rows, columns, numericIds, initialHidden, toolbar, renderDetail }: Props) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    Object.fromEntries((initialHidden ?? []).map((c) => [c, false])),
  )
  const [openRow, setOpenRow] = useState<string | null>(null)

  const table = useReactTable({
    data: rows,
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

  return (
    <div>
      <div className="table-tools">
        {toolbar}
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
          {visible.length} of {rows.length} rows
        </span>
      </div>

      <div className="table-scroll">
        <table className="data">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {renderDetail && <th style={{ width: 24 }} />}
                {hg.headers.map((h) => (
                  <th key={h.id} onClick={h.column.getToggleSortingHandler()}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    <span className="sort-mark">
                      {{ asc: '▲', desc: '▼' }[h.column.getIsSorted() as string] ?? ''}
                    </span>
                  </th>
                ))}
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
                    <td key={cell.id} className={numericIds?.has(cell.column.id) ? 'num' : ''}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </FragmentRow>
              )
            })}
          </tbody>
        </table>
      </div>
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
