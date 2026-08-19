import type { TunableOptions } from '../types'

interface Props {
  options: TunableOptions
  onChange: (options: TunableOptions) => void
}

const NUMERIC: Array<{ key: keyof TunableOptions; label: string; explain: string; step?: number }> = [
  {
    key: 'window_days',
    label: 'Window days',
    explain: 'How far either side of the statement dates a payment advice can sit and still be expected here. Widen for a multi-day statement; too wide reports earlier settlements as false shortfalls.',
  },
  {
    key: 'co7_lookback_days',
    label: 'CO7 lookback days',
    explain: 'How long a CO7 can sit without an advice before we stop expecting the credit in this statement.',
  },
  {
    key: 'date_tolerance_days',
    label: 'Date tolerance days',
    explain: 'Maximum gap between the credit value date and the advice/CO7 date for the dates to count as agreeing.',
  },
  {
    key: 'amount_tolerance',
    label: 'Amount tolerance (₹)',
    explain: 'Slack allowed when matching credit amount to bill Net Amt. 0 = exact paise match.',
    step: 0.01,
  },
  {
    key: 'max_batch_size',
    label: 'Max batch size',
    explain: 'Largest set of bills one credit is allowed to cover in the batched pass.',
  },
]

export function AdvancedPanel({ options, onChange }: Props) {
  return (
    <details className="advanced">
      <summary>Advanced options — matching tunables</summary>
      <div className="tunable-grid">
        {NUMERIC.map(({ key, label, explain, step }) => (
          <div className="tunable" key={key}>
            <label htmlFor={`tun-${key}`}>{label}</label>
            <p className="explain">{explain}</p>
            <input
              id={`tun-${key}`}
              type="number"
              step={step ?? 1}
              min={0}
              value={options[key] as number}
              onChange={(e) => onChange({ ...options, [key]: Number(e.target.value) })}
            />
          </div>
        ))}
        <div className="tunable">
          <label htmlFor="tun-batched">Batched pass</label>
          <p className="explain">
            Allow one credit to settle several bills whose Net Amts sum to it (subset-sum pass).
          </p>
          <input
            id="tun-batched"
            type="checkbox"
            checked={options.allow_batched}
            onChange={(e) => onChange({ ...options, allow_batched: e.target.checked })}
          />
        </div>
      </div>
    </details>
  )
}
