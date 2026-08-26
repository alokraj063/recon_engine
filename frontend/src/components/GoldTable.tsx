import { useEffect, useState } from 'react'
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
    hidden: preset.hidden.filter((k) => present.has(k)),
  }
}

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
  )

  if (error) return <p className="frame-note">could not load: {error}</p>
  if (rows === null)
    return (
      <p className="frame-note">
        <span className="quill" /> loading gold {frame}…
      </p>
    )

  const { columns, hidden } = buildColumns(frame, rows)
  return (
    <>
      <DataTable
        rows={rows}
        columns={columns}
        numericIds={AMOUNT_COLS}
        initialHidden={hidden}
        toolbar={filter}
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
