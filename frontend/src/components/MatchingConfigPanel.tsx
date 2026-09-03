import { useEffect, useState } from 'react'
import type {
  CopyText, CustomerConfig, CustomerRules, ExactSignal, FieldMap, GoldSchema,
} from '../types'
import { ApiError } from '../types'
import { fetchCustomerConfig, fetchGoldSchema, saveCustomerConfig } from '../api'
import { ErrorBanner } from './ErrorBanner'

interface Props {
  customerId: string
}

/** Client-side mirror of the backend defaults (rules.py) for "Reset to defaults". */
const DEFAULT_FIELD_MAP: FieldMap = {
  bank_amount_field: 'amount',
  bill_amount_field: 'net_payable_amount',
  bank_date_field: 'value_date',
  bill_date_primary: 'payment_advice_date',
  bill_date_fallback: 'payment_order_date',
  exact_signals: [{ bank_field: 'zone_guess', bill_field: 'zone', weight: 2, key: 'zone' }],
  eligibility_field: 'bill_status',
  fallback_due_statuses: ['CO7 DONE'],
}

const DEFAULT_RULES: CustomerRules = {
  date_tolerance_days: 2,
  amount_tolerance: 0.0,
  window_days: 0,
  co7_lookback_days: 5,
  allow_batched: true,
  max_batch_size: 3,
  paid_statuses: ['CO7 DONE', 'PAYMENT MADE'],
  weights: { advice_date: 4, zone: 2, co7_date: 1 },
  field_map: DEFAULT_FIELD_MAP,
  copy_overrides: {},
  batch_amount_slack: 0.5,
  amount_decimals: 2,
  ar_overdue_days: 30,
}

/** Terminology sections in display order: section key + heading + what
 *  the codes mean. Codes themselves come from copy_effective (server). */
const COPY_SECTIONS: Array<{ section: string; title: string; note: string }> = [
  { section: 'gap_type', title: 'Bank-only exceptions',
    note: 'guidance shown per gap type when a credit has no bill' },
  { section: 'expected_basis', title: 'Bill-only exceptions',
    note: 'guidance shown per expected-basis when a bill has no credit' },
  { section: 'review', title: 'Match review',
    note: 'guidance shown per review confidence on weak matches' },
]

