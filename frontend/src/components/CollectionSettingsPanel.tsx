import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { ApiError } from '../types'
import {
  fetchCollectionSettings, saveCollectionSettings,
  type CategoryMasterRow, type CollectionSettings, type CollectionSettingsBody,
} from '../api'
import { Notice } from './ui'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface Draft {
  pattern: string[]
  yearStarts: { year: string; start: string }[]
  master: { region: string; sales_rep: string; order_type: string; category: string; subcategory: string }[]
  branches: { sub: string; code: string }[]
  recipients: string
  segments: string
}

const toDraft = (c: CollectionSettings): Draft => ({
  pattern: c.fiscal_calendar.pattern.map(String),
  yearStarts: Object.entries(c.fiscal_calendar.year_starts).map(([year, start]) => ({ year, start })),
  master: c.category_master.map((r) => ({
    region: r.region ?? '', sales_rep: r.sales_rep, order_type: r.order_type ?? '',
    category: r.category ?? '', subcategory: r.subcategory,
  })),
  branches: Object.entries(c.branch_codes).map(([sub, code]) => ({ sub, code })),
  recipients: c.recipients.join('\n'),
  segments: c.segments.join(', '),
})

const fromDraft = (d: Draft): CollectionSettingsBody => ({
  fiscal_calendar: {
    pattern: d.pattern.map((p) => Number(p)),
    year_starts: Object.fromEntries(d.yearStarts.filter((y) => y.year.trim() && y.start)
      .map((y) => [y.year.trim(), y.start])),
  },
  category_master: d.master.filter((r) => r.sales_rep.trim()).map((r): CategoryMasterRow => ({
    region: r.region.trim() || null, sales_rep: r.sales_rep.trim(),
    order_type: r.order_type.trim() || null, category: r.category.trim() || null,
    subcategory: r.subcategory.trim(),
  })),
  branch_codes: Object.fromEntries(d.branches.filter((b) => b.sub.trim() && b.code.trim())
    .map((b) => [b.sub.trim(), b.code.trim()])),
  recipients: d.recipients.split(/[\n,;]/).map((r) => r.trim()).filter(Boolean),
  segments: d.segments.split(',').map((s) => s.trim()).filter(Boolean),
})

/**
 * Daily Collection export settings: which segments the sheet covers, who
 * receives it, the 4-4-5 fiscal calendar, and the category master that
 * turns an AR invoice's sales rep into Category / BRANCH. The export
 * reads them live — no reconcile needed.
 */
