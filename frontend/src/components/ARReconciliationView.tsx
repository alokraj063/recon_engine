import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import type { ArRow, ArStatus, ArView, RunListItem } from '../types'
import { fetchAr, fetchOperatingUnits, fetchRuns } from '../api'
import { fmtDay, inDayRange, inr, inrCompact, n, plural } from '../format'
import { ColumnFilter } from './filters/ColumnFilter'
import { FilterChips, type FilterChip } from './filters/FilterChips'
import { buildOptions } from './filters/facets'
import { SnapshotNotice } from './SnapshotNotice'
import {
  EMPTY_RUN_FILTER, RunFilter, runFilterSet, runLabelFor, type RunFilterValue,
} from './RunFilter'
import {
  DateFilter, UNASSIGNED_UNIT, resolveWindow, unitsLabel, windowLabel,
  type DateFilterValue,
} from './DateFilter'
import {
  Card, EmptyState, MoreRows, Notice, PageHeader, PartitionBar, RefreshButton, TextLink,
  useProgressiveRows,
} from './ui'

/** AR opens on the whole working set; the quick picks are one click away. */
const AR_DEFAULT_FILTER: DateFilterValue = { pick: 'all', from: '', to: '', units: null }

/** The day a row is filtered on: the credit's value date for settled /
 *  in-review rows, the bill's aging anchor (advice → order → submission,
 *  already resolved server-side as due_date) for outstanding ones. */
function rowDay(r: ArRow): string | null {
  if (r.status === 'SETTLED' || r.status === 'IN_REVIEW') return r.pay?.value_date ?? r.due_date
  return r.due_date
}

function inUnits(r: ArRow, units: string[] | null): boolean {
  if (!units) return true
  const u = r.org_unit_operating ?? null
  return u ? units.includes(u) : units.includes(UNASSIGNED_UNIT)
}

interface Props {
  customerId: string
  refreshKey: number
  /** settled / in-review rows link into the Analyst queue */
  onOpenInQueue: (matchLedgerId: string) => void
  /** the empty-ledger notice links back to Reconcile */
  onGoToReconcile: () => void
}

const STATUS_LABEL: Record<ArStatus, string> = {
  SETTLED: 'Settled',
  IN_REVIEW: 'In review',
  AWAITING: 'Awaiting',
  OVERDUE: 'Overdue',
}
/** the table's status tabs, most urgent first */
const STATUS_ORDER: ArStatus[] = ['OVERDUE', 'AWAITING', 'IN_REVIEW', 'SETTLED']

const isOpen = (r: ArRow) => r.status === 'AWAITING' || r.status === 'OVERDUE'

/* Aging of OUTSTANDING bills only — settled / in-review money is not
   owed any more. Age runs from the advice / payment-order date. */
const BUCKETS: Array<{ key: string; label: string; lo: number; hi: number | null; tone: string }> = [
  { key: '0-30', label: '0–30 days', lo: 0, hi: 30, tone: 'a1' },
  { key: '31-60', label: '31–60 days', lo: 31, hi: 60, tone: 'a2' },
  { key: '61-90', label: '61–90 days', lo: 61, hi: 90, tone: 'a3' },
  { key: '90+', label: 'Over 90 days', lo: 91, hi: null, tone: 'a4' },
]
function bucketOf(r: ArRow): string {
  if (r.age_days === null) return 'undated'
  const b = BUCKETS.find((x) => r.age_days! >= x.lo && (x.hi === null || r.age_days! <= x.hi))
  return b?.key ?? 'undated'
}
const bucketLabel = (k: string) => BUCKETS.find((b) => b.key === k)?.label ?? 'No due date'

const sum = (xs: Array<number | null>) => xs.reduce<number>((s, v) => s + (v ?? 0), 0)

