import { useEffect, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { FrameName, Row } from '../types'
import { fetchFrame } from '../api'
import { AMOUNT_COLS, fmtCell } from '../format'
import { BillTrailDetail } from './BillTrailDetail'
import { DataTable } from './DataTable'

/** Per-frame presets: curated column order + labels; everything else in
 *  the payload is appended hidden, reachable via the columns menu. */
const PRESETS: Record<FrameName, { curated: Array<[string, string]>; hidden: string[] }> = {
  bank: {
    curated: [
      ['UsedInRecon', 'Used'],
      ['txn_type', 'Type'],
      ['amount', 'Amount'],
      ['value_date', 'Value date'],
      ['zone_guess', 'Zone'],
      ['narrative', 'Narrative'],
      ['bank_ref', 'Bank ref'],
      ['customer_ref', 'Customer ref'],
      ['page', 'Page'],
    ],
    hidden: ['supplementary', 'timestamp'],
  },
  bills: {
    curated: [
      ['BillNumber', 'Bill no.'],
      ['ContractNo', 'Contract'],
      ['Zone', 'Zone'],
      ['Status', 'Status'],
      ['BillDate', 'Bill date'],
      ['BillAmt', 'Bill amt'],
      ['PassedAmt', 'Passed amt'],
      ['DeductedAmt', 'Deducted amt'],
      ['NetAmt', 'Net amt'],
      ['CO6No', 'CO6 no.'],
      ['CO6Date', 'CO6 date'],
      ['CO7No', 'CO7 no.'],
      ['CO7Date', 'CO7 date'],
      ['PaymentAdviceDateToBank', 'Advice date'],
      ['RecoveryCount', 'Recov.'],
      ['ReasonForReturn', 'Reason for return'],
      ['NetCheck', 'Net ✓'],
      ['RecoveryCheck', 'Recov ✓'],
      ['Sheet', 'Sheet'],
      ['DataRow', 'Row'],
    ],
    hidden: ['PartyName', 'PartyCode', 'UnparsedHeader', 'HeaderRow', 'Recoveries',
             'RecoverySum', 'AccountingUnit', 'ContractDate'],
  },
  bills_enriched: {
    curated: [
      ['BillNumber', 'Bill no.'],
      ['Zone', 'Zone'],
      ['Status', 'Status'],
      ['Settled', 'Settled in stmt'],
      ['AttemptCount', 'Attempts'],
      ['NetAmt', 'Net amt'],
      ['PaymentAdviceDateToBank', 'Advice date'],
      ['LineageStatus', 'Lineage'],
      ['PO', 'PO'],
      ['Receipt_Doc', 'Receipt doc'],
      ['RNOTE_MatchedVia', 'RNOTE via'],
      ['CRN_MatchedVia', 'CRN via'],
      ['CO7No', 'CO7 no.'],
      ['CO7Date', 'CO7 date'],
    ],
    hidden: [],   // computed below: everything not curated
  },
  recoveries: {
    curated: [
      ['BillNumber', 'Bill no.'],
      ['CO6No', 'CO6 no.'],
      ['Sheet', 'Sheet'],
      ['RecoveryHead', 'Recovery head'],
      ['RecoveryAmt', 'Amount'],
      ['RecoveryText', 'Raw text'],
    ],
    hidden: ['BillIndex'],
  },
}

function buildColumns(name: FrameName, rows: Row[]): { columns: ColumnDef<Row>[]; hidden: string[] } {
  const preset = PRESETS[name]
  const present = new Set(rows.flatMap((r) => Object.keys(r)))
  const curated = preset.curated.filter(([k]) => present.has(k))
  const rest = [...present].filter((k) => !preset.curated.some(([c]) => c === k)).sort()

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
    columns: [...curated.map(([k, l]) => make(k, l)), ...rest.map((k) => make(k, k))],
    // enriched frame: hide every non-curated column (the raw RN_/CR_ set is large)
    hidden: name === 'bills_enriched' ? rest : preset.hidden,
  }
}

interface Props {
  runId: string
  name: FrameName
}

// fetched frames, kept across tab switches (keyed by run so a new run
// naturally misses the cache)
const frameCache = new Map<string, Row[]>()

export function SourceTable({ runId, name }: Props) {
  const cacheKey = `${runId}:${name}`
  const [rows, setRows] = useState<Row[] | null>(frameCache.get(cacheKey) ?? null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (frameCache.has(cacheKey)) {
      setRows(frameCache.get(cacheKey)!)
      return
    }
    let live = true
    setRows(null)
    setError(null)
    fetchFrame(runId, name)
      .then((d) => {
        frameCache.set(cacheKey, d.rows)
        if (live) setRows(d.rows)
      })
      .catch((e) => live && setError(String(e.message ?? e)))
    return () => {
      live = false
    }
  }, [runId, name, cacheKey])

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
          ? (row) => <BillTrailDetail row={row} title={`Bill ${row.BillNumber ?? ''} — document history`} />
          : undefined
      }
    />
  )
}
