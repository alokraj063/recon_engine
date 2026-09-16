import { useEffect, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { GoldFileInfo, GoldFrameName, Row } from '../types'
import { fetchGoldFiles, fetchGoldFrame } from '../api'
import { AMOUNT_COLS, fmtCell, inDayRange } from '../format'
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

  const facets = new Map(preset.facets ?? [])
  const make = (key: string, label: string): ColumnDef<Row> => ({
    id: key,
    header: label,
    meta: facets.has(key) ? { facet: true, facetLabel: facets.get(key) } : undefined,
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

/** The date a frame's rows are windowed on — the SAME date db/overview
 *  counts that frame's figure with (credits by value_date; bills by
 *  submission_date, falling back to bill_date), so a Command Center link
 *  lands on exactly the rows it counted. Frames absent here offer no
 *  date filter. */
const DATE_FIELD: Partial<Record<GoldFrameName, { label: string; get: (r: Row) => string }>> = {
  bank: { label: 'Value date', get: (r) => String(r.value_date ?? '').slice(0, 10) },
  bills: {
    label: 'Submitted',
    get: (r) => String(r.submission_date ?? r.bill_date ?? '').slice(0, 10),
  },
}

/**
 * What a Command Center figure asks a gold table to show on arrival:
 * column filters over the frame's facet columns ({column id: values})
 * plus the page's date window (yyyy-mm-dd, '' = unbounded). Like
 * LedgerIntent, every preset lands as a visible, removable chip.
 */
export interface GoldIntent {
  frame: GoldFrameName
  filters: Record<string, string[]>
  from?: string
  to?: string
}

interface Props {
  customerId: string
  frame: GoldFrameName
  /** filters to open with; applied only when it names this frame */
  intent?: GoldIntent | null
  /** called once the intent has been taken so the parent clears it —
   *  an arrival instruction, not a selection */
  onIntentHandled?: () => void
}

export function GoldTable({ customerId, frame, intent, onIntentHandled }: Props) {
  // captured on mount: the parent clears the intent right away, but the
  // table only renders (and reads its initial filters) once rows arrive
  const [arrival] = useState(() => (intent?.frame === frame ? intent : undefined))
  useEffect(() => { if (intent) onIntentHandled?.() }, [intent, onIntentHandled])
  // App keys this component by customer + frame, so a switch remounts it
  // and the window starts empty — no reset effect needed
  const dateField = DATE_FIELD[frame]
  const [dateFrom, setDateFrom] = useState(() => (dateField && arrival?.from) || '')
  const [dateTo, setDateTo] = useState(() => (dateField && arrival?.to) || '')
  // no cache on purpose: gold mutates on every ingest — refetch-on-mount
  // keeps this always-correct, and the frames are a few thousand rows
  const [rows, setRows] = useState<Row[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<GoldFileInfo[]>([])
  const [bronzeFileId, setBronzeFileId] = useState<number | undefined>(undefined)

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
      {dateField && (
        <label className="gold-filter gold-dates">
          <span>{dateField.label}:</span>
          <input type="date" value={dateFrom} aria-label={`${dateField.label} from`}
                 onChange={(e) => setDateFrom(e.target.value)} />
          <span className="chip-note">to</span>
          <input type="date" value={dateTo} aria-label={`${dateField.label} to`}
                 onChange={(e) => setDateTo(e.target.value)} />
        </label>
      )}
    </>
  )
  // the ingestion pick and the date window are page-level filters: they
  // show in the same chip row as the column filters (DataTable renders all)
  const picked = files.find((f) => f.bronze_file_id === bronzeFileId)
  const windowOn = !!dateField && !!(dateFrom || dateTo)
  const externalChips = [
    ...(picked
      ? [{ key: 'ingestion', label: 'Ingestion', values: [picked.original_name],
           onRemove: () => setBronzeFileId(undefined) }]
      : []),
    ...(windowOn
      ? [{ key: 'date', label: dateField!.label,
           values: [`${dateFrom || '…'} → ${dateTo || '…'}`],
           onRemove: () => { setDateFrom(''); setDateTo('') } }]
      : []),
  ]

  if (error) return <p className="frame-note">could not load: {error}</p>
  if (rows === null)
    return (
      <p className="frame-note">
        <span className="quill" /> loading gold {frame}…
      </p>
    )

  // column filters (facets declared in framePresets) are DataTable's own
  const { columns, hidden } = buildColumns(frame, rows)
  // a row with no date at all is outside any bounded window, as it is for
  // the server's count (a NULL date never satisfies a bound)
  const shown = windowOn
    ? rows.filter((r) => {
        const d = dateField!.get(r)
        return !!d && inDayRange(d, dateFrom, dateTo)
      })
    : rows
  return (
    <>
      <DataTable
        rows={shown}
        totalRows={rows.length}
        columns={columns}
        numericIds={AMOUNT_COLS}
        initialHidden={hidden}
        toolbar={filter}
        externalChips={externalChips}
        initialFilters={arrival?.filters}
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
