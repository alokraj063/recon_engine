import type { ReconMeta, SummaryRow } from '../types'
import { findAmount, sumAmount, sumCounts } from '../combineRuns'
import { inr } from '../format'

export interface SummaryRun {
  runId: string
  label: string
  summary: SummaryRow[]
  meta: ReconMeta
}

interface Props {
  runs: SummaryRun[]
  /** multi-run selection: counts/amounts derived from the DEDUPED
   *  combined rows, so tiles agree with the Matched/Exceptions tables */
  aggregate?: {
    counts: ReconMeta['counts']
    amounts: { matched: number; bank_only: number; bill_only: number }
  }
}

function SummaryTable({ summary }: { summary: SummaryRow[] }) {
  return (
    <table className="ledger">
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
  )
}

export function SummaryDashboard({ runs, aggregate }: Props) {
  const multi = runs.length > 1
  const counts = multi
    ? aggregate?.counts ?? sumCounts(runs.map((r) => r.meta))
    : runs[0].meta.counts
  const amountOf = (category: string): number | null =>
    multi
      ? sumAmount(runs.map((r) => r.summary), category)
      : findAmount(runs[0].summary, category)

  const { meta } = runs[0]
  const { selfcheck, ingest, ledger } = meta
  const conflicts = ingest?.conflicts ?? 0

  const tiles = [
    {
      label: 'Bank credits',
      count: counts.bank_credits,
      amount: amountOf('Bank credits in statement'),
      tone: 'tone-neutral',
    },
    {
      label: 'Matched',
      count: counts.matched,
      amount: aggregate ? aggregate.amounts.matched : amountOf('Matched'),
      tone: '',
    },
    {
      label: 'Bank only — no bill',
      count: counts.bank_only,
      amount: aggregate ? aggregate.amounts.bank_only : amountOf('Exception - bank only'),
      tone: 'tone-bank',
    },
    {
      label: 'Bill only — no credit',
      count: counts.bill_only,
      amount: aggregate ? aggregate.amounts.bill_only : amountOf('Exception - bill only'),
      tone: 'tone-bill',
    },
  ]

  return (
    <div>
      {!multi && conflicts > 0 && (
        <div className="warn-banner">
          <h3>Settled bills protected from a newer export</h3>
          <p>
            A newer export tried to change {conflicts} settled bill{conflicts === 1 ? '' : 's'} —
            the locked values were kept and the attempted changes recorded as conflicts.
          </p>
        </div>
      )}

      <div className="tiles reveal reveal-1">
        {tiles.map((t) => (
          <div key={t.label} className={`tile ${t.tone}`}>
            <div className="tile-label">{t.label}</div>
            <div className="tile-count">{t.count}</div>
            <div className="tile-amount">{inr(t.amount)}</div>
          </div>
        ))}
      </div>

      {multi && (
        <p className="footer-note overlap-hint reveal reveal-2">
          Totals across {runs.length} runs. Open exceptions reported by several runs are
          shown once (latest state); the bank-credits total is a per-run sum and can
          double-count when runs share a statement.
        </p>
      )}

      {!multi && meta.mode === 'incremental' && ingest && (
        <div className="stat-chips reveal reveal-2">
          <span className="chip">files reused {ingest.files_reused}</span>
          <span className="chip">rows inserted {ingest.rows_inserted}</span>
          <span className="chip">bills updated {ingest.bills_updated}</span>
          <span className="chip">rows reused {ingest.rows_reused}</span>
          <span className={`chip${conflicts > 0 ? ' chip-attempts' : ''}`}>
            conflicts {conflicts}
          </span>
          {ledger && (
            <>
              <span className="chip">matches created {ledger.matches_created}</span>
              <span className="chip">auto-locked {ledger.auto_locked}</span>
              <span className="chip">exceptions opened {ledger.exceptions_opened}</span>
              <span className="chip">exceptions resolved {ledger.exceptions_resolved}</span>
            </>
          )}
        </div>
      )}

      {!multi && selfcheck && (
        selfcheck.passed === false ? (
          <p className="selfcheck-line selfcheck-warn reveal reveal-2">
            <span className="tick">⚠ parse mismatch</span> — statement states {selfcheck.stated_count} credits
            / {inr(selfcheck.stated_total)}; gold rebuilt {selfcheck.parsed_count} / {inr(selfcheck.parsed_total)}
          </p>
        ) : (
          <p className="selfcheck-line reveal reveal-2">
            <span className="tick">✓ parse verified</span> — statement states {selfcheck.stated_count} credits
            / {inr(selfcheck.stated_total)}; parsed {selfcheck.parsed_count} / {inr(selfcheck.parsed_total)}
          </p>
        )
      )}

      {multi ? (
        runs.map((r) => (
          <div key={r.runId} className="reveal reveal-3">
            <h3 className="ledger-h">
              {r.label}
              {r.meta.mode && <span className="stamp seg-inline"> {r.meta.mode}</span>}
            </h3>
            <SummaryTable summary={r.summary} />
          </div>
        ))
      ) : (
        <div className="reveal reveal-3">
          <SummaryTable summary={runs[0].summary} />
        </div>
      )}
    </div>
  )
}
