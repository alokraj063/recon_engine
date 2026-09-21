import { useEffect, useState } from 'react'
import { ArrowRight, GitMerge, Inbox, Settings2 } from 'lucide-react'
import type { CustomerInfo, GoldFileInfo, RunMode } from '../types'
import { fetchGoldFiles } from '../api'
import { fmtDay, n } from '../format'
import { MatchingConfigPanel } from './MatchingConfigPanel'
import { Card, CustomerSelect, EmptyState, PageHeader, TextLink, ToolSep } from './ui'

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
const MODES: Array<{ value: RunMode; label: string; tag?: string; blurb: string }> = [
  {
    value: 'incremental',
    label: 'Incremental',
    tag: 'Recommended',
    blurb: "Feeds this customer's running ledger — matches lock, open exceptions carry forward, and AR Reconciliation / Analyst queue fill up.",
  },
  {
    value: 'snapshot',
    label: 'Snapshot',
    blurb: "A one-off look: reconciles the chosen statements against all current gold bills and stores only this run's result. Nothing reaches the ledger or AR.",
  },
]

/** a statement's credit period, "3 Aug 2026 – 9 Aug 2026" */
function period(s: GoldFileInfo): string {
  const st = s.statement
  if (!st?.value_date_min) return 'no dates'
  return st.value_date_min === st.value_date_max
    ? fmtDay(st.value_date_min)
    : `${fmtDay(st.value_date_min)} – ${fmtDay(st.value_date_max)}`
}

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

  const stmts = statements ?? []
  const allSelected = stmts.length > 0 && stmts.every((s) => statementIds.includes(s.bronze_file_id))
  const someSelected = statementIds.length > 0
  const creditsOf = (list: GoldFileInfo[]) =>
    list.reduce((a, s) => a + (s.statement?.credits ?? 0), 0)
  const totalCredits = creditsOf(stmts)
  const selectedCredits = creditsOf(stmts.filter((s) => statementIds.includes(s.bronze_file_id)))
  const customerName = customers.find((c) => c.key === customerId)?.name ?? customerId
  const chosenMode = MODES.find((m) => m.value === mode)!

  return (
    <section className="ui-page">
      <PageHeader title="Initiate Reconciliation"
                  context={<>Match bank credits against the gold bills of {customerName}</>}>
        <CustomerSelect customers={customers} value={customerId} onChange={onCustomerChange} />
        <ToolSep />
        <button type="button" className={`ui-btn${showConfig ? ' is-on' : ''}`}
                aria-pressed={showConfig} onClick={() => setShowConfig((v) => !v)}>
          <Settings2 size={15} strokeWidth={1.75} /> Matching config
        </button>
      </PageHeader>

      {showConfig && (
        <Card title="Matching config"
              sub="Every run for this customer uses these saved rules — edit and save before running"
              ruled>
          <div className="ui-card-body config-body">
            <MatchingConfigPanel customerId={customerId} />
          </div>
        </Card>
      )}

      <div className="ui-grid-75 ingest-grid">
        <div className="ui-stack">
          <Card title={<><span className="step">1</span> Statements</>}
                sub="Tick one or several — one run covers all their credits"
                ruled>
            {statements === null ? (
              <div className="dt-loading"><span className="quill" /> Loading statements…</div>
            ) : statements.length === 0 ? (
              <EmptyState icon={<Inbox className="is-muted" size={22} strokeWidth={1.75} />}
                          title="No statements ingested yet">
                <span>Reconciliation runs on statements already in the gold layer.</span>
                <TextLink onClick={onGoToIngest}>
                  Ingest documents first <ArrowRight size={13} strokeWidth={2} />
                </TextLink>
              </EmptyState>
            ) : (
              <div className="rc-stmts">
                {/* Select all: a tri-state master checkbox above the (scrolling)
                    list, so it stays reachable however many daily statements
                    have been ingested. */}
                <label className="rc-stmt rc-stmt-head">
                  <input type="checkbox" checked={allSelected} disabled={running}
                         ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected }}
                         aria-label="Select all statements"
                         onChange={(e) => setStatementIds(
                           e.target.checked ? statements.map((s) => s.bronze_file_id) : [])} />
                  <span className="rc-stmt-name">Select all</span>
                  <span className="rc-stmt-period">Credit period</span>
                  <span className="rc-stmt-n">Credits</span>
                </label>
                <div className="rc-stmt-list">
                  {statements.map((s) => {
                    const on = statementIds.includes(s.bronze_file_id)
                    return (
                      <label key={s.bronze_file_id} className={`rc-stmt${on ? ' is-on' : ''}`}>
                        <input type="checkbox" checked={on} disabled={running}
                               onChange={(e) => setStatementIds((prev) =>
                                 e.target.checked
                                   ? [...prev, s.bronze_file_id]
                                   : prev.filter((id) => id !== s.bronze_file_id))} />
                        <span className="rc-stmt-name" title={s.original_name}>{s.original_name}</span>
                        <span className="rc-stmt-period">{period(s)}</span>
                        <span className="rc-stmt-n">{n(s.statement?.credits ?? 0)}</span>
                      </label>
                    )
                  })}
                </div>
                <div className="ui-card-foot">
                  {statementIds.length === 0
                    ? 'No statement selected'
                    : `${statementIds.length} of ${statements.length} selected · ${n(selectedCredits)} of ${n(totalCredits)} credits`}
                  {statementIds.length > 0 && (
                    <TextLink onClick={() => setStatementIds([])}>Clear</TextLink>
                  )}
                </div>
              </div>
            )}
          </Card>

          <Card title={<><span className="step">2</span> Mode</>}>
            <div className="rc-modes">
              {MODES.map((m) => (
                <label key={m.value} className={`rc-mode${mode === m.value ? ' is-on' : ''}`}>
                  <input type="radio" name="reconcile-mode" value={m.value}
                         checked={mode === m.value}
                         onChange={() => setMode(m.value)} disabled={running} />
                  <span className="rc-mode-name">
                    {m.label}
                    {m.tag && <span className="ui-pill tone-ok">{m.tag}</span>}
                  </span>
                  <span className="rc-mode-blurb">{m.blurb}</span>
                </label>
              ))}
            </div>
          </Card>
        </div>

        <aside className="ingest-side">
          <Card title="Run summary" ruled>
            <dl className="kv-list">
              <div><dt>Customer</dt><dd>{customerName}</dd></div>
              <div><dt>Statements</dt><dd>{n(statementIds.length)}</dd></div>
              <div><dt>Credits</dt><dd>{n(selectedCredits)}</dd></div>
              <div><dt>Mode</dt><dd>{chosenMode.label}</dd></div>
              <div>
                <dt>Matching rules</dt>
                <dd><TextLink onClick={() => setShowConfig(true)}>Saved config</TextLink></dd>
              </div>
            </dl>
            <div className="ui-card-body up-go">
              <button type="button" className="ui-btn ui-btn-primary up-go-btn"
                      disabled={statementIds.length === 0 || running}
                      onClick={() => statementIds.length > 0 && onReconcile(statementIds, mode)}>
                <GitMerge size={15} strokeWidth={1.75} />
                {running ? 'Reconciling…' : 'Initiate reconciliation'}
              </button>
              {running && (
                <p className="up-go-note">
                  <span className="quill" /> Scoring and assigning… {elapsed}s
                  <span className="running-hint"> (typically 5–20s)</span>
                </p>
              )}
              {!running && statementIds.length === 0 && stmts.length > 0 && (
                <p className="up-go-note">Tick at least one statement.</p>
              )}
            </div>
            <div className="ui-card-foot">
              The result opens on its Summary when the run finishes.
            </div>
          </Card>
        </aside>
      </div>
    </section>
  )
}