export function ARReconciliationView({
  customerId, refreshKey, onOpenInQueue, onGoToReconcile,
}: Props) {
  const [data, setData] = useState<ArView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // status tab + the Status column filter share one state (empty = all)
  const [statusFilter, setStatusFilter] = useState<string[]>([])
  // an aging bucket picked from the Aging card (outstanding rows only)
  const [ageFilter, setAgeFilter] = useState<string[]>([])
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [runFilter, setRunFilter] = useState<RunFilterValue>(EMPTY_RUN_FILTER)
  const [dateFilter, setDateFilter] = useState<DateFilterValue>(AR_DEFAULT_FILTER)
  const [units, setUnits] = useState<string[]>([])

  const load = useCallback(() => {
    setError(null)
    setLoading(true)
    fetchAr(customerId)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false))
    fetchRuns(customerId, 200).then(setRuns).catch(() => setRuns([]))
  }, [customerId])

  useEffect(load, [load, refreshKey])
  useEffect(() => {
    setRunFilter(EMPTY_RUN_FILTER); setDateFilter(AR_DEFAULT_FILTER)
    setStatusFilter([]); setAgeFilter([])
  }, [customerId])
  useEffect(() => {
    fetchOperatingUnits(customerId)
      .then((r) => setUnits(r.units.map((u) => u.unit)))
      .catch(() => setUnits([]))
  }, [customerId, refreshKey])

  const runSet = useMemo(() => runFilterSet(runFilter, runs), [runFilter, runs])
  // the run filter AND the date/unit filter narrow the working set, and
  // every figure on the page follows it — the page reads as one scope
  const win = resolveWindow(dateFilter)
  const inScope = useMemo(() => (data?.rows ?? []).filter((r) => {
    if (runSet && !(!!r.run_id && runSet.has(r.run_id))) return false
    if (win.from || win.to) {
      const day = rowDay(r)
      if (!day || !inDayRange(day, win.from, win.to)) return false
    }
    return inUnits(r, dateFilter.units)
  }), [data, runSet, win.from, win.to, dateFilter.units])

  const rows = inScope.filter((r) =>
    (statusFilter.length === 0 || statusFilter.includes(r.status))
    && (ageFilter.length === 0 || (isOpen(r) && ageFilter.includes(bucketOf(r)))))
  const drawn = useProgressiveRows(rows)
  const statusCounts = inScope.reduce<Record<string, number>>(
    (acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {})
  const scoped = !!runSet || win.from !== '' || win.to !== '' || dateFilter.units !== null
  const runNote = data && scoped ? `${inScope.length} of ${data.rows.length} bills` : undefined

  // figures over the rows in scope (the server's KPIs are whole-customer)
  const figs = useMemo(() => {
    const open = inScope.filter(isOpen)
    const overdue = open.filter((r) => r.status === 'OVERDUE')
    const settledTxns = new Map<string, { amount: number; value_date: string | null }>()
    for (const r of inScope) {
      if (r.status === 'SETTLED' && r.pay?.bank_ref) {
        settledTxns.set(`${r.pay.bank_ref}|${r.pay.value_date}`,
          { amount: r.pay.amount ?? 0, value_date: r.pay.value_date })
      }
    }
    const now = new Date()
    const ym = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    const received = [...settledTxns.values()]
    const aging = [...BUCKETS.map((b) => b.key), 'undated'].map((key) => {
      const hit = open.filter((r) => bucketOf(r) === key)
      return { key, count: hit.length, value: sum(hit.map((r) => r.net_payable_amount)) }
    })
    return {
      open: { count: open.length, value: sum(open.map((r) => r.net_payable_amount)) },
      overdue: { count: overdue.length, value: sum(overdue.map((r) => r.net_payable_amount)) },
      received: {
        count: received.length,
        value: sum(received.map((t) => t.amount)),
        mtd: sum(received.filter((t) => (t.value_date ?? '').startsWith(ym)).map((t) => t.amount)),
      },
      aging,
    }
  }, [inScope])

  const scopeLabel = `${windowLabel(dateFilter)}${units.length ? ` · ${unitsLabel(dateFilter, units)}` : ''}${runSet ? ' · selected runs' : ''}`
  const showStatus = (s: ArStatus | null) => { setStatusFilter(s ? [s] : []); setAgeFilter([]) }
  const showAge = (k: string) => {
    setAgeFilter(ageFilter.length === 1 && ageFilter[0] === k ? [] : [k])
    setStatusFilter([])
  }
  const clearAll = () => { setStatusFilter([]); setAgeFilter([]) }

  const chips: FilterChip[] = [
    { key: 'status', label: 'Status', values: statusFilter,
      format: (v) => STATUS_LABEL[v as ArStatus] ?? v,
      onRemove: (v) => setStatusFilter(v === undefined ? [] : statusFilter.filter((x) => x !== v)) },
    { key: 'age', label: 'Age', values: ageFilter, format: bucketLabel,
      onRemove: (v) => setAgeFilter(v === undefined ? [] : ageFilter.filter((x) => x !== v)) },
  ]
  const anyChip = statusFilter.length > 0 || ageFilter.length > 0
  const agingMax = Math.max(1, ...figs.aging.map((b) => b.value))
  const settledCount = statusCounts.SETTLED ?? 0
  const reviewCount = statusCounts.IN_REVIEW ?? 0
  const awaitingCount = statusCounts.AWAITING ?? 0
  const overdueCount = statusCounts.OVERDUE ?? 0

  return (
    <section className="ui-page">
      <PageHeader title="AR Reconciliation"
                  context={scopeLabel}>
        <DateFilter value={dateFilter} onChange={setDateFilter} units={units} />
        {runs.length > 0 && (
          <RunFilter runs={runs} value={runFilter} onChange={setRunFilter} note={runNote} />
        )}
        <RefreshButton onClick={load} loading={loading} label="Refresh AR" />
      </PageHeader>

      {error && (
        <Notice tone="error" action={<TextLink onClick={load}>Try again</TextLink>}>
          Could not load the AR view: {error}
        </Notice>
      )}
      {!data && !error && (
        <div className="ui-grid-75">
          <div className="ui-card sk" style={{ minHeight: 210 }} />
          <div className="ui-card sk" style={{ minHeight: 210 }} />
        </div>
      )}

      {data && data.rows.length === 0 && (
        <section className="ui-card">
          <SnapshotNotice runs={runs} what="AR Reconciliation" onGoToReconcile={onGoToReconcile} />
        </section>
      )}

      {data && data.rows.length > 0 && (
        <>
          <div className="ui-grid-75">
            {/* ---- the position ---- */}
            <Card title="Receivables position" className="ar-position">
              <div className="ar-position-body">
                <div className="ar-figures">
                  <button type="button" className="ar-figure"
                          onClick={() => setStatusFilter(['AWAITING', 'OVERDUE'])}
                          title={`${inr(figs.open.value)} — show the outstanding bills`}>
                    <span className="ui-eyebrow">Outstanding</span>
                    <span className="ui-big">{inrCompact(figs.open.value)}</span>
                    <span className="ar-figure-sub">
                      {n(figs.open.count)} {plural(figs.open.count, 'bill', 'bills')}
                    </span>
                  </button>
                  <button type="button" className="ar-figure is-bad" onClick={() => showStatus('OVERDUE')}
                          title={`${inr(figs.overdue.value)} — show the overdue bills`}>
                    <span className="ui-eyebrow">Overdue</span>
                    <span className="ui-big">{inrCompact(figs.overdue.value)}</span>
                    <span className="ar-figure-sub">
                      {n(figs.overdue.count)} {plural(figs.overdue.count, 'bill', 'bills')}
                    </span>
                  </button>
                  <button type="button" className="ar-figure" onClick={() => showStatus('SETTLED')}
                          title={`${inr(figs.received.value)} — show the settled bills`}>
                    <span className="ui-eyebrow">Received</span>
                    <span className="ui-big">{inrCompact(figs.received.value)}</span>
                    <span className="ar-figure-sub">
                      {n(figs.received.count)} {plural(figs.received.count, 'credit', 'credits')} · {inrCompact(figs.received.mtd)} MTD
                    </span>
                  </button>
                </div>
                <div className="ar-partition">
                  <span className="ui-eyebrow">Bills by status</span>
                  <PartitionBar unit={['bill', 'bills']} showCount parts={[
                    { key: 'SETTLED', tone: 'settled', label: 'Settled', count: settledCount,
                      onOpen: () => showStatus('SETTLED') },
                    { key: 'IN_REVIEW', tone: 'review', label: 'In review', count: reviewCount,
                      onOpen: () => showStatus('IN_REVIEW') },
                    { key: 'AWAITING', tone: 'awaiting', label: 'Awaiting', count: awaitingCount,
                      onOpen: () => showStatus('AWAITING') },
                    { key: 'OVERDUE', tone: 'open', label: 'Overdue', count: overdueCount,
                      onOpen: () => showStatus('OVERDUE') },
                  ]} />
                </div>
              </div>
            </Card>

            {/* ---- how old the rest is ---- */}
            <Card title="Aging" sub="Outstanding bills by days since advice" ruled>
              {figs.open.count === 0 ? (
                <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />} title="No outstanding bills">
                </EmptyState>
              ) : (
                <ul className="ar-aging">
                  {figs.aging.filter((b) => b.count > 0 || b.key !== 'undated').map((b) => {
                    const on = ageFilter.includes(b.key)
                    const tone = BUCKETS.find((x) => x.key === b.key)?.tone ?? 'neutral'
                    return (
                      <li key={b.key}>
                        <button type="button" className={`ar-aging-row${on ? ' is-on' : ''}`}
                                disabled={b.count === 0} onClick={() => showAge(b.key)}
                                title={`${inr(b.value)} · ${n(b.count)} ${plural(b.count, 'bill', 'bills')}`}>
                          <span className="ar-aging-label">{bucketLabel(b.key)}</span>
                          <span className="ar-aging-track">
                            <span className={`ar-aging-fill seg-${tone}`}
                                  style={{ width: `${(b.value / agingMax) * 100}%` }} />
                          </span>
                          <span className="ar-aging-value">{inrCompact(b.value)}</span>
                          <span className="ar-aging-count">{n(b.count)}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </Card>
          </div>

          {/* ---- the working set ---- */}
          <section className="ui-card">
            <div className="ui-tabbar">
              <div className="ui-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={statusFilter.length === 0}
                        className={`ui-tab${statusFilter.length === 0 ? ' is-on' : ''}`}
                        onClick={() => showStatus(null)}>
                  All bills <span className="ui-tab-count">{n(inScope.length)}</span>
                </button>
                {STATUS_ORDER.map((s) => {
                  const on = statusFilter.length === 1 && statusFilter[0] === s
                  return (
                    <button key={s} type="button" role="tab" aria-selected={on}
                            className={`ui-tab${on ? ' is-on' : ''}`} onClick={() => showStatus(s)}>
                      {s === 'OVERDUE' && (statusCounts[s] ?? 0) > 0
                        && <AlertTriangle size={13} strokeWidth={2} className="ar-tab-warn" />}
                      {STATUS_LABEL[s]} <span className="ui-tab-count">{n(statusCounts[s] ?? 0)}</span>
                    </button>
                  )
                })}
              </div>
              {rows.length !== inScope.length && (
                <span className="ui-tabbar-note">
                  {n(rows.length)} of {n(inScope.length)} bills
                  <TextLink onClick={clearAll}>Show all</TextLink>
                </span>
              )}
            </div>
            {anyChip && <div className="ui-filterbar"><FilterChips chips={chips} /></div>}

            {rows.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={22} strokeWidth={1.75} />} title="No bills match the current filters">
                <TextLink onClick={clearAll}>Clear filters</TextLink>
              </EmptyState>
            ) : (
              <div className="ledger-wrap ar-table-wrap">
                <table className="ledger ar-table">
                  <thead>
                    <tr>
                      <th>Bill</th>
                      <th>
                        Status
                        <ColumnFilter label="Status" value={statusFilter} onApply={setStatusFilter}
                                      format={(v) => STATUS_LABEL[v as ArStatus] ?? v}
                                      options={buildOptions(inScope, (r) => r.status)} />
                      </th>
                      <th>Zone</th>
                      <th>Due</th>
                      <th className="num">Age</th>
                      <th className="num">Net payable</th>
                      <th>Paid by</th>
                      <th className="num">Paid</th>
                      <th className="num">Variance</th>
                      <th>Match</th>
                      <th>Run</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drawn.shown.map((r, i) => (
                      <tr key={r.match_ledger_id ?? r.exception_id ?? i}
                          className={r.match_ledger_id ? 'ar-row-link' : ''}
                          title={r.match_ledger_id ? 'Open in Analyst queue' : undefined}
                          onClick={() => { if (r.match_ledger_id) onOpenInQueue(r.match_ledger_id) }}>
                        <td className="mono">{r.bill_number ?? '—'}</td>
                        <td><span className={`stamp stamp-ar-${r.status}`}>{STATUS_LABEL[r.status]}</span></td>
                        <td>{r.zone ?? '—'}</td>
                        <td className="nowrap" title={r.due_date ?? undefined}>{fmtDay(r.due_date)}</td>
                        <td className="num">
                          {r.age_days !== null && isOpen(r)
                            ? <span className={`ui-age${r.status === 'OVERDUE' ? ' is-late' : ''}`}>{r.age_days}d</span>
                            : '—'}
                        </td>
                        <td className="num strong">{r.net_payable_amount !== null ? inr(r.net_payable_amount) : '—'}</td>
                        <td>
                          {r.pay ? (
                            <div className="party-cell">
                              <span className="party-ref">{r.pay.bank_ref ?? '—'}</span>
                              <span className="party-meta">{fmtDay(r.pay.value_date)}</span>
                            </div>
                          ) : <span className="muted">—</span>}
                        </td>
                        <td className="num">{r.pay?.amount != null ? inr(r.pay.amount) : '—'}</td>
                        <td className="num">
                          {r.variance === null || !r.pay ? '—'
                            : r.variance === 0 ? <span className="empty-cell">₹0</span>
                            : <span className="ar-variance">{inr(r.variance)}</span>}
                        </td>
                        <td className="mono">{r.match_seq !== null ? `M-${r.match_seq}` : '—'}</td>
                        <td className="run-cell" title={r.run_id ?? undefined}>{runLabelFor(runs, r.run_id)}</td>
                      </tr>
                    ))}
                    <MoreRows remaining={drawn.remaining} onMore={drawn.more}
                              colSpan={11} noun="bills" />
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </section>
  )
}
