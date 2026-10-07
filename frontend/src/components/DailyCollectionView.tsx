import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import {
  dailyCollectionUrl, fetchDailyCollectionPreview,
  type DailyCollectionPreview, type DailyCollectionRow,
} from '../api'
import { ApiError, type CustomerInfo } from '../types'
import { inr, n, plural, fmtDay } from '../format'
import {
  Card, CustomerSelect, Dot, EmptyState, MoreRows, Notice, Page, PageHeader,
  RefreshButton, SkeletonCards, Stat, StatStrip, TextLink, ToolSep, useProgressiveRows,
} from './ui'
import { FilterChips, type FilterChip } from './filters/FilterChips'
import { ErrorBanner } from './ErrorBanner'

/* The collections team's DAILY COLLECTION sheet: confirmed matches whose
   credit falls in the window, one row per (credit, bill), one workbook
   sheet per 4-4-5 fiscal month. The window always starts on a fiscal
   month's first day (the sheet is month to date). Blank cells are values
   the data cannot supply — the issue filter lists them. */

interface Props {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onOpenMatch: (matchLedgerId: string) => void
  onOpenSettings: () => void
  onGoToIngest: () => void
}

const money = (v: number | null) => (v == null ? '' : inr(v))

function Blank({ show }: { show: boolean }) {
  return show ? <span className="webadi-blank" title="Left blank">—</span> : null
}

