import type { ReconMeta, SummaryRow } from '../types'
import { inr } from '../format'

interface Props {
  summary: SummaryRow[]
  meta: ReconMeta
}

function findAmount(summary: SummaryRow[], category: string): number | null {
  const row = summary.find((r) => !r.indent && r.Category === category)
  return row?.Amount ?? null
}

export function SummaryDashboard({ summary, meta }: Props) {
  const { counts, selfcheck } = meta
  const tiles = [
    {
      label: 'Bank credits',
      count: counts.bank_credits,
      amount: findAmount(summary, 'Bank credits in statement'),
      tone: 'tone-neutral',
    },
    {
      label: 'Matched',
      count: counts.matched,
      amount: findAmount(summary, 'Matched'),
      tone: '',
    },
    {
      label: 'Bank only — no bill',
      count: counts.bank_only,
      amount: findAmount(summary, 'Exception - bank only'),
      tone: 'tone-bank',
    },
    {
      label: 'Bill only — no credit',
      count: counts.bill_only,
      amount: findAmount(summary, 'Exception - bill only'),
      tone: 'tone-bill',
    },
  ]

  return (
    <div>
      <div className="tiles reveal reveal-1">
        {tiles.map((t) => (
          <div key={t.label} className={`tile ${t.tone}`}>
            <div className="tile-label">{t.label}</div>
            <div className="tile-count">{t.count}</div>
            <div className="tile-amount">{inr(t.amount)}</div>
          </div>
        ))}
      </div>

      {selfcheck && (
        <p className="selfcheck-line reveal reveal-2">
          <span className="tick">✓ parse verified</span> — HSBC states {selfcheck.stated_count} credits
          / {inr(selfcheck.stated_total)}; parsed {selfcheck.parsed_count} / {inr(selfcheck.parsed_total)}
        </p>
      )}

      <table className="ledger reveal reveal-3">
        <thead>
          <tr>
            <th>Category</th>
            <th style={{ textAlign: 'right' }}>Count</th>
            <th style={{ textAlign: 'right' }}>Amount</th>
          </tr>
        </thead>
        <tbody>
          {summary.map((r, i) => (
            <tr key={i} className={r.indent ? 'indent' : 'major'}>
              <td>{r.Category}</td>
              <td className="num">{r.Count ?? '—'}</td>
              <td className="num">{inr(r.Amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
