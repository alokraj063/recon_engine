import { CheckCircle2, ShieldAlert } from 'lucide-react'
import type { ReconMeta, SummaryRow } from '../types'
import { findAmount, sumAmount, sumCounts } from '../combineRuns'
import { fmtDay, inr, inrCompact, n, pct, plural } from '../format'
import { IngestStatsSummary } from './IngestStatsSummary'
import { Card, Notice, Stat, StatStrip } from './ui'

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
  /** the figures open the run's own tables */
  onOpen?: (view: 'matched' | 'exceptions' | 'bank') => void
}

/** Display names for an INCREMENTAL run's summary rows. The stored
 *  Category strings are frozen (golden CSVs, workbooks); only what the
 *  table shows changes: an incremental run's "statement" is its pool
 *  (new credits + carried open ones), and its expected bills start at
 *  the first day of bank data, not at a statement window. */
function displayCategory(cat: string, meta: ReconMeta): string {
  if (meta.mode !== 'incremental') return cat
  if (cat === 'Bank credits in statement') return 'Credits in this run (new + carried)'
  if (cat === 'Bills expected in window') {
    return meta.expected_from ? `Bills expected (from ${fmtDay(meta.expected_from)})` : 'Bills expected'
  }
  return cat
}

function SummaryTable({ summary, meta }: { summary: SummaryRow[]; meta: ReconMeta }) {
  return (
    <table className="ledger summary-table">
      <thead>
        <tr>
          <th>Category</th>
          <th className="num">Count</th>
          <th className="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {summary.map((r, i) => (
          <tr key={i} className={r.indent ? 'indent' : 'major'}>
            <td>{displayCategory(r.Category, meta)}</td>
            <td className="num">{r.Count ?? '—'}</td>
            <td className="num">{inr(r.Amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function SummaryDashboard({ runs, aggregate, onOpen }: Props) {
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
  // one line per statement when several were reconciled; the scalar
  // selfcheck is the single-statement compat form
  const checks = meta.selfchecks?.length
    ? meta.selfchecks
    : selfcheck ? [{ ...selfcheck, original_name: null as string | null }] : []
  const conflicts = ingest?.conflicts ?? 0
  const failed = checks.filter((c) => c.passed === false)

  const matchedAmt = aggregate ? aggregate.amounts.matched : amountOf('Matched')
  const bankOnlyAmt = aggregate ? aggregate.amounts.bank_only : amountOf('Exception - bank only')
  const billOnlyAmt = aggregate ? aggregate.amounts.bill_only : amountOf('Exception - bill only')
  const creditsAmt = amountOf('Bank credits in statement')
  const unrecognised = counts.unrecognised_receipts ?? 0
  const matchable = Math.max(0, counts.bank_credits - unrecognised)

  return (
    <div className="ui-stack">
      {!multi && failed.length > 0 && (
        <Notice tone="warn">
          <strong>Statement totals do not reconcile.</strong> Parsed credits differ from the
          statement's printed totals. Review Parse checks before relying on this run.
        </Notice>
      )}
      {!multi && conflicts > 0 && (
        <Notice tone="warn">
          <strong>{n(conflicts)} locked {plural(conflicts, 'bill', 'bills')} not updated.</strong> A
          newer export attempted changes; they were recorded as ingest conflicts.
        </Notice>
      )}
      {multi && (
        <Notice>
          Combined totals for {runs.length} runs. Exceptions reported by more than one run are
          counted once; bank credit totals are summed per run.
        </Notice>
      )}

      <StatStrip>
        <Stat label="Bank credits" value={n(counts.bank_credits)}
              sub={<>{inrCompact(creditsAmt)}{unrecognised > 0
                ? <> · {n(unrecognised)} other {plural(unrecognised, 'receipt', 'receipts')}</> : null}</>}
              title={`${inr(creditsAmt)} on the statement`}
              onOpen={onOpen && (() => onOpen('bank'))} />
        <Stat label="Matched" value={n(counts.matched)} tone="ok"
              sub={<>{inrCompact(matchedAmt)}{matchable > 0
                ? <> · {pct(Math.min(1, counts.matched / matchable))} of matchable</> : null}</>}
              title={inr(matchedAmt)}
              onOpen={onOpen && (() => onOpen('matched'))} />
        <Stat label="Credits with no bill" value={n(counts.bank_only)}
              tone={counts.bank_only ? 'bad' : undefined}
              sub={inrCompact(bankOnlyAmt)} title={inr(bankOnlyAmt)}
              onOpen={onOpen && (() => onOpen('exceptions'))} />
        <Stat label="Bills with no credit" value={n(counts.bill_only)}
              tone={counts.bill_only ? 'warn' : undefined}
              sub={inrCompact(billOnlyAmt)} title={inr(billOnlyAmt)}
              onOpen={onOpen && (() => onOpen('exceptions'))} />
        {(counts.match_review ?? 0) > 0 && (
          <Stat label="Matches to review" value={n(counts.match_review ?? 0)} tone="warn"
                sub="pending decision"
                onOpen={onOpen && (() => onOpen('exceptions'))} />
        )}
      </StatStrip>

      <div className="ui-grid-75">
        <div className="ui-stack">
          {multi ? runs.map((r) => (
            <Card key={r.runId} title={r.label} sub={r.meta.mode ? `${r.meta.mode} run` : undefined} ruled>
              <SummaryTable summary={r.summary} meta={r.meta} />
            </Card>
          )) : (
            <Card title="Breakdown" ruled>
              <SummaryTable summary={runs[0].summary} meta={runs[0].meta} />
            </Card>
          )}
        </div>

        {!multi && (
          <div className="ui-stack">
            {checks.length > 0 && (
              <Card title="Parse checks" sub="Parsed credits vs statement totals" ruled>
                <ul className="check-list">
                  {checks.map((c, i) => {
                    const who = c.original_name ?? 'Statement'
                    const ok = c.passed !== false
                    return (
                      <li key={i} className={ok ? 'is-ok' : 'is-bad'}>
                        {ok ? <CheckCircle2 size={16} strokeWidth={2} /> : <ShieldAlert size={16} strokeWidth={2} />}
                        <div>
                          <div className="check-title">
                            {ok ? 'Verified' : c.stated_count == null ? 'Could not verify' : 'Mismatch'}
                            <span className="check-who">{who}</span>
                          </div>
                          <div className="check-detail">
                            {c.stated_count == null
                              ? (c.detail ?? 'Statement could not be verified')
                              : <>Statement {n(c.stated_count)} credits / {inr(c.stated_total)} · Parsed{' '}
                                  {n(c.parsed_count ?? 0)} / {inr(c.parsed_total)}</>}
                          </div>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              </Card>
            )}

            {meta.mode === 'incremental' && ledger && (
              <Card title="Ledger changes" ruled>
                <dl className="kv-list">
                  <div><dt>Matches created</dt><dd>{n(ledger.matches_created)}</dd></div>
                  <div><dt>Auto-locked (HIGH)</dt><dd>{n(ledger.auto_locked)}</dd></div>
                  <div><dt>Exceptions opened</dt><dd>{n(ledger.exceptions_opened)}</dd></div>
                  <div><dt>Exceptions resolved</dt><dd>{n(ledger.exceptions_resolved)}</dd></div>
                  {!!ledger.provisional_superseded && (
                    <div><dt>Review matches replaced</dt><dd>{n(ledger.provisional_superseded)}</dd></div>
                  )}
                </dl>
              </Card>
            )}

            {meta.mode === 'incremental' && ingest && (
              <Card title="Data used" ruled>
                <div className="ui-card-body">
                  <IngestStatsSummary stats={ingest} />
                </div>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
