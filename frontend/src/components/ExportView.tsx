import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import {
  fetchWebadiPreview, webadiUrl, type WebadiPreview, type WebadiRecord,
} from '../api'
import { ApiError, type CustomerInfo } from '../types'
import { inr, n, plural, fmtDay } from '../format'
import {
  Card, CustomerSelect, Dot, EmptyState, MoreRows, Notice, Page, PageHeader,
  RefreshButton, SkeletonCards, Stat, StatStrip, TextLink, ToolSep, useProgressiveRows,
} from './ui'
import { FilterChips, type FilterChip } from './filters/FilterChips'
import { ErrorBanner } from './ErrorBanner'

/* Oracle AR Receipt Upload (WebADI): confirmed matches whose credit's
   value date falls on the chosen day(s), one record per bill recovery
   line, one workbook sheet per operating unit. Blank cells are values
   the data cannot supply — the issue filter lists them. */

const UNITS = ['Hosur', 'Rohtak', 'Friction', 'Unassigned'] as const

interface Props {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onOpenMatch: (matchLedgerId: string) => void
}

const money = (v: number | null) => (v == null ? '' : inr(v))

function Blank({ show }: { show: boolean }) {
  return show ? <span className="webadi-blank" title="Left blank">—</span> : null
}

export function ExportView({ customers, customerId, onCustomerChange, onOpenMatch }: Props) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [glDate, setGlDate] = useState('')
  const [unit, setUnit] = useState('')
  const [issue, setIssue] = useState('')
  const [data, setData] = useState<WebadiPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  // a new customer starts from its own latest day
  useEffect(() => { setFrom(''); setTo(''); setUnit(''); setIssue('') }, [customerId])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    fetchWebadiPreview({ customerId, from: from || undefined, to: to || undefined, glDate: glDate || undefined })
      .then((d) => {
        setData(d)
        if (!from) { setFrom(d.from); setTo(d.to) }
      })
      .catch((e) => setError(e instanceof ApiError ? e : new ApiError('ERROR', String(e))))
      .finally(() => setLoading(false))
  }, [customerId, from, to, glDate])

  useEffect(() => { load() }, [load])

  const all = data?.records ?? []
  const rows = useMemo(
    () => all.filter((r) => (!unit || r.unit === unit) && (!issue || r.issues.includes(issue))),
    [all, unit, issue])
  const { shown, remaining, more } = useProgressiveRows(rows)

  // figures follow the unit switch (that is what the download carries)
  const scoped = useMemo(() => all.filter((r) => !unit || r.unit === unit), [all, unit])
  const figures = useMemo(() => {
    const receipts = new Map<string, number>()
    const bills = new Set<string>()
    const issues: Record<string, number> = {}
    let flagged = 0
    for (const r of scoped) {
      receipts.set(r.match_ledger_id, r['Receipt Amount'] ?? 0)
      bills.add(`${r.match_ledger_id}|${r['Invoice Number']}|${r.submission_ref}`)
      if (r.issues.length) flagged += 1
      for (const c of r.issues) issues[c] = (issues[c] ?? 0) + 1
    }
    let amount = 0
    receipts.forEach((v) => { amount += v })
    return { receipts: receipts.size, bills: bills.size, amount, issues, flagged }
  }, [scoped])

  const byUnit = data?.summary.by_unit ?? {}
  const issueText = data?.issue_text ?? {}
  const span = from && to && from !== to ? `${fmtDay(from)} – ${fmtDay(to)}` : fmtDay(from)
  const query = { customerId, from, to, unit: unit || undefined, glDate: glDate || undefined }

  const chips: FilterChip[] = [
    { key: 'issue', label: 'Needs attention', values: issue ? [issue] : [],
      format: (v) => issueText[v] ?? v, onRemove: () => setIssue('') },
  ]

  return (
    <Page className="is-fill">
      <PageHeader
        title="WebADI export"
        context={<>{customers.find((c) => c.key === customerId)?.name ?? customerId}<Dot />Oracle AR Receipt Upload</>}
      >
        <CustomerSelect customers={customers} value={customerId} onChange={onCustomerChange} />
        <label className="ui-customer ui-date" title="Credit value date">
          <span>Receipt date</span>
          <input type="date" value={from} aria-label="from date"
                 onChange={(e) => { setFrom(e.target.value); if (!to || e.target.value > to) setTo(e.target.value) }} />
          <span className="ui-date-sep">to</span>
          <input type="date" value={to} min={from} aria-label="to date"
                 onChange={(e) => setTo(e.target.value)} />
        </label>
        <label className="ui-customer ui-date" title="Oracle posting date — empty = each receipt's own date">
          <span>GL date</span>
          <input type="date" value={glDate} aria-label="GL date" onChange={(e) => setGlDate(e.target.value)} />
        </label>
        <RefreshButton onClick={load} loading={loading} />
        <ToolSep />
        <a className={`ui-btn ui-btn-primary btn-ic${scoped.length === 0 ? ' is-disabled' : ''}`}
           href={webadiUrl(query)} download
           aria-disabled={scoped.length === 0}
           onClick={(e) => { if (scoped.length === 0) e.preventDefault() }}>
          <Download size={14} strokeWidth={1.75} /> Download WebADI
        </a>
      </PageHeader>

      {error && <ErrorBanner error={error} />}

      {!data && loading ? <SkeletonCards heights={[92, 360]} /> : data && (
        <>
          <StatStrip>
            <Stat label="Receipts" value={n(figures.receipts)} sub={span} />
            <Stat label="Bills" value={n(figures.bills)} />
            <Stat label="Upload rows" value={n(scoped.length)} sub="one per recovery line" />
            <Stat label="Receipt amount" value={inr(figures.amount)} />
            <Stat label="Needs attention" value={n(figures.flagged)}
                  tone={figures.flagged ? 'warn' : 'ok'}
                  sub={figures.flagged ? plural(figures.flagged, 'row with a blank', 'rows with blanks') : 'none'} />
          </StatStrip>

          {Object.keys(figures.issues).length > 0 && (
            <Notice tone="warn">
              Left blank:{' '}
              {Object.entries(figures.issues).map(([code, count], i) => (
                <span key={code}>
                  {i > 0 && ' · '}
                  <TextLink onClick={() => setIssue(code)}>
                    {issueText[code] ?? code} ({n(count)})
                  </TextLink>
                </span>
              ))}
            </Notice>
          )}

          <Card className="is-fill" ruled
                title="Upload rows"
                sub={`${n(rows.length)} of ${n(all.length)}`}
                action={
                  <span className="ui-seg" role="group" aria-label="Operating unit">
                    <button type="button" className={!unit ? 'on' : ''} onClick={() => setUnit('')}>All</button>
                    {UNITS.filter((u) => byUnit[u]).map((u) => (
                      <button key={u} type="button" className={unit === u ? 'on' : ''} onClick={() => setUnit(u)}>
                        {u} <span className="audit-cat-n">{n(byUnit[u])}</span>
                      </button>
                    ))}
                  </span>
                }>
            {issue && <div className="ui-filterbar"><FilterChips chips={chips} /></div>}
            {all.length === 0 ? (
              <EmptyState title="No confirmed matches on this date">
                {data.latest_date && data.latest_date !== from && (
                  <TextLink onClick={() => { setFrom(data.latest_date!); setTo(data.latest_date!) }}>
                    Latest: {fmtDay(data.latest_date)}
                  </TextLink>
                )}
              </EmptyState>
            ) : rows.length === 0 ? (
              <EmptyState title="No rows match these filters">
                <TextLink onClick={() => { setUnit(''); setIssue('') }}>Show all</TextLink>
              </EmptyState>
            ) : (
              <div className="ledger-wrap is-fill">
                <table className="ledger webadi-table">
                  <thead>
                    <tr>
                      <th>Match</th>
                      <th>Unit</th>
                      <th>Customer Name</th>
                      <th>Receipt Number</th>
                      <th>Receipt Date</th>
                      <th className="num">Receipt Amount</th>
                      <th>Invoice Number</th>
                      <th className="num">Amount Applied</th>
                      <th>Recovery (Bill Status)</th>
                      <th className="num">Adjustment Amount</th>
                      <th>Adjustment Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r: WebadiRecord, i) => (
                      <tr key={`${r.match_ledger_id}-${i}`} className={r.issues.length ? 'is-flagged' : undefined}>
                        <td><TextLink onClick={() => onOpenMatch(r.match_ledger_id)}>{r.match}</TextLink></td>
                        <td title={[r['Operating Unit ID Selected'], r['Receipt Method']].filter(Boolean).join('\n')}>
                          {r.unit}{!r['Receipt Method'] && r.unit !== 'Unassigned' && <Blank show />}
                        </td>
                        <td title={r.zone ?? undefined}>{r['Customer Name'] ?? <Blank show />}</td>
                        <td className="mono">{r['Receipt Number']}</td>
                        <td>{r['Receipt Date']}</td>
                        <td className="num">{money(r['Receipt Amount'])}</td>
                        <td className="mono">{r['Invoice Number'] ?? <Blank show />}</td>
                        <td className="num">{money(r['Receipt Amount Applied'])}</td>
                        <td className="webadi-head">{r.recovery_head}</td>
                        <td className="num">{money(r['Adjustment Amount'])}</td>
                        <td>{r['Adjustment Type'] ?? <Blank show={r.recovery_head != null} />}</td>
                      </tr>
                    ))}
                    <MoreRows remaining={remaining} colSpan={11} onMore={more} />
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </Page>
  )
}
