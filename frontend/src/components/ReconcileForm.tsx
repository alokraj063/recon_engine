import { useEffect, useState } from 'react'
import { Settings2 } from 'lucide-react'
import type { CustomerInfo, GoldFileInfo, RunMode } from '../types'
import { fetchGoldFiles } from '../api'
import { MatchingConfigPanel } from './MatchingConfigPanel'

interface Props {
  running: boolean
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onReconcile: (statementBronzeIds: number[], mode: RunMode) => void
  onGoToIngest: () => void
  refreshKey: number
}

// Incremental first and default: every workspace page (AR Reconciliation,
// Analyst queue, Command Center) reads the ledger only incremental feeds
const MODES: Array<{ value: RunMode; label: string; blurb: string }> = [
  {
    value: 'incremental',
    label: 'Incremental',
    blurb: "Feeds this customer's running ledger — matches lock, open exceptions carry forward, and AR Reconciliation / Analyst queue fill up. The normal mode.",
  },
  {
    value: 'snapshot',
    label: 'Snapshot',
    blurb: 'A one-off look: reconciles the chosen statement against all current gold bills and stores only this run\'s result. Nothing reaches the ledger or AR.',
  },
]

export function ReconcileForm({
  running, customers, customerId, onCustomerChange,
  onReconcile, onGoToIngest, refreshKey,
}: Props) {
  const [mode, setMode] = useState<RunMode>('incremental')
  const [statements, setStatements] = useState<GoldFileInfo[] | null>(null)
  // several statements may be ticked: one run over the union of their credits
  const [statementIds, setStatementIds] = useState<number[]>([])
  const [elapsed, setElapsed] = useState(0)
  const [showConfig, setShowConfig] = useState(false)

  useEffect(() => {
    fetchGoldFiles(customerId)
      .then((files) => {
        const stmts = files.filter((f) => f.source_type === 'bank_statement')
        setStatements(stmts)
        // newest first from the API; tick it by default (keep the still-valid
        // part of a prior selection)
        setStatementIds((prev) => {
          const kept = prev.filter((id) => stmts.some((s) => s.bronze_file_id === id))
          return kept.length ? kept : stmts[0] ? [stmts[0].bronze_file_id] : []
        })
      })
      .catch(() => setStatements([]))
  }, [customerId, refreshKey])

  useEffect(() => {
    if (!running) return
    setElapsed(0)
    const t = window.setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => window.clearInterval(t)
  }, [running])

  const stmtLabel = (s: GoldFileInfo) => {
    const st = s.statement
    const dates = st?.value_date_min
      ? st.value_date_min === st.value_date_max
        ? st.value_date_min
        : `${st.value_date_min} – ${st.value_date_max}`
      : 'no dates'
    return `${s.original_name} — ${dates} — ${st?.credits ?? 0} credits`
  }

  const stmts = statements ?? []
  const allSelected = stmts.length > 0 && stmts.every((s) => statementIds.includes(s.bronze_file_id))
  const someSelected = statementIds.length > 0
  const creditsOf = (list: GoldFileInfo[]) =>
    list.reduce((n, s) => n + (s.statement?.credits ?? 0), 0)
  const totalCredits = creditsOf(stmts)
  const selectedCredits = creditsOf(stmts.filter((s) => statementIds.includes(s.bronze_file_id)))

  return (
    <section className="intake">
      <div className="ingest-head">
        <h2 className="page-title">Initiate Reconciliation</h2>
        <button className={`btn-refresh new-customer-btn btn-ic${showConfig ? ' on' : ''}`}
                onClick={() => setShowConfig((v) => !v)}>
          <Settings2 size={14} strokeWidth={1.75} /> Matching config {showConfig ? '▴' : '▾'}
        </button>
      </div>
      {showConfig && (
        <div className="config-inset">
          <MatchingConfigPanel customerId={customerId} />
        </div>
      )}

      <div className="run-context">
        <label className="ctx-field">
          <span className="slot-label">Customer</span>
          <select value={customerId} onChange={(e) => onCustomerChange(e.target.value)}
                  disabled={running}>
            {customers.map((c) => (
              <option key={c.key} value={c.key}>{c.name} ({c.key})</option>
            ))}
          </select>
        </label>

        <label className="ctx-field ctx-wide">
          <span className="slot-label">Statement</span>
          {statements === null ? (
            <span className="frame-note">loading…</span>
          ) : statements.length === 0 ? (
            <span className="frame-note">
              No statements ingested yet —{' '}
              <button className="link-btn" onClick={onGoToIngest}>ingest documents first</button>
            </span>
          ) : (
            <div className="stmt-picker">
              {/* Select all: a tri-state master checkbox above the (scrolling)
                  list, so it stays reachable however many daily statements
                  have been ingested. Ticked = every statement selected,
                  indeterminate = some, clear = none. */}
              <label className={`stmt-row stmt-head${allSelected ? ' on' : ''}`}>
                <input type="checkbox" checked={allSelected} disabled={running}
                       ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected }}
                       aria-label="Select all statements"
                       onChange={(e) => setStatementIds(
                         e.target.checked ? statements.map((s) => s.bronze_file_id) : [])} />
                <span className="stmt-label">
                  Select all
                  <span className="chip-note">
                    {' '}· {statements.length} statement{statements.length === 1 ? '' : 's'} ·{' '}
                    {totalCredits} credits
                  </span>
                </span>
              </label>
              <div className="stmt-list">
                {statements.map((s) => {
                  const on = statementIds.includes(s.bronze_file_id)
                  return (
                    <label key={s.bronze_file_id} className={`stmt-row${on ? ' on' : ''}`}>
                      <input type="checkbox" checked={on} disabled={running}
                             onChange={(e) => setStatementIds((prev) =>
                               e.target.checked
                                 ? [...prev, s.bronze_file_id]
                                 : prev.filter((id) => id !== s.bronze_file_id))} />
                      <span className="stmt-label">{stmtLabel(s)}</span>
                    </label>
                  )
                })}
              </div>
              <div className="stmt-foot">
                <span className="chip-note">
                  {statementIds.length === 0
                    ? 'no statement selected'
                    : `${statementIds.length} of ${statements.length} selected · ${selectedCredits} credits`}
                </span>
                {statementIds.length > 0 && (
                  <button className="link-btn" disabled={running}
                          onClick={() => setStatementIds([])}>
                    clear
                  </button>
                )}
              </div>
            </div>
          )}
        </label>
      </div>

      <div className="mode-cards">
        {MODES.map((m) => (
          <label key={m.value} className={`mode-card${mode === m.value ? ' on' : ''}`}>
            <input type="radio" name="reconcile-mode" value={m.value}
                   checked={mode === m.value}
                   onChange={() => setMode(m.value)} disabled={running} />
            <span className="mode-name">{m.label}</span>
            <span className="mode-blurb">{m.blurb}</span>
          </label>
        ))}
      </div>

      <p className="footer-note">
        Tolerances, window and all matching rules come from the customer's matching
        config (⚙ top right) — edit and save there before running.
      </p>

      <div className="run-row">
        <button className="btn-run" disabled={statementIds.length === 0 || running}
                onClick={() => statementIds.length > 0 && onReconcile(statementIds, mode)}>
          Initiate reconciliation
        </button>
        {running && (
          <span className="running-note">
            <span className="quill" /> scoring and assigning… {elapsed}s
            <span className="running-hint"> (typical run: 5–20s)</span>
          </span>
        )}
      </div>
    </section>
  )
}
