import { useEffect, useState, type ReactNode } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { GoldFileInfo, GoldFrameName, Row } from '../types'
import { fetchGoldFiles, fetchGoldFrame, setCreditSource } from '../api'
import { ApiError } from '../types'
import { AMOUNT_COLS, fmtCell, inDayRange } from '../format'
import { COLUMN_FORMATS, SHARED_PRESETS } from '../framePresets'
import { DataTable } from './DataTable'
import { Notice } from './ui'

/** How each gold table opens: newest first on its own date column, and —
 *  on the bank table — showing the CREDITS. Reconciliation is about money
 *  in; the debits outnumber them 3:1 and used to fill the first screens.
 *  Both land as ordinary sort / filter state the analyst can change, and
 *  the credits filter shows as a removable chip. */
const OPENS_WITH: Partial<Record<GoldFrameName, {
  sort?: { id: string; desc: boolean }[]
  filters?: Record<string, string[]>
}>> = {
  bank: { sort: [{ id: 'value_date', desc: true }], filters: { used_in_recon: ['true'] } },
  bills: { sort: [{ id: 'submission_date', desc: true }] },
}

/** Which source types can own rows of each gold frame — scopes the
 *  ingestion-filter dropdown to relevant files. */
const FRAME_SOURCE_TYPES: Record<GoldFrameName, string[]> = {
  bank: ['bank_statement'],
  bills: ['bill_status'],
  recoveries: ['bill_status'],
  lineage: ['lineage_rnote', 'lineage_crn'],
}

function buildColumns(frame: GoldFrameName, rows: Row[],
                      renderCell?: (key: string, row: Row) => ReactNode | undefined,
): { columns: ColumnDef<Row>[]; hidden: string[] } {
  const preset = SHARED_PRESETS[frame]
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  const curated = preset.curated.filter(([k]) => present.has(k))
  const rest = [...present].filter((k) => !preset.curated.some(([c]) => c === k)).sort()

  const facets = new Map(preset.facets ?? [])
  const clamped = new Set(preset.clamp ?? [])
  const make = (key: string, label: string): ColumnDef<Row> => ({
    id: key,
    header: label,
    meta: facets.has(key)
      ? { facet: true, facetLabel: facets.get(key), facetFormat: COLUMN_FORMATS[key] }
      : undefined,
    accessorFn: (row) => row[key],
    cell: (ctx) => {
      const custom = renderCell?.(key, ctx.row.original)
      if (custom !== undefined) return custom
      const text = fmtCell(key, ctx.row.original[key])
      if (text === '—') return <span className="empty-cell">—</span>
      return clamped.has(key) ? <span className="cell-clamp" title={text}>{text}</span> : text
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
  // Source edits (bank only): a change refetches the frame IN PLACE (the
  // server re-derives source + credit_scope), keeping the table mounted so
  // its filters, sort and scroll survive the edit
  const [editing, setEditing] = useState<string | null>(null)
  const [editError, setEditError] = useState<string | null>(null)

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
      <label className="dt-field">
        <span>Ingestion</span>
        <select value={bronzeFileId ?? ''}
                onChange={(e) => setBronzeFileId(
                  e.target.value === '' ? undefined : Number(e.target.value))}>
          <option value="">All ingestions</option>
          {files.map((f) => (
            <option key={f.bronze_file_id} value={f.bronze_file_id}>
              {f.original_name}
            </option>
          ))}
        </select>
      </label>
      {dateField && (
        <label className="dt-field gold-dates">
          <span>{dateField.label}</span>
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

  if (error) return <div className="dt-state"><Notice tone="error">Could not load this table: {error}</Notice></div>
  if (rows === null) return <div className="dt-loading" aria-busy="true"><span className="quill" /> Loading…</div>

  const editSource = (row: Row, value: string) => {
    const id = String(row.gold_bank_txn_id)
    setEditing(id)
    setEditError(null)
    setCreditSource(customerId, id,
                    value === 'AUTO' ? null : (value as 'IREPS' | 'NON_IREPS'))
      .then(() => fetchGoldFrame(customerId, frame, bronzeFileId))
      .then((d) => { setRows(d.rows); setTotal(d.total) })
      .catch((e) => setEditError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setEditing(null))
  }
  // Bank credits: Source is editable in place. Debits have no source to
  // decide; the selection mirrors the Analyst queue's Non-IREPS decisions
  const renderCell = frame === 'bank'
    ? (key: string, row: Row): ReactNode | undefined => {
        if (key !== 'source' || !row.gold_bank_txn_id || row.source === 'DEBIT') return undefined
        const decided = row.source_decided === true
        return (
          <span className="src-edit" onClick={(e) => e.stopPropagation()}>
            <select value={String(row.source ?? '')} disabled={editing === row.gold_bank_txn_id}
                    aria-label="Source"
                    title={decided ? 'Set by an analyst' : 'Derived from the narrative zone'}
                    className={`src-select src-${String(row.source).toLowerCase()}`}
                    onChange={(e) => editSource(row, e.target.value)}>
              <option value="IREPS">IREPS</option>
              <option value="NON_IREPS">Non-IREPS</option>
              {decided && <option value="AUTO">Auto (from zone)</option>}
            </select>
            {decided && <span className="src-edited" title="Set by an analyst">edited</span>}
          </span>
        )
      }
    : undefined
  // column filters (facets declared in framePresets) are DataTable's own
  const { columns, hidden } = buildColumns(frame, rows, renderCell)
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
      {editError && <div className="dt-state"><Notice tone="error">{editError}</Notice></div>}
      <DataTable
        fill
        rows={shown}
        totalRows={rows.length}
        columns={columns}
        numericIds={AMOUNT_COLS}
        initialHidden={hidden}
        toolbar={filter}
        externalChips={externalChips}
        initialSort={OPENS_WITH[frame]?.sort}
        initialFilters={arrival?.filters ?? OPENS_WITH[frame]?.filters}
      />
      {rows.length < total && (
        <div className="ui-card-foot">
          Showing {rows.length.toLocaleString('en-IN')} of{' '}
          {total.toLocaleString('en-IN')} rows. Filter by ingestion or date to narrow the result.
        </div>
      )}
    </>
  )
}
