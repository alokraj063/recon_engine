import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight, CheckCircle2, FileText, History, Plus, Trash2, Upload, UserPlus, X,
} from 'lucide-react'
import type { AdapterOption, AdapterRegistry, CustomerInfo, IngestResponse } from '../types'
import { ApiError } from '../types'
import {
  createCustomer, fetchAdapters, fetchCustomerConfig, ingestFiles,
  saveCustomerSources, type UploadFiles,
} from '../api'
import { inr } from '../format'
import { ErrorBanner } from './ErrorBanner'
import { IngestionsView } from './IngestionsView'
import { IngestStatsSummary, fileOutcomeLabel } from './IngestStatsSummary'
import { Card, CustomerSelect, Notice, PageHeader, TextLink, ToolSep } from './ui'

interface Props {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onCustomersChanged: () => void
  onIngested: (r: IngestResponse) => void
  /** the success card offers the next step */
  onGoToReconcile?: () => void
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

/** One document slot's file state: a drop zone with a "browse" button
 *  (hidden input, multiple). A slot may hold SEVERAL files — they are
 *  ingested in the order attached — and is ingested only when it holds at
 *  least one; nothing is ever substituted, so an empty slot is simply not
 *  part of the ingestion. */
function SlotFileArea({ on, running, files, accept, onAdd, onRemove }: {
  on: boolean
  running: boolean
  files: File[]
  accept?: string
  onAdd: (fs: File[]) => void
  onRemove: (index: number) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const disabled = running || !on
  const browse = () => inputRef.current?.click()

  return (
    <div
      className={`up-drop${dragging && !disabled ? ' is-drag' : ''}${files.length ? ' has-files' : ''}${!on ? ' is-off' : ''}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        if (disabled) return
        const fs = Array.from(e.dataTransfer.files ?? [])
        if (fs.length) onAdd(fs)
      }}
    >
      {!on ? (
        <span className="up-drop-note">Skipped — tick the box to include this document</span>
      ) : files.length === 0 ? (
        <span className="up-drop-note">
          <Upload size={15} strokeWidth={1.75} />
          Drop files here or{' '}
          <button type="button" className="ui-link" disabled={disabled} onClick={browse}>browse</button>
          {accept && <span className="up-drop-kinds">{accept.replace(/,/g, ' ')}</span>}
        </span>
      ) : (
        <ul className="up-files">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              <FileText size={14} strokeWidth={1.75} />
              <span className="up-file-name" title={f.name}>{f.name}</span>
              <span className="up-file-size">{fileSize(f.size)}</span>
              <button type="button" className="up-file-x" disabled={running}
                      title="Remove this file" aria-label={`Remove ${f.name}`}
                      onClick={() => onRemove(i)}>
                <X size={13} strokeWidth={2} />
              </button>
            </li>
          ))}
          <li className="up-files-add">
            <button type="button" className="ui-link" disabled={disabled} onClick={browse}>
              <Plus size={13} strokeWidth={2} /> Add another file
            </button>
          </li>
        </ul>
      )}
      <input ref={inputRef} type="file" accept={accept} disabled={disabled} multiple hidden
             onChange={(e) => {
               const fs = Array.from(e.target.files ?? [])
               if (fs.length) onAdd(fs)
               e.target.value = ''   // allow re-picking the same file later
             }} />
    </div>
  )
}

const fileSize = (b: number) =>
  b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} KB` : `${b} B`

/** File-picker accept attribute from the adapter's declared file_kinds;
 *  undefined (accept anything) when the adapter declares none. */
const acceptOf = (o?: AdapterOption): string | undefined =>
  o && o.file_kinds?.length ? o.file_kinds.join(',') : undefined

const NO_FILES: UploadFiles = { statement: [], bills: [], rnote: [], crn: [] }
const ALL_ON: Record<string, boolean> = { statement: true, bills: true, rnote: true, crn: true }

/** "IREPS bill status" under system IREPS -> "Bill status". */
function docLabel(label: string, system: string): string {
  const stripped = label.startsWith(system) ? label.slice(system.length).trim() : label
  return stripped ? stripped[0].toUpperCase() + stripped.slice(1) : label
}

export function IngestForm({
  customers, customerId, onCustomerChange, onCustomersChanged, onIngested, onGoToReconcile,
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
  const [extraFiles, setExtraFiles] = useState<Record<string, File[]>>({})
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

  const addFiles = (field: keyof UploadFiles) => (fs: File[]) =>
    setFiles((prev) => ({ ...prev, [field]: [...prev[field], ...fs] }))
  const removeFile = (field: keyof UploadFiles) => (i: number) =>
    setFiles((prev) => ({ ...prev, [field]: prev[field].filter((_, j) => j !== i) }))

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
  const outgoingExtras: Record<string, File[]> = Object.fromEntries(
    extraSlots.filter(extraEnabled).map((st) => [st, extraFiles[st] ?? []]))
  const anyInput = Object.values(outgoing).some((fs) => fs.length > 0)
    || Object.values(outgoingExtras).some((fs) => fs.length > 0)

  const onIngest = async () => {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const res = await ingestFiles(outgoing, customerId, outgoingExtras)
      setResult(res)
      // the "Ingested" panel now lists these files — clear the slots so
      // the chips don't repeat it (and a second click can't re-post the
      // same bytes); include toggles are kept. A failure keeps the
      // selection so the user can fix and resubmit.
      setFiles(NO_FILES)
      setExtraFiles({})
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
      setExtraFiles((prev) => { const next = { ...prev }; delete next[slot]; return next })
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
    <SlotFileArea on={on} running={running} files={files[s.field]}
                  accept={accept} onAdd={addFiles(s.field)} onRemove={removeFile(s.field)} />
  )

  const toggle = (key: string, on: boolean, name: string) => (
    <label className="up-slot-name">
      <input type="checkbox" checked={on} disabled={running}
             title={on ? 'skip this document' : 'include this document'}
             onChange={(e) => setEnabled((prev) => ({ ...prev, [key]: e.target.checked }))} />
      {name}
    </label>
  )

  const bankOn = enabled[BANK_SLOT.field]
  const bankFilled = bankOn && files.statement.length > 0
  // extensions follow the SELECTED adapter (fall back to the first option
  // before the customer's saved choice loads)
  const bankAccept = acceptOf(
    bankOptions.find((o) => o.key === sources.bank_statement) ?? bankOptions[0])

  const customerName = customers.find((c) => c.key === customerId)?.name ?? customerId

  // the "Ready to ingest" checklist: every slot on the page, in order,
  // with what will actually be posted for it
  const plan = [
    { key: BANK_SLOT.field as string, name: 'Bank statement', on: bankOn, n: files.statement.length },
    ...erpDocs.map(({ slot, opt }) => ({
      key: slot.field as string, name: docLabel(opt.label, activeSystem),
      on: enabled[slot.field], n: files[slot.field].length,
    })),
    ...extraSlots.map((st) => ({
      key: st, name: st.replace(/^lineage_/, ''), on: extraEnabled(st), n: (extraFiles[st] ?? []).length,
    })),
  ]
  const fileCount = plan.reduce((a, p) => a + (p.on ? p.n : 0), 0)
  const checks = result
    ? (result.selfchecks?.length
        ? result.selfchecks
        : result.selfcheck ? [{ ...result.selfcheck, original_name: null }] : [])
    : []

  return (
    <section className="ui-page ingest-page">
      <PageHeader title="Ingest documents"
                  context={<>Load source files into the gold layer for {customerName}</>}>
        <CustomerSelect customers={customers} value={customerId} onChange={onCustomerChange} />
        {customers.length > 1 && <ToolSep />}
        <button type="button" className={`ui-btn${showHistory ? ' is-on' : ''}`}
                aria-pressed={showHistory} onClick={() => setShowHistory((v) => !v)}>
          <History size={15} strokeWidth={1.75} /> All ingestions
        </button>
        <button type="button" className={`ui-btn${creating ? ' is-on' : ''}`}
                aria-pressed={creating} onClick={() => setCreating((v) => !v)}>
          <UserPlus size={15} strokeWidth={1.75} /> New customer
        </button>
      </PageHeader>

      {creating && (
        <Card title="New customer" sub="Starts with the default sources and matching rules — adjust them afterwards">
          <div className="ui-card-body new-customer">
            <label className="ui-field">
              <span>Key</span>
              <input placeholder="a-z 0-9 - _" value={newKey} autoFocus
                     onChange={(e) => setNewKey(e.target.value)} />
            </label>
            <label className="ui-field">
              <span>Display name</span>
              <input placeholder="Acme Corp" value={newName}
                     onChange={(e) => setNewName(e.target.value)} />
            </label>
            <div className="new-customer-actions">
              <button type="button" className="ui-btn" onClick={() => setCreating(false)}>Cancel</button>
              <button type="button" className="ui-btn ui-btn-primary"
                      disabled={!newKey.trim() || !newName.trim()} onClick={onCreateCustomer}>
                Create customer
              </button>
            </div>
          </div>
        </Card>
      )}

      {showHistory && <IngestionsView customerId={customerId} refreshKey={historyEpoch} />}

      {error && <ErrorBanner error={error} />}

      {result && (
        <Card title={<><CheckCircle2 size={16} strokeWidth={2} className="ok-ic" /> Ingested</>}
              sub={`${result.files.length} ${result.files.length === 1 ? 'file' : 'files'} processed`}
              action={onGoToReconcile && (
                <button type="button" className="ui-btn ui-btn-primary is-sm" onClick={onGoToReconcile}>
                  Reconcile now <ArrowRight size={14} strokeWidth={2} />
                </button>
              )}
              ruled>
          <div className="ui-card-body ingest-result-body">
            <ul className="up-result-files">
              {result.files.map((f, i) => (
                <li key={`${f.bronze_file_id}-${i}`}>
                  <FileText size={14} strokeWidth={1.75} />
                  <span className="up-file-name">{f.original_name}</span>
                  <span className={`ui-pill${f.outcome === 'registered' ? ' tone-ok' : ''}`}>
                    {fileOutcomeLabel(f.outcome)}
                  </span>
                </li>
              ))}
            </ul>
            <IngestStatsSummary stats={result.stats} />
            {result.stats.rows_inserted === 0 && (result.stats.rows_reported ?? 0) > 0 && (
              <Notice>
                Nothing new was added — everything in this upload was already in gold
                ({result.stats.bills_updated} updated in place, {result.stats.rows_reused} duplicates
                left as they were). The rows are still browsable as this ingestion: pick it in the
                Data pages' “Ingestion” filter.
              </Notice>
            )}
            {checks.map((c, i) => (
              <Notice key={i} tone={c.passed === false ? 'warn' : 'ok'}>
                {c.passed === false ? 'Parse did not verify' : 'Parse verified'}
                {c.original_name ? ` — ${c.original_name}` : ''}: states {c.stated_count} credits
                / {inr(c.stated_total)}, parsed {c.parsed_count} / {inr(c.parsed_total)}
              </Notice>
            ))}
          </div>
        </Card>
      )}

      <div className="ui-grid-75 ingest-grid">
        <div className="ui-stack">
          <Card title={<><span className="step">1</span> Bank statement</>}
                action={
                  <label className="ui-field is-inline">
                    <span>Format</span>
                    <select value={sources.bank_statement ?? ''} disabled={running || !bankOn}
                            onChange={(e) => onBankFormat(e.target.value)}>
                      {bankOptions.map((o) => (
                        <option key={o.key} value={o.key}>{o.label}</option>
                      ))}
                    </select>
                    {saved === 'bank' && <span className="ui-pill tone-ok">saved</span>}
                  </label>
                }>
            <div className={`up-slot${bankOn ? '' : ' is-off'}${bankFilled ? ' is-filled' : ''}`}>
              {toggle(BANK_SLOT.field, bankOn, 'Statement')}
              {fileArea(BANK_SLOT, bankOn, bankAccept)}
            </div>
          </Card>

          <Card title={<><span className="step">2</span> ERP documents</>}
                sub="The documents below follow the chosen ERP"
                action={
                  <label className="ui-field is-inline">
                    <span>ERP</span>
                    <select value={activeSystem} disabled={running}
                            onChange={(e) => onErpSystem(e.target.value)}>
                      {erpSystems.map((sys) => (
                        <option key={sys} value={sys}>{sys}</option>
                      ))}
                    </select>
                    {saved === 'erp' && <span className="ui-pill tone-ok">saved</span>}
                  </label>
                }>
            {erpDocs.map(({ slot, opt }) => {
              const on = enabled[slot.field]
              const filled = on && files[slot.field].length > 0
              return (
                <div key={slot.field}
                     className={`up-slot${on ? '' : ' is-off'}${filled ? ' is-filled' : ''}`}>
                  {toggle(slot.field, on, docLabel(opt.label, activeSystem))}
                  {fileArea(slot, on, acceptOf(opt))}
                </div>
              )
            })}
          </Card>

          {(extraSlots.length > 0 || addingSlot || lineageAdapters.length > 0) && (
            <Card title={<><span className="step">3</span> Additional lineage documents</>}
                  sub="Optional — extra upstream document kinds that join the same document trail"
                  action={!addingSlot && (
                    <button type="button" className="ui-btn is-sm"
                            disabled={running || !lineageAdapters.length}
                            onClick={() => setAddingSlot(true)}>
                      <Plus size={14} strokeWidth={2} /> Add lineage source
                    </button>
                  )}>
              {extraSlots.map((st) => {
                const on = extraEnabled(st)
                const opt = lineageAdapters.find((o) => o.key === sources[st])
                const own = extraFiles[st] ?? []
                return (
                  <div key={st} className={`up-slot${on ? '' : ' is-off'}${on && own.length ? ' is-filled' : ''}`}>
                    <div className="up-slot-side">
                      {toggle(st, on, st.replace(/^lineage_/, ''))}
                      <label className="ui-field is-inline">
                        <span>Format</span>
                        <select value={sources[st] ?? ''} disabled={running || !on}
                                onChange={(e) => onExtraAdapter(st, e.target.value)}>
                          {lineageAdapters.map((o) => (
                            <option key={o.key} value={o.key}>{o.label}</option>
                          ))}
                        </select>
                      </label>
                      <button type="button" className="ui-link up-slot-remove" disabled={running}
                              title="Remove this lineage source" onClick={() => onRemoveSlot(st)}>
                        <Trash2 size={13} strokeWidth={1.75} /> Remove
                      </button>
                    </div>
                    <SlotFileArea on={on} running={running} files={own}
                                  accept={acceptOf(opt)}
                                  onAdd={(fs) =>
                                    setExtraFiles((prev) => ({ ...prev, [st]: [...(prev[st] ?? []), ...fs] }))}
                                  onRemove={(i) =>
                                    setExtraFiles((prev) => ({ ...prev, [st]: (prev[st] ?? []).filter((_, j) => j !== i) }))} />
                  </div>
                )
              })}
              {extraSlots.length === 0 && !addingSlot && (
                <p className="ui-card-note">No additional lineage sources configured.</p>
              )}
              {addingSlot && (
                <div className="up-add-slot">
                  <label className="ui-field">
                    <span>Slot key</span>
                    <input placeholder="e.g. grn" value={newSlotKey} autoFocus
                           onChange={(e) => setNewSlotKey(e.target.value)} />
                  </label>
                  <label className="ui-field">
                    <span>Format</span>
                    <select value={newSlotAdapter || lineageAdapters[0]?.key || ''}
                            onChange={(e) => setNewSlotAdapter(e.target.value)}>
                      {lineageAdapters.map((o) => (
                        <option key={o.key} value={o.key}>{o.label}</option>
                      ))}
                    </select>
                  </label>
                  <div className="new-customer-actions">
                    <button type="button" className="ui-btn" onClick={() => setAddingSlot(false)}>Cancel</button>
                    <button type="button" className="ui-btn ui-btn-primary" disabled={!newSlotKey.trim()}
                            onClick={onAddSlot}>Add source</button>
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>

        {/* ---- what will be sent ---- */}
        <aside className="ingest-side">
          <Card title="Ready to ingest" ruled>
            <ul className="up-plan">
              {plan.map((p) => (
                <li key={p.key} className={!p.on ? 'is-off' : p.n ? 'is-set' : ''}>
                  <span className="up-plan-name">{p.name}</span>
                  <span className="up-plan-state">
                    {!p.on ? 'skipped' : p.n ? `${p.n} ${p.n === 1 ? 'file' : 'files'}` : 'no file'}
                  </span>
                </li>
              ))}
            </ul>
            <div className="ui-card-body up-go">
              <button type="button" className="ui-btn ui-btn-primary up-go-btn"
                      disabled={!anyInput || !registryReady || running} onClick={onIngest}>
                <Upload size={15} strokeWidth={1.75} />
                {running ? 'Ingesting…' : fileCount ? `Ingest ${fileCount} ${fileCount === 1 ? 'file' : 'files'}` : 'Ingest'}
              </button>
              {running && (
                <p className="up-go-note">
                  <span className="quill" /> Hashing, parsing and transforming… {elapsed}s
                  <span className="running-hint"> (typically 5–20s)</span>
                </p>
              )}
              {!running && !anyInput && (
                <p className="up-go-note">Attach at least one file to start.</p>
              )}
            </div>
            <div className="ui-card-foot">
              An ingestion is exactly the files you attach: an empty or unticked slot is skipped,
              never filled in for you. Identical files are recognised and not loaded twice, and
              format choices are saved to the customer.
              {onGoToReconcile && (
                <> <TextLink onClick={onGoToReconcile}>Go to reconcile</TextLink></>
              )}
            </div>
          </Card>
        </aside>
      </div>
    </section>
  )
}
