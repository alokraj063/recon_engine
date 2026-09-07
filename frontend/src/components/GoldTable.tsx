import { useEffect, useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { GoldFileInfo, GoldFrameName, Row } from '../types'
import { fetchGoldFiles, fetchGoldFrame } from '../api'
import { AMOUNT_COLS, fmtCell } from '../format'
import { SHARED_PRESETS } from '../framePresets'
import { DataTable } from './DataTable'

/** Which source types can own rows of each gold frame — scopes the
 *  ingestion-filter dropdown to relevant files. */
const FRAME_SOURCE_TYPES: Record<GoldFrameName, string[]> = {
  bank: ['bank_statement'],
  bills: ['bill_status'],
  recoveries: ['bill_status'],
  lineage: ['lineage_rnote', 'lineage_crn'],
}

function buildColumns(frame: GoldFrameName, rows: Row[]): { columns: ColumnDef<Row>[]; hidden: string[] } {
  const preset = SHARED_PRESETS[frame]
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  const curated = preset.curated.filter(([k]) => present.has(k))
  const rest = [...present].filter((k) => !preset.curated.some(([c]) => c === k)).sort()

  const make = (key: string, label: string): ColumnDef<Row> => ({
    id: key,
    header: label,
    accessorFn: (row) => row[key],
    cell: (ctx) => {
      const text = fmtCell(key, ctx.row.original[key])
      return text === '—' ? <span className="empty-cell">—</span> : text
    },
  })

  return {
    columns: [...curated.map(([k, l]) => make(k, l)), ...rest.map((k) => make(k, k))],
    // uncurated extras append hidden (framePresets' contract) — reachable
    // via the columns menu
    hidden: [...preset.hidden.filter((k) => present.has(k)), ...rest],
  }
}

/** one bucket for every spelling of "nothing here" so the dropdown shows a
 *  single "(blank)" entry, matching fmtCell's em-dash */
const BLANK = '(blank)'
const facetKey = (v: unknown): string =>
  v == null || v === '' ? BLANK : String(v)

interface Props {
  customerId: string
  frame: GoldFrameName
}

export function GoldTable({ customerId, frame }: Props) {
  // no cache on purpose: gold mutates on every ingest — refetch-on-mount
  // keeps this always-correct, and the frames are a few thousand rows
  const [rows, setRows] = useState<Row[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<GoldFileInfo[]>([])
  const [bronzeFileId, setBronzeFileId] = useState<number | undefined>(undefined)
  // client-side value filters over the fetched rows: column -> selected
  // value ('' / absent = all). Declared per frame in framePresets.facets.
  const [facetValues, setFacetValues] = useState<Record<string, string>>({})

  useEffect(() => {
    fetchGoldFiles(customerId)
      .then((all) => setFiles(all.filter(
        (f) => FRAME_SOURCE_TYPES[frame].includes(f.source_type))))
      .catch(() => setFiles([]))
    setBronzeFileId(undefined)
  }, [customerId, frame])

  useEffect(() => {
    let live = true
    setRows(null)
    setError(null)
    setFacetValues({})   // option lists change with the fetched rows
    fetchGoldFrame(customerId, frame, bronzeFileId)
      .then((d) => {
        if (live) {
          setRows(d.rows)
          setTotal(d.total)
        }
      })
      .catch((e) => live && setError(String(e.message ?? e)))
    return () => {
      live = false
    }
  }, [customerId, frame, bronzeFileId])

  // per-facet [value, count] lists from the UNFILTERED rows, so every
  // option stays visible (with its count) while another facet is active
  const facets = useMemo(() => {
    const declared = SHARED_PRESETS[frame].facets ?? []
    const all = rows ?? []
    return declared
      .filter(([key]) => all.some((r) => key in r))
      .map(([key, label]) => {
        const counts = new Map<string, number>()
        for (const r of all) {
          const v = facetKey(r[key])
          counts.set(v, (counts.get(v) ?? 0) + 1)
        }
        const options = [...counts.entries()].sort(([a], [b]) =>
          a === BLANK ? 1 : b === BLANK ? -1 : a.localeCompare(b))
        return { key, label, options }
      })
  }, [frame, rows])

  const filteredRows = useMemo(() => {
    const active = Object.entries(facetValues).filter(([, v]) => v !== '')
    const all = rows ?? []
    return active.length === 0
      ? all
      : all.filter((r) => active.every(([k, v]) => facetKey(r[k]) === v))
  }, [rows, facetValues])

  const filter = (
    <>
      <label className="gold-filter">
        <span>Ingestion:</span>
        <select value={bronzeFileId ?? ''}
                onChange={(e) => setBronzeFileId(
                  e.target.value === '' ? undefined : Number(e.target.value))}>
          <option value="">All data</option>
          {files.map((f) => (
            <option key={f.bronze_file_id} value={f.bronze_file_id}>
              {f.original_name}
            </option>
          ))}
        </select>
      </label>
      {facets.map(({ key, label, options }) => (
        <label className="gold-filter" key={key}>
          <span>{label}:</span>
          <select value={facetValues[key] ?? ''}
                  onChange={(e) => setFacetValues((prev) => ({ ...prev, [key]: e.target.value }))}>
            <option value="">All ({(rows ?? []).length.toLocaleString('en-IN')})</option>
            {options.map(([v, n]) => (
              <option key={v} value={v}>
                {v} ({n.toLocaleString('en-IN')})
              </option>
            ))}
          </select>
        </label>
      ))}
    </>
  )

  if (error) return <p className="frame-note">could not load: {error}</p>
  if (rows === null)
    return (
      <p className="frame-note">
        <span className="quill" /> loading gold {frame}…
      </p>
    )

  // columns come from the UNFILTERED rows so a facet can't shift the
  // column set / hidden list; DataTable gets the filtered rows and keeps
  // its own sort / visibility state across facet changes (no key remount)
  const { columns, hidden } = buildColumns(frame, rows)
  return (
    <>
      <DataTable
        rows={filteredRows}
        columns={columns}
        numericIds={AMOUNT_COLS}
        initialHidden={hidden}
        toolbar={filter}
        totalRows={rows.length}
      />
      {rows.length < total && (
        <p className="frame-note">
          showing the first {rows.length.toLocaleString('en-IN')} of{' '}
          {total.toLocaleString('en-IN')} rows
        </p>
      )}
    </>
  )
}