export function CollectionSettingsPanel({ customerId }: { customerId: string }) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [defaults, setDefaults] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  const [resetPending, setResetPending] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apply = (c: CollectionSettings) => {
    setDraft(toDraft(c))
    setDefaults(c.defaults)
    setDirty(false)
    setResetPending(false)
  }

  useEffect(() => {
    setDraft(null)
    setError(null)
    setSaved(false)
    fetchCollectionSettings(customerId).then(apply).catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  if (!draft) {
    return error
      ? <Notice tone="error">Could not load settings: {error}</Notice>
      : <div className="dt-loading"><span className="quill" /> Loading…</div>
  }

  const edit = (patch: Partial<Draft>) => {
    setDraft({ ...draft, ...patch })
    setDirty(true)
    setResetPending(false)
    setSaved(false)
  }
  const builtIn = (section: string) =>
    defaults.includes(section) ? <span className="chip-note"> built-in</span> : null
  const weeks = draft.pattern.reduce((a, p) => a + (Number(p) || 0), 0)

  const onSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const body: CollectionSettingsBody = resetPending
        ? { fiscal_calendar: null, category_master: null, branch_codes: null, recipients: null, segments: null }
        : fromDraft(draft)
      apply(await saveCollectionSettings(customerId, body))
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
        <h3 className="ledger-h">Scope and recipients</h3>
        <div className="collection-grid">
          <label className="ui-field">
            <span>Segments{builtIn('segments')}</span>
            <input value={draft.segments} disabled={saving} placeholder="TSG"
                   onChange={(e) => edit({ segments: e.target.value.toUpperCase() })} />
            <small className="hint">Zone segments the sheet covers, comma-separated</small>
          </label>
          <label className="ui-field">
            <span>Recipients</span>
            <textarea rows={3} value={draft.recipients} disabled={saving} placeholder="one email per line"
                      onChange={(e) => edit({ recipients: e.target.value })} />
          </label>
        </div>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Fiscal calendar{builtIn('fiscal_calendar')}</h3>
        <p className="hint">Weeks per month, Monday to Sunday; the year ends on the last Sunday of December.</p>
        <div className="collection-pattern">
          {MONTHS.map((m, i) => (
            <label key={m} className="ui-field">
              <span>{m}</span>
              <input type="number" min={1} max={6} value={draft.pattern[i]} disabled={saving}
                     onChange={(e) => edit({ pattern: draft.pattern.map((p, j) => (j === i ? e.target.value : p)) })} />
            </label>
          ))}
          <span className={`chip-note${weeks === 52 ? '' : ' is-warn'}`}>{weeks} weeks</span>
        </div>
        <table className="data zone-table">
          <thead><tr><th>Fiscal year</th><th>Starts (Monday)</th><th /></tr></thead>
          <tbody>
            {draft.yearStarts.map((y, i) => (
              <tr key={i}>
                <td><input className="zone-short" value={y.year} disabled={saving} placeholder="2027"
                           onChange={(e) => edit({ yearStarts: draft.yearStarts.map((r, j) => (j === i ? { ...r, year: e.target.value } : r)) })} /></td>
                <td><input type="date" value={y.start} disabled={saving}
                           onChange={(e) => edit({ yearStarts: draft.yearStarts.map((r, j) => (j === i ? { ...r, start: e.target.value } : r)) })} /></td>
                <td>
                  <button type="button" className="btn-reject btn-ic" title="Remove" disabled={saving}
                          onClick={() => edit({ yearStarts: draft.yearStarts.filter((_, j) => j !== i) })}>
                    <X size={13} strokeWidth={2} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button type="button" className="ui-btn is-sm btn-ic" disabled={saving}
                onClick={() => edit({ yearStarts: [...draft.yearStarts, { year: '', start: '' }] })}>
          <Plus size={13} strokeWidth={2} /> Override a year start
        </button>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Category master{builtIn('category_master')}</h3>
        <p className="hint">AR sales rep (and order type, when a rep repeats) → Category and Subcategory.</p>
        <table className="data zone-table">
          <thead>
            <tr><th>Region</th><th>Sales rep</th><th>Order type</th><th>Category</th><th>Subcategory</th><th /></tr>
          </thead>
          <tbody>
            {draft.master.map((r, i) => {
              const set = (patch: Partial<Draft['master'][number]>) =>
                edit({ master: draft.master.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
              return (
                <tr key={i}>
                  <td><input className="zone-short" value={r.region} disabled={saving}
                             onChange={(e) => set({ region: e.target.value })} /></td>
                  <td><input value={r.sales_rep} disabled={saving}
                             onChange={(e) => set({ sales_rep: e.target.value })} /></td>
                  <td><input value={r.order_type} disabled={saving}
                             onChange={(e) => set({ order_type: e.target.value })} /></td>
                  <td><input className="zone-short" value={r.category} disabled={saving}
                             onChange={(e) => set({ category: e.target.value })} /></td>
                  <td><input className="zone-short" value={r.subcategory} disabled={saving}
                             onChange={(e) => set({ subcategory: e.target.value })} /></td>
                  <td>
                    <button type="button" className="btn-reject btn-ic" title="Remove row" disabled={saving}
                            onClick={() => edit({ master: draft.master.filter((_, j) => j !== i) })}>
                      <X size={13} strokeWidth={2} />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <button type="button" className="ui-btn is-sm btn-ic" disabled={saving}
                onClick={() => edit({ master: [...draft.master, { region: '', sales_rep: '', order_type: '', category: '', subcategory: '' }] })}>
          <Plus size={13} strokeWidth={2} /> Add row
        </button>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Branch codes{builtIn('branch_codes')}</h3>
        <p className="hint">Subcategory → the BRANCH code the sheet shows.</p>
        <table className="data zone-table">
          <thead><tr><th>Subcategory</th><th>Branch</th><th /></tr></thead>
          <tbody>
            {draft.branches.map((b, i) => (
              <tr key={i}>
                <td><input value={b.sub} disabled={saving}
                           onChange={(e) => edit({ branches: draft.branches.map((x, j) => (j === i ? { ...x, sub: e.target.value } : x)) })} /></td>
                <td><input className="zone-short" value={b.code} disabled={saving}
                           onChange={(e) => edit({ branches: draft.branches.map((x, j) => (j === i ? { ...x, code: e.target.value.toUpperCase() } : x)) })} /></td>
                <td>
                  <button type="button" className="btn-reject btn-ic" title="Remove" disabled={saving}
                          onClick={() => edit({ branches: draft.branches.filter((_, j) => j !== i) })}>
                    <X size={13} strokeWidth={2} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button type="button" className="ui-btn is-sm btn-ic" disabled={saving}
                onClick={() => edit({ branches: [...draft.branches, { sub: '', code: '' }] })}>
          <Plus size={13} strokeWidth={2} /> Add branch code
        </button>
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      <div className={`config-savebar${dirty && !saved ? ' is-dirty' : ''}`}>
        <span className="config-savebar-state">
          {saved ? 'Saved — the Daily collection export uses these settings'
            : resetPending ? 'Will reset to the built-in settings'
            : dirty ? 'Unsaved changes' : 'No changes'}
        </span>
        <button type="button" className="ui-btn" disabled={saving || (defaults.length === 5 && !dirty)}
                onClick={() => { setResetPending(true); setDirty(true); setSaved(false) }}>
          Reset to defaults
        </button>
        <button type="button" className="ui-btn ui-btn-primary" disabled={!dirty || saving} onClick={onSave}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
      </div>
    </div>
  )
}
