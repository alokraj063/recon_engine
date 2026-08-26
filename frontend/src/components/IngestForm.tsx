import { useEffect, useState } from 'react'
import { History, UserPlus } from 'lucide-react'
import type { AdapterOption, AdapterRegistry, CustomerInfo, Defaults, IngestResponse } from '../types'
import { ApiError } from '../types'
import {
  createCustomer, fetchAdapters, fetchCustomerConfig, fetchDefaults,
  ingestFiles, saveCustomerSources, type UploadFiles,
} from '../api'
import { inr } from '../format'
import { ErrorBanner } from './ErrorBanner'
import { IngestionsView } from './IngestionsView'

interface Props {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onCustomersChanged: () => void
  onIngested: (r: IngestResponse) => void
}

interface SlotSpec {
  field: keyof UploadFiles
  sourceType: string
  hint: string
}

const BANK_SLOT: SlotSpec = {
  field: 'statement', sourceType: 'bank_statement',
  hint: 'click to select the statement file',
}

// ERP document slots, in ingest order; which of them RENDER depends on
// the chosen ERP system (whichever source_types it has adapters for)
const ERP_SLOTS: SlotSpec[] = [
  { field: 'bills', sourceType: 'bill_status', hint: 'click to select the export' },
  { field: 'rnote', sourceType: 'lineage_rnote', hint: 'click to select the report' },
  { field: 'crn', sourceType: 'lineage_crn', hint: 'click to select the report' },
]

/** File-picker accept attribute from the adapter's declared file_kinds;
 *  undefined (accept anything) when the adapter declares none. */
const acceptOf = (o?: AdapterOption): string | undefined =>
  o && o.file_kinds?.length ? o.file_kinds.join(',') : undefined

const NO_FILES: UploadFiles = { statement: null, bills: null, rnote: null, crn: null }
const NO_DEFAULTS: Defaults = { statement: null, bills: null, rnote: null, crn: null }
const ALL_ON: Record<string, boolean> = { statement: true, bills: true, rnote: true, crn: true }

/** "IREPS bill status" under system IREPS -> "Bill status". */
function docLabel(label: string, system: string): string {
  const stripped = label.startsWith(system) ? label.slice(system.length).trim() : label
  return stripped ? stripped[0].toUpperCase() + stripped.slice(1) : label
}

