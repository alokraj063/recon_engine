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
  onReconcile: (statementBronzeId: number, mode: RunMode) => void
  onGoToIngest: () => void
  refreshKey: number
}

const MODES: Array<{ value: RunMode; label: string; blurb: string }> = [
  {
    value: 'snapshot',
    label: 'Snapshot',
    blurb: 'A standalone reconciliation of the chosen statement against all current gold bills; nothing is carried forward.',
  },
  {
    value: 'incremental',
    label: 'Incremental',
    blurb: "Feeds this customer's running ledger: locked matches stay settled and open exceptions carry across runs.",
  },
]

export function ReconcileForm({
  running, customers, customerId, onCustomerChange,
  onReconcile, onGoToIngest, refreshKey,
}: Props) {
  const [mode, setMode] = useState<RunMode>('snapshot')
  const [statements, setStatements] = useState<GoldFileInfo[] | null>(null)
  const [statementId, setStatementId] = useState<number | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [showConfig, setShowConfig] = useState(false)

  useEffect(() => {
    fetchGoldFiles(customerId)
      .then((files) => {
        const stmts = files.filter((f) => f.source_type === 'bank_statement')
        setStatements(stmts)
        // newest first from the API; preselect it (keep a still-valid pick)
        setStatementId((prev) =>
          prev !== null && stmts.some((s) => s.bronze_file_id === prev)
            ? prev
            : stmts[0]?.bronze_file_id ?? null)
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

  return (
    <section className="intake">
      <div className="ingest-head">
        <h2>Reconcile from gold</h2>
        <button className={`btn-refresh new-customer-btn btn-ic${showConfig ? ' on' : ''}`}
                onClick={() => setShowConfig((v) => !v)}>
          <Settings2 size={14} strokeWidth={1.75} /> Matching config {showConfig ? '▴' : '▾'}
        </button>
      </div>
      <p className="hint">
        Runs purely against the ingested gold layer — no uploads, no re-parsing. Pick which
        statement's credits to reconcile; bills are always the current gold state.
      </p>

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
              <button className="link-btn" onClick={onGoToIngest}>ingest files first</button>
            </span>
          ) : (
            <select value={statementId ?? ''} disabled={running}
                    onChange={(e) => setStatementId(Number(e.target.value))}>
              {statements.map((s) => (
                <option key={s.bronze_file_id} value={s.bronze_file_id}>
                  {stmtLabel(s)}
                </option>
              ))}
            </select>
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
        <button className="btn-run" disabled={statementId === null || running}
                onClick={() => statementId !== null && onReconcile(statementId, mode)}>
          Run reconciliation
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
