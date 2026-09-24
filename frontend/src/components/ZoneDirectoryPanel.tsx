import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { ApiError, type ZoneEntry } from '../types'
import { fetchZoneDirectory, saveZoneDirectory } from '../api'
import { Notice } from './ui'

/** an editable row: aliases as the comma-separated text the analyst types */
interface Draft { code: string; name: string; region: string; segment: string; aliases: string }

const toDraft = (z: ZoneEntry): Draft => ({
  code: z.code, name: z.name ?? '', region: z.region ?? '', segment: z.segment,
  aliases: z.aliases.join(', '),
})
const fromDraft = (d: Draft): ZoneEntry => ({
  code: d.code.trim(), name: d.name.trim() || null, region: d.region.trim() || null,
  segment: d.segment.trim(),
  aliases: d.aliases.split(',').map((a) => a.trim()).filter(Boolean),
})

/**
 * The customer's zone directory: zone code -> name, region and segment
 * (TSG / OE …). The Analyst queue's Segment column reads it live, so a
 * save needs no reconcile. Aliases catch the other spellings the data
 * uses (IREPS writes NFR for NEFR).
 */
export function ZoneDirectoryPanel({ customerId }: { customerId: string }) {
  const [rows, setRows] = useState<Draft[] | null>(null)
  const [isDefault, setIsDefault] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [resetPending, setResetPending] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apply = (d: { zones: ZoneEntry[]; is_default: boolean }) => {
    setRows(d.zones.map(toDraft))
    setIsDefault(d.is_default)
    setDirty(false)
    setResetPending(false)
  }

  useEffect(() => {
    setRows(null)
    setError(null)
    setSaved(false)
    fetchZoneDirectory(customerId).then(apply).catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  if (!rows) {
    return error
      ? <Notice tone="error">Could not load zones: {error}</Notice>
      : <div className="dt-loading"><span className="quill" /> Loading…</div>
  }

  const change = (i: number, patch: Partial<Draft>) => {
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
    setDirty(true)
    setResetPending(false)
    setSaved(false)
  }
  const segments = [...new Set(rows.map((r) => r.segment.trim().toUpperCase()).filter(Boolean))].sort()

  const onSave = async () => {
    setSaving(true)
    setError(null)
    try {
      apply(await saveZoneDirectory(customerId, resetPending ? null : rows.map(fromDraft)))
      setSaved(true)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="config-panel">
      <div className="config-section">
        <h3 className="ledger-h">Zones{isDefault && <span className="chip-note"> built-in list</span>}</h3>
        <p className="hint">Segment shows in the Analyst queue. Aliases: other spellings, comma-separated.</p>
        <table className="data zone-table">
          <thead>
            <tr>
              <th>Code</th><th>Name</th><th>Region</th><th>Segment</th><th>Aliases</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><input className="zone-code" value={r.code} disabled={saving}
                           onChange={(e) => change(i, { code: e.target.value.toUpperCase() })} /></td>
                <td><input value={r.name} disabled={saving}
                           onChange={(e) => change(i, { name: e.target.value })} /></td>
                <td><input className="zone-short" value={r.region} disabled={saving}
                           onChange={(e) => change(i, { region: e.target.value.toUpperCase() })} /></td>
                <td><input className="zone-short" value={r.segment} list="zone-segments" disabled={saving}
                           onChange={(e) => change(i, { segment: e.target.value.toUpperCase() })} /></td>
                <td><input value={r.aliases} disabled={saving}
                           onChange={(e) => change(i, { aliases: e.target.value.toUpperCase() })} /></td>
                <td>
                  <button type="button" className="btn-reject btn-ic" title="Remove zone" disabled={saving}
                          onClick={() => { setRows(rows.filter((_, j) => j !== i)); setDirty(true); setSaved(false) }}>
                    <X size={13} strokeWidth={2} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <datalist id="zone-segments">
          {segments.map((s) => <option key={s} value={s} />)}
        </datalist>
        <button type="button" className="ui-btn is-sm btn-ic" disabled={saving}
                onClick={() => {
                  setRows([...rows, { code: '', name: '', region: '', segment: '', aliases: '' }])
                  setDirty(true)
                  setSaved(false)
                }}>
          <Plus size={13} strokeWidth={2} /> Add zone
        </button>
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      <div className={`config-savebar${dirty && !saved ? ' is-dirty' : ''}`}>
        <span className="config-savebar-state">
          {saved ? 'Saved — the Analyst queue uses these zones'
            : resetPending ? 'Will reset to the built-in list'
            : dirty ? 'Unsaved changes' : 'No changes'}
        </span>
        <button type="button" className="ui-btn" disabled={saving || (isDefault && !dirty)}
                onClick={() => { setResetPending(true); setDirty(true); setSaved(false) }}>
          Reset to defaults
        </button>
        <button type="button" className="ui-btn ui-btn-primary" disabled={!dirty || saving} onClick={onSave}>
          {saving ? 'Saving…' : 'Save zones'}
        </button>
      </div>
    </div>
  )
}
