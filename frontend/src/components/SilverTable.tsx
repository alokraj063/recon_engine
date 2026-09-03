import { useEffect, useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { Row, SilverFileInfo } from '../types'
import { fetchSilverFiles, fetchSilverFrame } from '../api'
import { AMOUNT_COLS, fmtCell } from '../format'
import { DataTable } from './DataTable'

/** Bookkeeping the API adds to every silver row — present but folded away. */
const PROVENANCE = ['bronze_file_id', 'row_seq']

/** Silver has no curated preset: the columns ARE the source's own field
 *  names, and a new adapter invents its own. Order follows the payload's
 *  key order, which is the parser's column order. */
function buildColumns(rows: Row[]): { columns: ColumnDef<Row>[]; hidden: string[] } {
  const keys: string[] = []
  for (const row of rows) {
    for (const k of Object.keys(row)) if (!keys.includes(k)) keys.push(k)
  }
  const ordered = [...keys.filter((k) => !PROVENANCE.includes(k)),
                   ...keys.filter((k) => PROVENANCE.includes(k))]
  return {
    columns: ordered.map((key) => ({
      id: key,
      header: key,
      accessorFn: (row: Row) => row[key],
      cell: (ctx) => {
        const text = fmtCell(key, ctx.row.original[key])
        return text === '—' ? <span className="empty-cell">—</span> : text
      },
    })),
    hidden: keys.filter((k) => PROVENANCE.includes(k)),
  }
}

interface Props {
  customerId: string
}

export function SilverTable({ customerId }: Props) {
  // no cache: silver grows with every ingest, like gold
  const [files, setFiles] = useState<SilverFileInfo[] | null>(null)
  const [bronzeFileId, setBronzeFileId] = useState<number | undefined>(undefined)
  const [frame, setFrame] = useState<string | null>(null)
  const [rows, setRows] = useState<Row[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setFiles(null)
    setBronzeFileId(undefined)
    setFrame(null)
    fetchSilverFiles(customerId)
      .then(setFiles)
      .catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  // frames on offer follow the file filter, so a picker never offers a
  // frame the chosen file has no rows of
  const frames = useMemo(() => {
    const scope = (files ?? []).filter(
      (f) => bronzeFileId === undefined || f.bronze_file_id === bronzeFileId)
    const names = new Set<string>()
    scope.forEach((f) => Object.keys(f.silver_counts).forEach((n) => names.add(n)))
    return [...names].sort()
  }, [files, bronzeFileId])

  useEffect(() => {
    if (frames.length && (frame === null || !frames.includes(frame)))
      setFrame(frames[0])
  }, [frames, frame])

  useEffect(() => {
    if (!frame) return
    let live = true
    setRows(null)
    setError(null)
    fetchSilverFrame(customerId, frame, bronzeFileId)
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

  const toolbar = (
    <>
      <label className="gold-filter">
        <span>Ingestion:</span>
        <select value={bronzeFileId ?? ''}
                onChange={(e) => setBronzeFileId(
                  e.target.value === '' ? undefined : Number(e.target.value))}>
          <option value="">All files</option>
          {(files ?? []).map((f) => (
            <option key={f.bronze_file_id} value={f.bronze_file_id}>
              {f.original_name}
            </option>
          ))}
        </select>
      </label>
      <label className="gold-filter">
        <span>Frame:</span>
        <select value={frame ?? ''} onChange={(e) => setFrame(e.target.value)}>
          {frames.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
      </label>
    </>
  )

  if (error) return <p className="frame-note">could not load: {error}</p>
  if (files !== null && files.length === 0)
    return (
      <p className="frame-note">
        Nothing parsed yet — ingest a source file and its rows land here in
        the shape its own parser produced.
      </p>
    )
  if (files === null || rows === null)
    return (
      <p className="frame-note">
        <span className="quill" /> loading silver {frame ?? 'rows'}…
      </p>
    )

  const { columns, hidden } = buildColumns(rows)
  return (
    <>
      <DataTable
        rows={rows}
        columns={columns}
        numericIds={AMOUNT_COLS}
        initialHidden={hidden}
        toolbar={toolbar}
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