export function DailyCollectionView({
  customers, customerId, onCustomerChange, onOpenMatch, onOpenSettings, onGoToIngest,
}: Props) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [month, setMonth] = useState('')
  const [issue, setIssue] = useState('')
  const [data, setData] = useState<DailyCollectionPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  // a new customer starts from its own latest month
  useEffect(() => { setFrom(''); setTo(''); setMonth(''); setIssue('') }, [customerId])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    fetchDailyCollectionPreview({ customerId, from: from || undefined, to: to || undefined })
      .then((d) => {
        setData(d)
        // the server snaps `from` to its fiscal month's first day
        if (d.from !== from) setFrom(d.from)
        if (!to) setTo(d.to)
      })
      .catch((e) => setError(e instanceof ApiError ? e : new ApiError('ERROR', String(e))))
      .finally(() => setLoading(false))
  }, [customerId, from, to])

  useEffect(() => { load() }, [load])

  const all = data?.rows ?? []
  const months = useMemo(() => Object.keys(data?.summary.by_month ?? {}), [data])
  const scoped = useMemo(() => all.filter((r) => !month || r.month === month), [all, month])
  const rows = useMemo(() => scoped.filter((r) => !issue || r.issues.includes(issue)), [scoped, issue])
  const { shown, remaining, more } = useProgressiveRows(rows)

  const figures = useMemo(() => {
    const issues: Record<string, number> = {}
    let credits = 0, eft = 0, collection = 0, flagged = 0
    const days = new Set<string>()
    for (const r of scoped) {
      if (r.first_of_credit) { credits += 1; eft += r['EFT AMOUNT'] ?? 0 }
      collection += r['INVOICE AMOUNT'] ?? 0
      days.add(r['EFT DATE'])
      if (r.issues.length) flagged += 1
      for (const c of r.issues) issues[c] = (issues[c] ?? 0) + 1
    }
    return { credits, eft, collection, days: days.size, issues, flagged }
  }, [scoped])

  const issueText = data?.issue_text ?? {}
  const arDates = (data?.ar_statements ?? []).map((a) => a.statement_date).filter(Boolean) as string[]
  const span = from && to ? `${fmtDay(from)} – ${fmtDay(to)}` : ''
  const query = { customerId, from, to }

  const chips: FilterChip[] = [
    { key: 'issue', label: 'Needs attention', values: issue ? [issue] : [],
      format: (v) => issueText[v] ?? v, onRemove: () => setIssue('') },
  ]

  return (
    <Page className="is-fill">
      <PageHeader
        title="Daily collection"
        context={<>
          {customers.find((c) => c.key === customerId)?.name ?? customerId}
          <Dot />{months.length ? months.join(', ') : 'Fiscal month to date'}
          {arDates.length > 0 && <><Dot />AR statement {fmtDay(arDates[arDates.length - 1])}</>}
        </>}
      >
        <CustomerSelect customers={customers} value={customerId} onChange={onCustomerChange} />
        <label className="ui-customer ui-date" title="Credit value date; starts on the first day of its fiscal month">
          <span>EFT date</span>
          <input type="date" value={from} aria-label="from date"
                 onChange={(e) => { setFrom(e.target.value); if (!to || e.target.value > to) setTo(e.target.value) }} />
          <span className="ui-date-sep">to</span>
          <input type="date" value={to} min={from} aria-label="to date"
                 onChange={(e) => setTo(e.target.value)} />
        </label>
        <RefreshButton onClick={load} loading={loading} />
        <ToolSep />
        <a className={`ui-btn ui-btn-primary btn-ic${all.length === 0 ? ' is-disabled' : ''}`}
           href={dailyCollectionUrl(query)} download
           aria-disabled={all.length === 0}
           onClick={(e) => { if (all.length === 0) e.preventDefault() }}>
          <Download size={14} strokeWidth={1.75} /> Download
        </a>
      </PageHeader>

      {error && <ErrorBanner error={error} />}

      {!data && loading ? <SkeletonCards heights={[92, 360]} /> : data && (
        <>
          <StatStrip>
            <Stat label="Credits" value={n(figures.credits)} sub={span} />
            <Stat label="Days" value={n(figures.days)} />
            <Stat label="Collection" value={inr(figures.collection)} sub="invoice amounts" />
            <Stat label="EFT amount" value={inr(figures.eft)} />
            <Stat label="Needs attention" value={n(figures.flagged)}
                  tone={figures.flagged ? 'warn' : 'ok'}
                  sub={figures.flagged ? plural(figures.flagged, 'row with a blank', 'rows with blanks') : 'none'} />
          </StatStrip>

          {arDates.length === 0 && (
            <Notice tone="warn">
              No AR statement: Invoice Value, BRANCH and OD/NOD stay blank.{' '}
              <TextLink onClick={onGoToIngest}>Ingest one</TextLink>
            </Notice>
          )}

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
                title="Collection rows"
                sub={<>
                  {n(rows.length)} of {n(all.length)}
                  <Dot />
                  {data.recipients.length
                    ? <span title={data.recipients.join('\n')}>{n(data.recipients.length)} {plural(data.recipients.length, 'recipient', 'recipients')}</span>
                    : 'No recipients'}
                  {' '}<TextLink onClick={onOpenSettings}>Settings</TextLink>
                </>}
                action={months.length > 1 ? (
                  <span className="ui-seg" role="group" aria-label="Fiscal month">
                    <button type="button" className={!month ? 'on' : ''} onClick={() => setMonth('')}>All</button>
                    {months.map((m) => (
                      <button key={m} type="button" className={month === m ? 'on' : ''} onClick={() => setMonth(m)}>
                        {m} <span className="audit-cat-n">{n(data.summary.by_month[m])}</span>
                      </button>
                    ))}
                  </span>
                ) : undefined}>
            {issue && <div className="ui-filterbar"><FilterChips chips={chips} /></div>}
            {all.length === 0 ? (
              <EmptyState title="No confirmed matches in this window">
                {data.latest_date && data.latest_date !== to && (
                  <TextLink onClick={() => { setFrom(data.latest_date!); setTo(data.latest_date!) }}>
                    Latest: {fmtDay(data.latest_date)}
                  </TextLink>
                )}
              </EmptyState>
            ) : rows.length === 0 ? (
              <EmptyState title="No rows match these filters">
                <TextLink onClick={() => { setMonth(''); setIssue('') }}>Show all</TextLink>
              </EmptyState>
            ) : (
              <div className="ledger-wrap is-fill">
                <table className="ledger webadi-table collection-table">
                  <thead>
                    <tr>
                      <th className="num">SL.NO</th>
                      <th>EFT date</th>
                      <th>Bank ref</th>
                      <th>Region</th>
                      <th>RLY</th>
                      <th className="num">EFT amount</th>
                      <th>Bill no.</th>
                      <th>Bill date</th>
                      <th className="num">Invoice amount</th>
                      <th className="num">Daily collection</th>
                      <th className="num">Total collection</th>
                      <th>Branch</th>
                      <th className="num">Invoice value</th>
                      <th>OD/NOD</th>
                      <th>Week</th>
                      <th>Match</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r: DailyCollectionRow, i) => (
                      <tr key={`${r.match_ledger_id}-${i}`}
                          className={[r.issues.length ? 'is-flagged' : '',
                                      r['DAILY COLLECTION'] != null ? 'is-day-end' : ''].join(' ').trim() || undefined}>
                        <td className="num">{r['SL.NO'] ?? ''}</td>
                        <td>{r.first_of_credit ? fmtDay(r['EFT DATE']) : ''}</td>
                        <td className="mono">{r.first_of_credit ? r['Bank Ref'] : ''}</td>
                        <td>{r.Region ?? <Blank show />}</td>
                        <td>{r.RLY}</td>
                        <td className="num">{money(r['EFT AMOUNT'])}</td>
                        <td className="mono">{r['BILL NO.'] ?? <Blank show />}</td>
                        <td>{fmtDay(r['Bill Date'])}</td>
                        <td className="num">{money(r['INVOICE AMOUNT'])}</td>
                        <td className="num">{money(r['DAILY COLLECTION'])}</td>
                        <td className="num">{money(r['TOTAL COLLECTION'])}</td>
                        <td title={[r.Category, r.subcategory].filter(Boolean).join(' · ') || undefined}>
                          {r.BRANCH ?? <Blank show />}
                        </td>
                        <td className="num" title={r.ar_statement_date ? `AR statement ${fmtDay(r.ar_statement_date)}` : undefined}>
                          {r['Invoice Value'] == null ? <Blank show /> : money(r['Invoice Value'])}
                        </td>
                        <td title={r.due_date ? `Due ${fmtDay(r.due_date)}` : undefined}>
                          {r['OD/NOD'] ?? <Blank show />}
                        </td>
                        <td>{r.Week}</td>
                        <td><TextLink onClick={() => onOpenMatch(r.match_ledger_id)}>{r.match}</TextLink></td>
                      </tr>
                    ))}
                    <MoreRows remaining={remaining} colSpan={16} onMore={more} />
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