/** Editable list of short string tokens (statuses). */
function ChipEditor({ values, onChange, disabled }: {
  values: string[]
  onChange: (v: string[]) => void
  disabled?: boolean
}) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (!v || values.includes(v)) return
    onChange([...values, v])
    setDraft('')
  }
  return (
    <span className="chip-editor">
      {values.map((v) => (
        <span key={v} className="chip">
          {v}
          <button className="chip-x" disabled={disabled} title="remove"
                  onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      <input className="chip-input" placeholder="add…" value={draft} disabled={disabled}
             onChange={(e) => setDraft(e.target.value)}
             onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
      <button className="btn-refresh chip-add" disabled={disabled || !draft.trim()}
              onClick={add}>add</button>
    </span>
  )
}

function FieldSelect({ value, options, onChange, allowNone, disabled }: {
  value: string | null
  options: string[]
  onChange: (v: string | null) => void
  allowNone?: boolean
  disabled?: boolean
}) {
  return (
    <select value={value ?? ''} disabled={disabled}
            onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}>
      {allowNone && <option value="">— none —</option>}
      {options.map((f) => <option key={f} value={f}>{f}</option>)}
      {/* keep an unknown stored value visible rather than silently snapping */}
      {value && !options.includes(value) && <option value={value}>{value} (unknown)</option>}
    </select>
  )
}

export function MatchingConfigPanel({ customerId }: Props) {
  const [schema, setSchema] = useState<GoldSchema | null>(null)
  const [config, setConfig] = useState<CustomerConfig | null>(null)
  const [rules, setRules] = useState<CustomerRules | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  useEffect(() => {
    setError(null)
    setSaved(false)
    setDirty(false)
    Promise.all([fetchGoldSchema(), fetchCustomerConfig(customerId)])
      .then(([s, c]) => {
        setSchema(s)
        setConfig(c)
        setRules(c.rules)
      })
      .catch((e) =>
        setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e))))
  }, [customerId])

  if (error && !rules) return <ErrorBanner error={error} />
  if (!schema || !config || !rules) return <p className="footer-note">Loading configuration…</p>

  const bank = schema.bank_txns
  const bills = schema.bills
  const fm = rules.field_map

  const patch = (r: Partial<CustomerRules>) => {
    setRules((prev) => (prev ? { ...prev, ...r } : prev))
    setDirty(true)
    setSaved(false)
  }
  const patchMap = (m: Partial<FieldMap>) => patch({ field_map: { ...fm, ...m } })
  const patchWeight = (key: string, w: number) =>
    patch({ weights: { ...rules.weights, [key]: w } })

  // edits accumulate in copy_overrides; the backend stores only entries
  // that differ from its defaults (sparse), so round-trips stay clean
  const copyValue = (section: string, code: string): string =>
    rules.copy_overrides?.[section]?.[code]
    ?? rules.copy_effective?.[section]?.[code] ?? ''
  const patchCopy = (section: string, code: string, text: string) => {
    const next: CopyText = {
      ...(rules.copy_overrides ?? {}),
      [section]: { ...(rules.copy_overrides?.[section] ?? {}), [code]: text },
    }
    patch({ copy_overrides: next })
  }
  // display label for a frozen code ("labels" section; falls back to code)
  const labelValue = (code: string): string =>
    copyValue('labels', code) || code

  const setSignal = (i: number, s: Partial<ExactSignal>) => {
    const next = fm.exact_signals.map((sig, j) => (j === i ? { ...sig, ...s } : sig))
    // a keyed signal's effective weight comes from the weights dict — keep both in step
    const sig = next[i]
    patchMap({ exact_signals: next })
    if (s.weight !== undefined && sig.key) patchWeight(sig.key, s.weight)
  }

  const signalWeight = (sig: ExactSignal) =>
    sig.key && rules.weights[sig.key] !== undefined ? rules.weights[sig.key] : sig.weight

  const onSave = async () => {
    setSaving(true)
    setError(null)
    try {
      // an emptied textarea/label means "back to default" — drop it
      // rather than sending an empty string the API would refuse
      const cleaned: CopyText = {}
      for (const [section, entries] of Object.entries(rules.copy_overrides ?? {})) {
        const kept = Object.fromEntries(
          Object.entries(entries).filter(([, t]) => t.trim() !== ''))
        if (Object.keys(kept).length) cleaned[section] = kept
      }
      const res = await saveCustomerConfig(customerId,
                                           { ...rules, copy_overrides: cleaned })
      setConfig(res)
      setRules(res.rules)
      setDirty(false)
      setSaved(true)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="config-panel">
      <p className="hint">
        Which gold-layer fields drive reconciliation for <strong>{config.name}</strong>.
        Amount is a filter — pairs are only considered when amounts already agree — while
        the date and exact signals score and break ties. Changes apply to every future run
        for this customer.
      </p>

      <div className="config-section">
        <h3 className="ledger-h">Amount join</h3>
        <div className="config-grid">
          <label className="ctx-field">
            <span className="slot-label">Bank field</span>
            <FieldSelect value={fm.bank_amount_field} options={bank.numeric_fields}
                         onChange={(v) => v && patchMap({ bank_amount_field: v })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Bill field</span>
            <FieldSelect value={fm.bill_amount_field} options={bills.numeric_fields}
                         onChange={(v) => v && patchMap({ bill_amount_field: v })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Tolerance (₹)</span>
            <input type="number" min={0} step={0.01} value={rules.amount_tolerance}
                   onChange={(e) => patch({ amount_tolerance: Number(e.target.value) })} />
          </label>
        </div>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Date signal</h3>
        <div className="config-grid">
          <label className="ctx-field">
            <span className="slot-label">Bank field</span>
            <FieldSelect value={fm.bank_date_field} options={bank.date_fields}
                         onChange={(v) => v && patchMap({ bank_date_field: v })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Bill primary</span>
            <FieldSelect value={fm.bill_date_primary} options={bills.date_fields}
                         onChange={(v) => v && patchMap({ bill_date_primary: v })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Bill fallback</span>
            <FieldSelect value={fm.bill_date_fallback} options={bills.date_fields} allowNone
                         onChange={(v) => patchMap({ bill_date_fallback: v })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Tolerance (days)</span>
            <input type="number" min={0} value={rules.date_tolerance_days}
                   onChange={(e) => patch({ date_tolerance_days: Number(e.target.value) })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Primary weight</span>
            <input type="number" min={1} value={rules.weights.advice_date ?? 4}
                   onChange={(e) => patchWeight('advice_date', Number(e.target.value))} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Fallback weight</span>
            <input type="number" min={1} value={rules.weights.co7_date ?? 1}
                   onChange={(e) => patchWeight('co7_date', Number(e.target.value))} />
          </label>
        </div>
        <p className="explain">
          A match on the primary date carries more weight than the fallback. Set fallback to
          “— none —” to disable the second date entirely.
        </p>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Exact signals</h3>
        <p className="explain">
          Field pairs compared for exact (case/space-insensitive) equality — each agreeing
          pair adds its weight, and a HIGH-confidence match needs all of them to agree. With
          no signals, confidence tops out below HIGH.
        </p>
        {fm.exact_signals.length === 0 && (
          <p className="chip-note">⚠ no exact signals — matches rely on amount + date only.</p>
        )}
        {fm.exact_signals.map((sig, i) => (
          <div className="config-grid signal-row" key={i}>
            <label className="ctx-field">
              <span className="slot-label">Bank field</span>
              <FieldSelect value={sig.bank_field} options={bank.fields}
                           onChange={(v) => v && setSignal(i, { bank_field: v })} />
            </label>
            <label className="ctx-field">
              <span className="slot-label">Bill field</span>
              <FieldSelect value={sig.bill_field} options={bills.fields}
                           onChange={(v) => v && setSignal(i, { bill_field: v })} />
            </label>
            <label className="ctx-field">
              <span className="slot-label">Weight</span>
              <input type="number" min={1} value={signalWeight(sig)}
                     onChange={(e) => setSignal(i, { weight: Number(e.target.value) })} />
            </label>
            <span className="signal-tail">
              {sig.key && <span className="chip">key: {sig.key}</span>}
              <button className="btn-reject" title="remove signal"
                      onClick={() => patchMap({
                        exact_signals: fm.exact_signals.filter((_, j) => j !== i),
                      })}>remove</button>
            </span>
          </div>
        ))}
        <button className="btn-refresh"
                onClick={() => patchMap({
                  exact_signals: [...fm.exact_signals,
                    { bank_field: bank.fields[0] ?? '', bill_field: bills.fields[0] ?? '',
                      weight: 2, key: null }],
                })}>+ add signal</button>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Eligibility</h3>
        <div className="config-grid">
          <label className="ctx-field">
            <span className="slot-label">Bill status field</span>
            <FieldSelect value={fm.eligibility_field} options={bills.fields}
                         onChange={(v) => v && patchMap({ eligibility_field: v })} />
          </label>
        </div>
        <div className="config-chip-row">
          <span className="slot-label">Paid statuses (eligible to match)</span>
          <ChipEditor values={rules.paid_statuses}
                      onChange={(v) => patch({ paid_statuses: v })} />
        </div>
        <div className="config-chip-row">
          <span className="slot-label">Fallback-due statuses</span>
          <ChipEditor values={fm.fallback_due_statuses}
                      onChange={(v) => patchMap({ fallback_due_statuses: v })} />
        </div>
        <p className="explain">
          A bill with no primary date is still expected in a statement when its status is in
          the fallback-due list and its fallback date falls inside the lookback window —
          the payment order can go out before the export refreshes the status.
        </p>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Other tunables</h3>
        <div className="config-grid">
          <label className="ctx-field">
            <span className="slot-label">Window days</span>
            <input type="number" min={0} value={rules.window_days}
                   onChange={(e) => patch({ window_days: Number(e.target.value) })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Fallback lookback days</span>
            <input type="number" min={0} value={rules.co7_lookback_days}
                   onChange={(e) => patch({ co7_lookback_days: Number(e.target.value) })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Batched pass</span>
            <input type="checkbox" checked={rules.allow_batched}
                   onChange={(e) => patch({ allow_batched: e.target.checked })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Max batch size</span>
            <input type="number" min={2} value={rules.max_batch_size}
                   onChange={(e) => patch({ max_batch_size: Number(e.target.value) })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Batch amount slack</span>
            <input type="number" min={0} step={0.05} value={rules.batch_amount_slack}
                   onChange={(e) => patch({ batch_amount_slack: Number(e.target.value) })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">Amount decimals</span>
            <input type="number" min={0} max={6} value={rules.amount_decimals}
                   onChange={(e) => patch({ amount_decimals: Number(e.target.value) })} />
          </label>
          <label className="ctx-field">
            <span className="slot-label">AR overdue after (days)</span>
            <input type="number" min={0} value={rules.ar_overdue_days}
                   onChange={(e) => patch({ ar_overdue_days: Number(e.target.value) })} />
          </label>
        </div>
        <p className="explain">
          Batch slack is the amount gap a batched (one-credit-covers-several-bills)
          match may leave unexplained; amount decimals is the rounding precision of
          the amount join.
        </p>
      </div>

      <div className="config-section">
        <h3 className="ledger-h">Terminology &amp; guidance</h3>
        <p className="explain">
          The advisory text and display names stamped into exception rows, editable
          per customer — the underlying codes never change. Edits apply to future runs.
        </p>
        {COPY_SECTIONS.map(({ section, title, note }) => {
          const codes = Object.keys(rules.copy_effective?.[section] ?? {})
          if (codes.length === 0) return null
          return (
            <div key={section} className="copy-section">
              <h4 className="ingest-section-h">{title}</h4>
              <p className="explain">{note}</p>
              {codes.map((code) => (
                <div key={code} className="ctx-field copy-row">
                  <span className="copy-row-head">
                    <input className="copy-label-input" value={labelValue(code)}
                           title={`display name — editable; stored under code ${code}`}
                           onChange={(e) => patchCopy('labels', code, e.target.value)} />
                  </span>
                  <textarea rows={2} value={copyValue(section, code)}
                            title={`guidance text for ${code}`}
                            onChange={(e) => patchCopy(section, code, e.target.value)} />
                </div>
              ))}
            </div>
          )
        })}
      </div>

      {error && <ErrorBanner error={error} />}

      <div className="run-row">
        <button className="btn-run" disabled={!dirty || saving} onClick={onSave}>
          {saving ? 'Saving…' : 'Save configuration'}
        </button>
        <button className="btn-refresh" disabled={saving}
                onClick={() => {
                  // keep copy_effective so the terminology editors stay
                  // rendered; empty overrides restore default text on save
                  setRules({ ...DEFAULT_RULES,
                             copy_effective: rules.copy_effective })
                  setDirty(true)
                  setSaved(false)
                }}>
          Reset to defaults
        </button>
        {saved && <span className="chip chip-settled">saved</span>}
        {dirty && !saved && <span className="chip-note">unsaved changes</span>}
      </div>
    </div>
  )
}
