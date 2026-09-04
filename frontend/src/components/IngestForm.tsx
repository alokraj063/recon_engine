import { useEffect, useRef, useState } from 'react'
import { History, Upload, UserPlus, X } from 'lucide-react'
import type { AdapterOption, AdapterRegistry, CustomerInfo, IngestResponse } from '../types'
import { ApiError } from '../types'
import {
  createCustomer, fetchAdapters, fetchCustomerConfig, ingestFiles,
  saveCustomerSources, type UploadFiles,
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
}

const BANK_SLOT: SlotSpec = { field: 'statement', sourceType: 'bank_statement' }

// ERP document slots, in ingest order; which of them RENDER depends on
// the chosen ERP system (whichever source_types it has adapters for)
const ERP_SLOTS: SlotSpec[] = [
  { field: 'bills', sourceType: 'bill_status' },
  { field: 'rnote', sourceType: 'lineage_rnote' },
  { field: 'crn', sourceType: 'lineage_crn' },
]

/** One document slot's file state: an explicit Upload button (hidden
 *  input) and drag & drop onto the area. A slot is ingested only when it
 *  holds a file — nothing is ever substituted for one, so an empty slot
 *  is simply not part of the ingestion. */
function SlotFileArea({ on, running, file, accept, onFile }: {
  on: boolean
  running: boolean
  file: File | null
  accept?: string
  onFile: (f: File | null) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const disabled = running || !on

  const btn = (label: string, icon: React.ReactNode, onClick: () => void) => (
    <button className="slot-revert" disabled={running} onClick={onClick}>
      {icon} {label}
    </button>
  )
  const uploadBtn = btn('Upload file', <Upload size={13} strokeWidth={1.75} />,
                        () => inputRef.current?.click())

  return (
    <div
      className={`slot-file-area${dragging && !disabled ? ' dragover' : ''}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        if (disabled) return
        const f = e.dataTransfer.files?.[0]
        if (f) onFile(f)
      }}
    >
      {!on ? (
        <span className="slot-file slot-empty">skipped</span>
      ) : file ? (
        <>
          <span className="slot-badge upload">Your upload</span>
          <span className="slot-file" title={file.name}>{file.name}</span>
          {btn('Remove', <X size={13} strokeWidth={1.75} />, () => onFile(null))}
        </>
      ) : (
        <>
          <span className="slot-file slot-empty">no file selected — drag &amp; drop or</span>
          {uploadBtn}
        </>
      )}
      <input ref={inputRef} type="file" accept={accept} disabled={disabled}
             onChange={(e) => {
               const f = e.target.files?.[0] ?? null
               if (f) onFile(f)
               e.target.value = ''   // allow re-picking the same file later
             }} />
    </div>
  )
}

/** File-picker accept attribute from the adapter's declared file_kinds;
 *  undefined (accept anything) when the adapter declares none. */
const acceptOf = (o?: AdapterOption): string | undefined =>
  o && o.file_kinds?.length ? o.file_kinds.join(',') : undefined

const NO_FILES: UploadFiles = { statement: null, bills: null, rnote: null, crn: null }
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
  // extra lineage slots (beyond the ERP's own documents): uploads keyed
  // by slot source_type, plus the add-slot mini-form
  const [extraFiles, setExtraFiles] = useState<Record<string, File | null>>({})
  const [addingSlot, setAddingSlot] = useState(false)
  const [newSlotKey, setNewSlotKey] = useState('')
  const [newSlotAdapter, setNewSlotAdapter] = useState('')

  useEffect(() => {
    fetchAdapters().then(setAdapters).catch(() => setAdapters({}))
  }, [])

  useEffect(() => {
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

  // customer's extra lineage slots (0..N; any lineage-role adapter fits)
  const FIXED_SOURCE_TYPES = new Set(
    [BANK_SLOT, ...ERP_SLOTS].map((s) => s.sourceType))
  const extraSlots = Object.keys(sources)
    .filter((st) => st.startsWith('lineage_') && !FIXED_SOURCE_TYPES.has(st))
    .sort()
  const lineageAdapters = [...new Map(
    Object.values(adapters).flat()
      .filter((o) => o.role === 'lineage')
      .map((o) => [o.key, o]),
  ).values()]

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

  const setFile = (field: keyof UploadFiles) => (f: File | null) =>
    setFiles((prev) => ({ ...prev, [field]: f }))

  const extraEnabled = (st: string) => enabled[st] ?? true
  // what actually gets posted: the files of enabled slots, nothing else.
  // An unticked row's file is held in state (so re-ticking restores it)
  // but never sent.
  const outgoing: UploadFiles = {
    ...NO_FILES,
    ...Object.fromEntries(activeSlots
      .filter((s) => enabled[s.field])
      .map((s) => [s.field, files[s.field]])),
  }
  const outgoingExtras = Object.fromEntries(
    extraSlots.filter(extraEnabled).map((st) => [st, extraFiles[st] ?? null]))
  const anyInput = Object.values(outgoing).some(Boolean)
    || Object.values(outgoingExtras).some(Boolean)

  const onIngest = async () => {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const res = await ingestFiles(outgoing, customerId, outgoingExtras)
      setResult(res)
      setHistoryEpoch((n) => n + 1)
      onIngested(res)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    } finally {
      setRunning(false)
    }
  }

  const fail = (e: unknown) =>
    setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))

  const onAddSlot = async () => {
    const key = newSlotKey.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
    const adapter = newSlotAdapter || lineageAdapters[0]?.key
    if (!key || !adapter) return
    try {
      const res = await saveCustomerSources(customerId, { [`lineage_${key}`]: adapter })
      setSources(res.sources)
      setAddingSlot(false)
      setNewSlotKey('')
      setNewSlotAdapter('')
    } catch (e) { fail(e) }
  }

  const onRemoveSlot = async (slot: string) => {
    try {
      const res = await saveCustomerSources(customerId, { [slot]: null })
      setSources(res.sources)
      setExtraFiles((prev) => ({ ...prev, [slot]: null }))
    } catch (e) { fail(e) }
  }

  const onExtraAdapter = async (slot: string, adapterKey: string) => {
    setSources((prev) => ({ ...prev, [slot]: adapterKey }))
    try {
      await saveCustomerSources(customerId, { [slot]: adapterKey })
      flashSaved('erp')
    } catch (e) { fail(e) }
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

  const fileArea = (s: SlotSpec, on: boolean, accept?: string) => (
    <SlotFileArea on={on} running={running} file={files[s.field]}
                  accept={accept} onFile={setFile(s.field)} />
  )

  const toggle = (field: keyof UploadFiles, on: boolean) => (
    <input type="checkbox" className="slot-toggle" checked={on}
           title={on ? 'skip this document' : 'include this document'}
           onChange={(e) =>
             setEnabled((prev) => ({ ...prev, [field]: e.target.checked }))}
           disabled={running} />
  )

  const bankOn = enabled[BANK_SLOT.field]
  const bankFilled = bankOn && Boolean(files.statement)
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
        transform into the gold layer every reconciliation runs on. An ingestion is
        exactly the files you attach — a slot you leave empty (or untick) is skipped,
        never filled in for you. Format choices persist to the customer's configuration.
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
            const filled = on && Boolean(files[slot.field])
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

      {(extraSlots.length > 0 || addingSlot || lineageAdapters.length > 0) && (
        <div className="ingest-section">
          <h3 className="ingest-section-h">Additional lineage documents</h3>
          <div className="slot-stack">
            {extraSlots.map((st) => {
              const on = extraEnabled(st)
              const opt = lineageAdapters.find((o) => o.key === sources[st])
              const own = extraFiles[st] ?? null
              return (
                <div key={st}
                     className={`slot-row${on ? '' : ' slot-off'}${on && own ? ' filled' : ''}`}>
                  <input type="checkbox" className="slot-toggle" checked={on}
                         title={on ? 'skip this document' : 'include this document'}
                         disabled={running}
                         onChange={(e) =>
                           setEnabled((prev) => ({ ...prev, [st]: e.target.checked }))} />
                  <span className="slot-doc">{st.replace(/^lineage_/, '')}</span>
                  <label className="slot-format">
                    <span className="slot-format-label">File format</span>
                    <select value={sources[st] ?? ''} disabled={running || !on}
                            onChange={(e) => onExtraAdapter(st, e.target.value)}>
                      {lineageAdapters.map((o) => (
                        <option key={o.key} value={o.key}>{o.label}</option>
                      ))}
                    </select>
                  </label>
                  <SlotFileArea on={on} running={running} file={own}
                                accept={acceptOf(opt)}
                                onFile={(f) =>
                                  setExtraFiles((prev) => ({ ...prev, [st]: f }))} />
                  <button className="btn-reject" title="remove this slot"
                          disabled={running} onClick={() => onRemoveSlot(st)}>
                    remove
                  </button>
                </div>
              )
            })}
          </div>
          {!addingSlot ? (
            <button className="btn-refresh" disabled={running || !lineageAdapters.length}
                    onClick={() => setAddingSlot(true)}>
              + add lineage source
            </button>
          ) : (
            <span className="new-customer-form">
              <label className="ctx-field">
                <span className="slot-label">Slot key</span>
                <input placeholder="e.g. grn" value={newSlotKey}
                       onChange={(e) => setNewSlotKey(e.target.value)} />
              </label>
              <label className="ctx-field">
                <span className="slot-label">File format</span>
                <select value={newSlotAdapter || lineageAdapters[0]?.key || ''}
                        onChange={(e) => setNewSlotAdapter(e.target.value)}>
                  {lineageAdapters.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </label>
              <button className="btn-accept" disabled={!newSlotKey.trim()}
                      onClick={onAddSlot}>Add</button>
              <button className="btn-reject" onClick={() => setAddingSlot(false)}>Cancel</button>
            </span>
          )}
          <p className="explain">
            Extra upstream document kinds beyond the ERP's own reports — each slot
            parses with the chosen lineage adapter and joins into the same document
            trail.
          </p>
        </div>
      )}

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
            {result.stats.rows_reported !== undefined && (
              <span className="chip chip-settled">
                rows reported {result.stats.rows_reported}
              </span>
            )}
            <span className="chip">rows inserted {result.stats.rows_inserted}</span>
            <span className="chip">bills updated {result.stats.bills_updated}</span>
            <span className="chip">rows reused {result.stats.rows_reused}</span>
            <span className="chip">files reused {result.stats.files_reused}</span>
            <span className={`chip${result.stats.conflicts > 0 ? ' chip-attempts' : ''}`}>
              conflicts {result.stats.conflicts}
            </span>
          </div>
          {result.stats.rows_inserted === 0
            && (result.stats.rows_reported ?? 0) > 0 && (
            <p className="frame-note">
              Nothing new to insert — every row this upload carried was
              already in gold, so it updated {result.stats.bills_updated} and
              left {result.stats.rows_reused} unchanged. The rows are still
              browsable as this ingestion: pick it in the Gold data tabs'
              “Ingestion” filter.
            </p>
          )}
          {result.selfcheck && result.selfcheck.passed !== false && (
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