export function IngestForm({
  customers, customerId, onCustomerChange, onCustomersChanged, onIngested,
}: Props) {
  const [files, setFiles] = useState<UploadFiles>(NO_FILES)
  const [defaults, setDefaults] = useState<Defaults>(NO_DEFAULTS)
  const [enabled, setEnabled] = useState<Record<string, boolean>>(ALL_ON)
  const [adapters, setAdapters] = useState<AdapterRegistry>({})
  const [sources, setSources] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<'bank' | 'erp' | null>(null)
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<ApiError | null>(null)
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newName, setNewName] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const [historyEpoch, setHistoryEpoch] = useState(0)

  useEffect(() => {
    fetchAdapters().then(setAdapters).catch(() => setAdapters({}))
  }, [])

  useEffect(() => {
    // repo-sample prefills exist for the default customer only
    fetchDefaults(customerId).then(setDefaults).catch(() => setDefaults(NO_DEFAULTS))
    fetchCustomerConfig(customerId)
      .then((c) => setSources(c.sources))
      .catch(() => setSources({}))
  }, [customerId])

  useEffect(() => {
    if (!running) return
    setElapsed(0)
    const t = window.setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => window.clearInterval(t)
  }, [running])

  const flashSaved = (which: 'bank' | 'erp') => {
    setSaved(which)
    window.setTimeout(() => setSaved(null), 2000)
  }

  // --- derivations from the registry -----------------------------------
  // until the registry loads, the ERP rows are unknown — hold the ingest
  // button so a click can't send a statement-only slots list by accident
  const registryReady = Object.keys(adapters).length > 0
  const bankOptions = adapters.bank_statement ?? []

  // ERP systems = distinct `system` values among non-bank adapters
  const erpSystems = [...new Set(
    ERP_SLOTS.flatMap((s) => (adapters[s.sourceType] ?? []).map((o) => o.system))
      .filter(Boolean),
  )]
  // active system: whatever the customer's saved bill_status adapter belongs to
  const activeSystem =
    (adapters.bill_status ?? []).find((o) => o.key === sources.bill_status)?.system
    ?? erpSystems[0] ?? ''
  // the documents this system provides: one row per source_type that has
  // an adapter in the system
  const erpDocs = ERP_SLOTS.flatMap((slot) => {
    const opt = (adapters[slot.sourceType] ?? []).find((o) => o.system === activeSystem)
    return opt ? [{ slot, opt }] : []
  })
  const activeSlots: SlotSpec[] = [BANK_SLOT, ...erpDocs.map((d) => d.slot)]

  const onBankFormat = async (key: string) => {
    setSources((prev) => ({ ...prev, bank_statement: key }))
    try {
      await saveCustomerSources(customerId, { bank_statement: key })
      flashSaved('bank')
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    }
  }

  const onErpSystem = async (system: string) => {
    // one choice sets every slot the system provides
    const update: Record<string, string> = {}
    for (const slot of ERP_SLOTS) {
      const opt = (adapters[slot.sourceType] ?? []).find((o) => o.system === system)
      if (opt) update[slot.sourceType] = opt.key
    }
    setSources((prev) => ({ ...prev, ...update }))
    try {
      await saveCustomerSources(customerId, update)
      flashSaved('erp')
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    }
  }

  const pick = (field: keyof UploadFiles) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null
    setFiles((prev) => ({ ...prev, [field]: f }))
  }

  const revert = (field: keyof UploadFiles) => (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setFiles((prev) => ({ ...prev, [field]: null }))
  }

  const slots = activeSlots.filter((s) => enabled[s.field]).map((s) => s.field)
  const anyInput = activeSlots.some(
    (s) => enabled[s.field] && (files[s.field] || defaults[s.field]))

  const onIngest = async () => {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const res = await ingestFiles(files, customerId, slots)
      setResult(res)
      setHistoryEpoch((n) => n + 1)
      onIngested(res)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    } finally {
      setRunning(false)
    }
  }

  const onCreateCustomer = async () => {
    setError(null)
    try {
      const c = await createCustomer(newKey.trim(), newName.trim())
      setCreating(false)
      setNewKey('')
      setNewName('')
      onCustomersChanged()
      onCustomerChange(c.key)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    }
  }

  const fileArea = (s: SlotSpec, on: boolean, accept?: string) => {
    const own = files[s.field]
    const dflt = defaults[s.field]
    return (
      <label className="slot-file-area">
        {!on ? (
          <span className="slot-file slot-empty">skipped</span>
        ) : own ? (
          <>
            <span className="slot-file">{own.name}</span>
            {dflt && (
              <button className="slot-revert" onClick={revert(s.field)} disabled={running}>
                ↺ use repo sample
              </button>
            )}
          </>
        ) : dflt ? (
          <>
            <span className="slot-file">{dflt.name}</span>
            <span className="slot-tag">repo sample — click to replace</span>
          </>
        ) : (
          <span className="slot-file slot-empty">{s.hint}</span>
        )}
        <input type="file" accept={accept} onChange={pick(s.field)}
               disabled={running || !on} />
      </label>
    )
  }

  const toggle = (field: keyof UploadFiles, on: boolean) => (
    <input type="checkbox" className="slot-toggle" checked={on}
           title={on ? 'skip this document' : 'include this document'}
           onChange={(e) =>
             setEnabled((prev) => ({ ...prev, [field]: e.target.checked }))}
           disabled={running} />
  )

  const bankOn = enabled[BANK_SLOT.field]
  const bankFilled = bankOn && Boolean(files.statement || defaults.statement)
  // extensions follow the SELECTED adapter (fall back to the first option
  // before the customer's saved choice loads)
  const bankAccept = acceptOf(
    bankOptions.find((o) => o.key === sources.bank_statement) ?? bankOptions[0])

  return (
    <section className="intake">
      <div className="ingest-head">
        <h2>Ingest source documents</h2>
        {!creating ? (
          <span className="cc-actions">
            <button className={`btn-refresh new-customer-btn btn-ic${showHistory ? ' on' : ''}`}
                    onClick={() => setShowHistory((v) => !v)}>
              <History size={14} strokeWidth={1.75} /> All ingestions {showHistory ? '▴' : '▾'}
            </button>
            <button className="btn-refresh new-customer-btn btn-ic"
                    onClick={() => setCreating(true)}>
              <UserPlus size={14} strokeWidth={1.75} /> new customer
            </button>
          </span>
        ) : (
          <span className="new-customer-form">
            <label className="ctx-field">
              <span className="slot-label">Key</span>
              <input placeholder="a-z 0-9 - _" value={newKey}
                     onChange={(e) => setNewKey(e.target.value)} />
            </label>
            <label className="ctx-field">
              <span className="slot-label">Display name</span>
              <input placeholder="Acme Corp" value={newName}
                     onChange={(e) => setNewName(e.target.value)} />
            </label>
            <button className="btn-accept" disabled={!newKey.trim() || !newName.trim()}
                    onClick={onCreateCustomer}>Create</button>
            <button className="btn-reject" onClick={() => setCreating(false)}>Cancel</button>
          </span>
        )}
      </div>

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
      </div>

      {showHistory && (
        <div className="config-inset">
          <IngestionsView customerId={customerId} refreshKey={historyEpoch} />
        </div>
      )}

      <p className="hint">
        Raw files land in bronze (registered by content hash), parse into silver, and
        transform into the gold layer every reconciliation runs on. Untick a row to skip
        that document — ingest just a statement or just the ERP documents. Format
        choices persist to the customer's configuration.
      </p>

      <div className="ingest-section">
        <h3 className="ingest-section-h">Bank statement</h3>
        <div className={`slot-row${bankOn ? '' : ' slot-off'}${bankFilled ? ' filled' : ''}`}>
          {toggle(BANK_SLOT.field, bankOn)}
          <label className="slot-format">
            <span className="slot-format-label">File format</span>
            <select value={sources.bank_statement ?? ''} disabled={running || !bankOn}
                    onChange={(e) => onBankFormat(e.target.value)}>
              {bankOptions.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
            {saved === 'bank' && <span className="chip chip-settled">saved</span>}
          </label>
          {fileArea(BANK_SLOT, bankOn, bankAccept)}
        </div>
      </div>

      <div className="ingest-section">
        <h3 className="ingest-section-h">ERP documents</h3>
        <div className="slot-erp-format">
          <label className="slot-format">
            <span className="slot-format-label">File format</span>
            <select value={activeSystem} disabled={running}
                    onChange={(e) => onErpSystem(e.target.value)}>
              {erpSystems.map((sys) => (
                <option key={sys} value={sys}>{sys}</option>
              ))}
            </select>
            {saved === 'erp' && <span className="chip chip-settled">saved</span>}
          </label>
          <span className="chip-note">
            documents below follow the chosen ERP
          </span>
        </div>
        <div className="slot-stack">
          {erpDocs.map(({ slot, opt }) => {
            const on = enabled[slot.field]
            const filled = on && Boolean(files[slot.field] || defaults[slot.field])
            return (
              <div key={slot.field}
                   className={`slot-row${on ? '' : ' slot-off'}${filled ? ' filled' : ''}`}>
                {toggle(slot.field, on)}
                <span className="slot-doc">{docLabel(opt.label, activeSystem)}</span>
                {fileArea(slot, on, acceptOf(opt))}
              </div>
            )
          })}
        </div>
      </div>

      <div className="run-row">
        <button className="btn-run" disabled={!anyInput || !registryReady || running}
                onClick={onIngest}>
          Ingest files
        </button>
        {running && (
          <span className="running-note">
            <span className="quill" /> hashing, parsing and transforming… {elapsed}s
            <span className="running-hint"> (typical: 5–20s)</span>
          </span>
        )}
      </div>

      {error && <ErrorBanner error={error} />}

      {result && (
        <div className="ingest-result">
          <h3 className="ledger-h">Ingested</h3>
          <div className="stat-chips">
            {result.files.map((f) => (
              <span key={f.bronze_file_id}
                    className={`chip${f.outcome === 'registered' ? ' chip-settled' : ''}`}>
                {f.original_name} · {f.outcome}
              </span>
            ))}
          </div>
          <div className="stat-chips">
            <span className="chip">rows inserted {result.stats.rows_inserted}</span>
            <span className="chip">bills updated {result.stats.bills_updated}</span>
            <span className="chip">rows reused {result.stats.rows_reused}</span>
            <span className="chip">files reused {result.stats.files_reused}</span>
            <span className={`chip${result.stats.conflicts > 0 ? ' chip-attempts' : ''}`}>
              conflicts {result.stats.conflicts}
            </span>
          </div>
          {result.selfcheck && (
            <p className="selfcheck-line">
              <span className="tick">✓ parse verified</span> — statement states{' '}
              {result.selfcheck.stated_count} credits / {inr(result.selfcheck.stated_total)};
              parsed {result.selfcheck.parsed_count} / {inr(result.selfcheck.parsed_total)}
            </p>
          )}
          <p className="footer-note">
            Gold is updated — browse it under Gold data, or head to Reconcile to run against it.
          </p>
        </div>
      )}
    </section>
  )
}
