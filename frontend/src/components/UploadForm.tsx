import { useEffect, useState } from 'react'
import type { Defaults, TunableOptions } from '../types'
import { fetchDefaults, type UploadFiles } from '../api'
import { AdvancedPanel } from './AdvancedPanel'

interface Props {
  running: boolean
  options: TunableOptions
  onOptionsChange: (o: TunableOptions) => void
  onRun: (files: UploadFiles) => void
}

interface SlotSpec {
  field: keyof UploadFiles
  label: string
  accept: string
  required: boolean
  hint: string
}

const SLOTS: SlotSpec[] = [
  { field: 'statement', label: 'HSBC statement', accept: '.pdf', required: true, hint: 'daily statement PDF' },
  { field: 'bills', label: 'IREPS Bill Status', accept: '.xlsx,.xlsm', required: true, hint: 'View Bills Status export' },
  { field: 'rnote', label: 'RNOTE report', accept: '.xlsx,.xlsm', required: false, hint: 'receipt-note lineage' },
  { field: 'crn', label: 'CRN report', accept: '.xlsx,.xlsm', required: false, hint: 'challan lineage' },
]

const NO_FILES: UploadFiles = { statement: null, bills: null, rnote: null, crn: null }
const NO_DEFAULTS: Defaults = { statement: null, bills: null, rnote: null, crn: null }

export function UploadForm({ running, options, onOptionsChange, onRun }: Props) {
  const [files, setFiles] = useState<UploadFiles>(NO_FILES)
  const [defaults, setDefaults] = useState<Defaults>(NO_DEFAULTS)

  useEffect(() => {
    fetchDefaults().then(setDefaults).catch(() => setDefaults(NO_DEFAULTS))
  }, [])

  const pick = (field: keyof UploadFiles) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null
    setFiles((prev) => ({ ...prev, [field]: f }))
  }

  const revert = (field: keyof UploadFiles) => (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setFiles((prev) => ({ ...prev, [field]: null }))
  }

  const ready =
    Boolean(files.statement || defaults.statement) &&
    Boolean(files.bills || defaults.bills) &&
    !running

  return (
    <section className="intake">
      <h2>Source documents</h2>
      <p className="hint">
        Pre-filled with the sample documents in this repo — click any slot to replace one with your
        own file. Both sides are sources of truth: the bank statement and the IREPS export are
        reconciled in both directions. RNOTE / CRN attach the PO-to-payment document trail.
      </p>
      <div className="file-grid">
        {SLOTS.map((s) => {
          const own = files[s.field]
          const dflt = defaults[s.field]
          const filled = Boolean(own || dflt)
          return (
            <label key={s.field} className={`file-slot${filled ? ' filled' : ''}`}>
              <span className="slot-label">
                {s.label}
                {s.required ? <span className="req">required</span> : <span className="opt">optional</span>}
              </span>
              {own ? (
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
              <input type="file" accept={s.accept} onChange={pick(s.field)} disabled={running} />
            </label>
          )
        })}
      </div>

      <AdvancedPanel options={options} onChange={onOptionsChange} />

      <div className="run-row">
        <button className="btn-run" disabled={!ready} onClick={() => onRun(files)}>
          Run reconciliation
        </button>
        {running && (
          <span className="running-note">
            <span className="quill" /> parsing, scoring and assigning…
          </span>
        )}
      </div>
    </section>
  )
}
