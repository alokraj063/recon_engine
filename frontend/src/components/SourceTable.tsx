import { useEffect, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { FrameName, Row } from '../types'
import { fetchFrame } from '../api'
import { AMOUNT_COLS, fmtCell } from '../format'
import { combineFrameRows } from '../combineRuns'
import { SHARED_PRESETS, type FramePreset } from '../framePresets'
import { BillTrailDetail } from './BillTrailDetail'
import { DataTable } from './DataTable'

/** Per-frame presets: bank/bills/recoveries are shared with GoldTable
 *  (framePresets.ts); bills_enriched is a run artifact with settlement
 *  stamps, so it stays private here. Everything not curated is appended
 *  hidden, reachable via the columns menu. */
const PRESETS: Record<FrameName, FramePreset> = {
  bank: SHARED_PRESETS.bank,
  bills: SHARED_PRESETS.bills,
  recoveries: SHARED_PRESETS.recoveries,
  bills_enriched: {
    curated: [
      ['bill_number', 'Bill no.'],
      ['zone', 'Zone'],
      ['bill_status', 'Status'],
      ['Settled', 'Settled in stmt'],
      ['AttemptCount', 'Attempts'],
      ['net_payable_amount', 'Net payable'],
      ['payment_advice_date', 'Advice date'],
      ['LineageStatus', 'Lineage'],
      ['PO', 'PO'],
      ['Receipt_Doc', 'Receipt doc'],
      ['RNOTE_MatchedVia', 'RNOTE via'],
      ['CRN_MatchedVia', 'CRN via'],
      ['payment_order_ref', 'Pay order ref'],
      ['payment_order_date', 'Pay order date'],
    ],
    hidden: [],   // computed below: everything not curated
  },
}

function buildColumns(name: FrameName, rows: Row[]): { columns: ColumnDef<Row>[]; hidden: string[] } {
  const preset = PRESETS[name]
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  const curated = preset.curated.filter(([k]) => present.has(k))
  // multi-run union rows carry an origin label; it leads the table and
  // its id twin stays out of the column set
  const hasRun = present.has('Run')
  const rest = [...present]
    .filter((k) => k !== 'Run' && k !== 'run_id' && !preset.curated.some(([c]) => c === k))
    .sort()

  const make = (key: string, label: string): ColumnDef<Row> => ({
    id: key,
    header: label,
    accessorFn: (row) => row[key],
    cell: (ctx) => {
      const v = ctx.row.original[key]
      if (key === 'AttemptCount') {
        return typeof v === 'number' && v > 1
          ? <span className="chip chip-attempts">×{v} attempts</span>
          : <span className="empty-cell">—</span>
      }
      if (key === 'Settled') {
        if (v !== 'SETTLED') return <span className="empty-cell">—</span>
        const r = ctx.row.original
        return (
          <span className="settled-cell">
            <span className="chip chip-settled">✓ SETTLED</span>
            <span className="settled-ref">
              {String(r.Settled_ValueDate ?? '')} · {String(r.Settled_BankRef ?? '')}
            </span>
          </span>
        )
      }
      const text = fmtCell(key, v)
      return text === '—' ? <span className="empty-cell">—</span> : text
    },
  })

  return {
    columns: [
      ...(hasRun ? [make('Run', 'Run')] : []),
      ...curated.map(([k, l]) => make(k, l)),
      ...rest.map((k) => make(k, k)),
    ],
    // enriched frame: hide every non-curated column (the raw RN_/CR_ set is large)
    hidden: name === 'bills_enriched' ? rest : preset.hidden,
  }
}

interface Props {
  /** selected runs, newest first; >1 renders the deduped union */
  runs: Array<{ runId: string; label: string }>
  name: FrameName
}

// fetched frames, kept across tab switches (keyed by run so a new run
// naturally misses the cache)
const frameCache = new Map<string, Row[]>()

async function loadFrame(runId: string, name: FrameName): Promise<Row[]> {
  const cacheKey = `${runId}:${name}`
  const hit = frameCache.get(cacheKey)
  if (hit) return hit
  const d = await fetchFrame(runId, name)
  // frames are immutable per run, so the cache is always correct;
  // cap it so browsing many persisted runs can't grow it unbounded
  if (frameCache.size >= 32) {
    const oldest = frameCache.keys().next().value
    if (oldest !== undefined) frameCache.delete(oldest)
  }
  frameCache.set(cacheKey, d.rows)
  return d.rows
}

export function SourceTable({ runs, name }: Props) {
  const selKey = `${runs.map((r) => r.runId).join(',')}:${name}`
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setRows(null)
    setError(null)
    Promise.all(
      runs.map((r) =>
        loadFrame(r.runId, name)
          .then((rows) => ({ runId: r.runId, label: r.label, rows }))
          .catch(() => null)),   // a failed run drops out, not the table
    ).then((loaded) => {
      if (!live) return
      const ok = loaded.filter((x): x is NonNullable<typeof x> => x !== null)
      if (!ok.length) {
        setError('could not load run frames')
        return
      }
      setRows(combineFrameRows(ok, name))
    })
    return () => {
      live = false
    }
    // selKey covers runs + name content
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selKey])

  if (error) return <p className="frame-note">could not load: {error}</p>
  if (rows === null)
    return (
      <p className="frame-note">
        <span className="quill" /> loading {name.replace('_', ' ')}…
      </p>
    )

  // Display gate: only HIGH-confidence matches count as settled. Injecting
  // the token into the row makes the global filter find "SETTLED".
  const shown =
    name === 'bills_enriched'
      ? rows.map((r) => ({
          ...r,
          Settled: r.SettledInStatement && r.Settled_Confidence === 'HIGH' ? 'SETTLED' : null,
          // filterable token for bills that went through several attempts
          Attempts_Flag: typeof r.AttemptCount === 'number' && r.AttemptCount > 1 ? 'MULTI_ATTEMPT' : null,
        }))
      : rows

  const { columns, hidden } = buildColumns(name, shown)
  return (
    <DataTable
      rows={shown}
      columns={columns}
      numericIds={AMOUNT_COLS}
      initialHidden={hidden}
      renderDetail={
        name === 'bills_enriched'
          ? (row) => <BillTrailDetail row={row} title={`Bill ${row.bill_number ?? ''} — lineage`} />
          : undefined
      }
    />
  )
}
