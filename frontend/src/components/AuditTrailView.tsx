import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, ChevronRight, Search } from 'lucide-react'
import { FilterChips } from './filters/FilterChips'
import type { AuditEventRow, CustomerInfo } from '../types'
import { fetchAudit } from '../api'
import { fmtWhen, n } from '../format'
import {
  CustomerSelect, EmptyState, MoreRows, Notice, PageHeader, RefreshButton, Stat, StatStrip, TextLink,
  useProgressiveRows,
} from './ui'

interface Props {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  refreshKey: number
  /** deep link: open a match_ledger row focused in the Analyst queue */
  onOpenMatch?: (matchLedgerId: string) => void
}

type Category = 'ingest' | 'conflict' | 'run' | 'ledger' | 'decision' | 'config' | 'other'
type Actor = 'all' | 'user' | 'system'
type Window = 'all' | '24h' | '7d'
type Tab = 'feed' | 'record'

const CATEGORIES: Category[] = ['ingest', 'conflict', 'run', 'ledger', 'decision', 'config', 'other']

const CATEGORY_LABEL: Record<Category, string> = {
  ingest: 'Ingest', conflict: 'Conflict', run: 'Run', ledger: 'Ledger',
  decision: 'Decision', config: 'Config', other: 'Other',
}

/** event_type -> category (mirror of the backend logging taxonomy). */
function category(e: AuditEventRow): Category {
  const t = e.event_type
  if (t === 'gold.ingest_conflict') return 'conflict'
  if (t.startsWith('bronze.') || t.startsWith('silver.')
      || t === 'gold.rows_persisted' || t === 'gold.ingest_completed'
      || t === 'ingestion.completed') return 'ingest'
  if (t.startsWith('run.') || t.startsWith('pipeline.')) return 'run'
  if (t === 'ledger.finalized') return 'ledger'
  if (t.startsWith('ledger.match_')) return 'decision'
  if (t.startsWith('config.') || t === 'customer.created') return 'config'
  return 'other'
}

/** No auth yet: decisions and config edits are the human actions, the
 *  rest is the system doing its job. */
const actorOf = (c: Category): 'user' | 'system' =>
  c === 'decision' || c === 'config' ? 'user' : 'system'

/** pill + feed-icon tone per category (the kit's four tones) */
const CATEGORY_TONE: Record<Category, 'ok' | 'bad' | 'info' | 'warn' | 'neutral'> = {
  decision: 'ok',
  conflict: 'bad',
  run: 'info',
  ledger: 'info',
  config: 'warn',
  ingest: 'neutral',
  other: 'neutral',
}

/** Readable names for the audit vocabulary (CLAUDE.md taxonomy); any
 *  other code falls back to its last segment in words. The raw code is
 *  always shown beside it, so nothing is hidden. */
const EVENT_NAME: Record<string, string> = {
  'ingestion.completed': 'Documents ingested',
  'bronze.file_registered': 'File registered',
  'bronze.file_deduped': 'Duplicate file recognised',
  'silver.rows_persisted': 'Parsed rows stored',
  'gold.rows_persisted': 'Records stored',
  'gold.ingest_completed': 'Ingestion completed',
  'gold.ingest_conflict': 'Update to locked bill blocked',
  'gold.bills_merged': 'Duplicate bills merged',
  'run.started': 'Run started',
  'run.succeeded': 'Run succeeded',
  'run.failed': 'Run failed',
  'run.start_conflict': 'Run refused — one already running',
  'run.selfcheck_failed': 'Statement check failed',
  'run.parse_failed': 'Parse failed',
  'pipeline.selfcheck': 'Statement self-check',
  'ledger.finalized': 'Ledger updated',
  'ledger.match_accepted': 'Match accepted',
  'ledger.match_rejected': 'Match rejected',
  'ledger.match_unlocked': 'Match unlocked',
  'ledger.match_reopened': 'Match reopened',
  'ledger.match_created_manual': 'Manual match created',
  'run.reconcile_failed': 'Reconciliation failed',
  'run.selfcheck_mismatch': 'Statement totals mismatch',
  'run.selfcheck_error': 'Statement check errored',
  'pipeline.signal_coverage': 'Match signal missing',
  'config.rules_updated': 'Matching rules updated',
  'config.sources_updated': 'Document sources updated',
  'customer.created': 'Customer created',
}
function eventName(t: string): string {
  if (EVENT_NAME[t]) return EVENT_NAME[t]
  const last = t.split('.').pop() ?? t
  const words = last.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function within(w: Window, iso: string): boolean {
  if (w === 'all') return true
  const ms = w === '24h' ? 86_400_000 : 7 * 86_400_000
  return Date.now() - new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z').getTime() <= ms
}

function recordKey(e: AuditEventRow): string {
  if (e.entity_label) return e.entity_label
  if (e.entity_type && e.entity_id) return `${e.entity_type} ${e.entity_id}`
  if (e.run_id) return `run ${e.run_id.slice(0, 8)}`
  return 'system'
}

function detailsText(d: AuditEventRow['details']): string {
  if (!d) return ''
  return Object.entries(d)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ')
}

function CategoryChip({ c }: { c: Category }) {
  const tone = CATEGORY_TONE[c]
  return <span className={`ui-pill${tone === 'neutral' ? '' : ` tone-${tone}`}`}>{CATEGORY_LABEL[c]}</span>
}

export function AuditTrailView({ customers, customerId, onCustomerChange,
                                 refreshKey, onOpenMatch }: Props) {
  const [events, setEvents] = useState<AuditEventRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [actor, setActor] = useState<Actor>('all')
  const [win, setWin] = useState<Window>('all')
  const [tab, setTab] = useState<Tab>('feed')
  // single-select focus filter, consistent with Actor/Window
  const [cat, setCat] = useState<Category | 'all'>('all')
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  const load = useCallback(() => {
    setError(null)
    fetchAudit(customerId)
      .then(setEvents)
      .catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  useEffect(load, [load, refreshKey])

  const enriched = useMemo(
    () => (events ?? []).map((e) => ({ ...e, cat: category(e) })),
    [events])

  const counts = useMemo(() => {
    const c = Object.fromEntries(CATEGORIES.map((x) => [x, 0])) as Record<Category, number>
    enriched.forEach((e) => { c[e.cat] += 1 })
    return c
  }, [enriched])

  const kpis = useMemo(() => ({
    total: enriched.length,
    decisions: counts.decision,
    // distinct executions, not lifecycle events: incremental logs both
    // run.started + run.succeeded per run, snapshot logs run.succeeded only
    runs: new Set(enriched.filter((e) => e.cat === 'run' && e.run_id).map((e) => e.run_id)).size,
    ingests: counts.ingest,
    last24: enriched.filter((e) => within('24h', e.created_at)).length,
  }), [enriched, counts])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return enriched.filter((e) => {
      if (cat !== 'all' && e.cat !== cat) return false
      if (actor !== 'all' && actorOf(e.cat) !== actor) return false
      if (!within(win, e.created_at)) return false
      if (q) {
        const blob = `${e.event_type} ${e.entity_type ?? ''} ${e.entity_id ?? ''} `
          + `${e.entity_label ?? ''} ${detailsText(e.context ?? null)} `
          + `${e.run_id ?? ''} ${detailsText(e.details)}`
        if (!blob.toLowerCase().includes(q)) return false
      }
      return true
    })
  }, [enriched, cat, actor, win, query])

  const grouped = useMemo(() => {
    const groups = new Map<string, typeof filtered>()
    filtered.forEach((e) => {
      const k = recordKey(e)
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k)!.push(e)
    })
    // events oldest -> newest inside a group; groups by latest activity
    groups.forEach((list) => list.sort((a, b) => a.created_at.localeCompare(b.created_at)))
    return [...groups.entries()].sort((a, b) =>
      b[1][b[1].length - 1].created_at.localeCompare(a[1][a[1].length - 1].created_at))
  }, [filtered])

  const customerName = customers.find((c) => c.key === customerId)?.name ?? customerId
  const drawn = useProgressiveRows(filtered)
  const chips = [
    { key: 'q', label: 'Search', values: query ? [query] : [], onRemove: () => setQuery('') },
    { key: 'actor', label: 'Actor', values: actor === 'all' ? [] : [actor],
      format: (v: string) => (v === 'user' ? 'Human' : 'System'), onRemove: () => setActor('all') },
    { key: 'win', label: 'Window', values: win === 'all' ? [] : [win], onRemove: () => setWin('all') },
    { key: 'cat', label: 'Category', values: cat === 'all' ? [] : [cat],
      format: (v: string) => CATEGORY_LABEL[v as Category], onRemove: () => setCat('all') },
  ]
  const anyChip = chips.some((c) => c.values.length > 0)
  const clearAll = () => { setQuery(''); setActor('all'); setWin('all'); setCat('all') }
  const only = (c: Category) => { clearAll(); setCat(c) }

  return (
    <section className="ui-page">
      <PageHeader title="Audit trail"
                  context={customerName}>
        <CustomerSelect customers={customers} value={customerId} onChange={onCustomerChange} />
        <RefreshButton onClick={load} label="Refresh the audit trail" />
      </PageHeader>

      {error && (
        <Notice tone="error" action={<TextLink onClick={load}>Try again</TextLink>}>
          Could not load the audit trail: {error}
        </Notice>
      )}
      {!events && !error && <div className="ui-card sk" style={{ minHeight: 320 }} />}

      {events && (
        <>
          <StatStrip>
            <Stat label="Events" value={n(kpis.total)} 
                  onOpen={clearAll} title="Show every event" />
            <Stat label="Decisions" value={n(kpis.decisions)} sub="Match decisions"
                  onOpen={() => only('decision')} title="Show match decisions" />
            <Stat label="Runs" value={n(kpis.runs)} 
                  onOpen={() => only('run')} title="Show run events" />
            <Stat label="Ingest events" value={n(kpis.ingests)} 
                  onOpen={() => only('ingest')} title="Show ingestion events" />
            <Stat label="Last 24 hours" value={n(kpis.last24)} 
                  onOpen={() => { clearAll(); setWin('24h') }} title="Show the last 24 hours" />
          </StatStrip>

          <section className="ui-card">
            <div className="ui-tabbar">
              <div className="ui-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={tab === 'feed'}
                        className={`ui-tab${tab === 'feed' ? ' is-on' : ''}`} onClick={() => setTab('feed')}>
                  Activity feed <span className="ui-tab-count">{n(filtered.length)}</span>
                </button>
                <button type="button" role="tab" aria-selected={tab === 'record'}
                        className={`ui-tab${tab === 'record' ? ' is-on' : ''}`} onClick={() => setTab('record')}>
                  By record <span className="ui-tab-count">{n(grouped.length)}</span>
                </button>
              </div>
              {filtered.length !== enriched.length && (
                <span className="ui-tabbar-note">
                  {n(filtered.length)} of {n(enriched.length)} events
                  <TextLink onClick={clearAll}>Show all</TextLink>
                </span>
              )}
            </div>

            <div className="dt-tools audit-tools">
              <label className="dt-search">
                <Search size={14} strokeWidth={2} aria-hidden />
                <input type="search" placeholder="Search events"
                       aria-label="Search events" value={query} onChange={(e) => setQuery(e.target.value)} />
              </label>
              <span className="ui-seg" role="group" aria-label="Actor">
                {(['all', 'user', 'system'] as Actor[]).map((a) => (
                  <button key={a} type="button" className={actor === a ? 'on' : ''} onClick={() => setActor(a)}>
                    {a === 'all' ? 'Everyone' : a === 'user' ? 'Human' : 'System'}
                  </button>
                ))}
              </span>
              <span className="ui-seg" role="group" aria-label="Window">
                {(['all', '24h', '7d'] as Window[]).map((w) => (
                  <button key={w} type="button" className={win === w ? 'on' : ''} onClick={() => setWin(w)}>
                    {w === 'all' ? 'All time' : w === '24h' ? '24 hours' : '7 days'}
                  </button>
                ))}
              </span>
              <span className="audit-cats">
                {CATEGORIES.filter((c) => counts[c] > 0).map((c) => (
                  <button key={c} type="button"
                          className={`audit-cat cat-${c}${cat === c ? ' on' : ''}`}
                          onClick={() => setCat(cat === c ? 'all' : c)}>
                    {CATEGORY_LABEL[c]} <span className="audit-cat-n">{n(counts[c])}</span>
                  </button>
                ))}
              </span>
            </div>
            {anyChip && <div className="ui-filterbar"><FilterChips chips={chips} /></div>}

            {tab === 'feed' && (filtered.length === 0 ? (
              <EmptyState title="No events match these filters">
                <TextLink onClick={clearAll}>Clear all filters</TextLink>
              </EmptyState>
            ) : (
              <div className="ledger-wrap audit-wrap">
                <table className="ledger audit-table">
                  <thead>
                    <tr>
                      <th>When</th><th>Category</th><th>Event</th>
                      <th>Record</th><th>Run</th><th>Severity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drawn.shown.map((e) => (
                      <Fragment key={e.id}>
                      <tr className={`xq-row${expanded[e.id] ? ' open' : ''}`}
                          onClick={() => setExpanded((x) => ({ ...x, [e.id]: !x[e.id] }))}>
                        <td className="nowrap">
                          <ChevronRight className="chev chev-ic" size={14} strokeWidth={2} aria-hidden />
                          {fmtWhen(e.created_at)}
                        </td>
                        <td><CategoryChip c={e.cat} /></td>
                        <td>
                          <div className="party-cell">
                            <span className="audit-event">{eventName(e.event_type)}</span>
                            <span className="party-ref">{e.event_type}</span>
                          </div>
                        </td>
                        <td className="mono">
                          {e.entity_label
                            ? <span className="audit-entity">{e.entity_label}</span>
                            : e.entity_type
                              ? `${e.entity_type} ${(e.entity_id ?? '').slice(0, 12)}`
                              : '—'}
                        </td>
                        <td className="mono">{e.run_id ? e.run_id.slice(0, 8) : '—'}</td>
                        <td>{e.severity === 'INFO' ? <span className="muted">—</span>
                          : <span className={`ui-pill ${e.severity === 'WARNING' ? 'tone-warn' : 'tone-bad'}`}>
                              {e.severity.toLowerCase()}
                            </span>}</td>
                      </tr>
                      {expanded[e.id] && (
                        <tr className="xq-detail">
                          <td colSpan={6}>
                            <div className="detail-grid">
                              {e.context && Object.entries(e.context)
                                .filter(([, v]) => v !== null && v !== undefined)
                                .map(([k, v]) => (
                                <div key={`ctx-${k}`}>
                                  <div className="dt-label">{k.replace(/_/g, ' ')}</div>
                                  <div className="dt-value">{String(v)}</div>
                                </div>
                              ))}
                              {e.entity_label && e.entity_type && (
                                <div>
                                  <div className="dt-label">record</div>
                                  <div className="dt-value">{e.entity_type} {e.entity_id}</div>
                                </div>
                              )}
                              {e.details && Object.entries(e.details).map(([k, v]) => (
                                <div key={k}>
                                  <div className="dt-label">{k.replace(/_/g, ' ')}</div>
                                  <div className="dt-value">
                                    {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                                  </div>
                                </div>
                              ))}
                              {!e.details && !e.context && (
                                <p className="frame-note">No details.</p>
                              )}
                              {onOpenMatch && e.entity_type === 'match_ledger' && e.entity_id && (
                                <div>
                                  <button type="button" className="ui-btn is-sm"
                                          onClick={(ev) => { ev.stopPropagation(); onOpenMatch(e.entity_id!) }}>
                                    Open in Analyst queue <ArrowRight size={13} strokeWidth={2} />
                                  </button>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    ))}
                    <MoreRows remaining={drawn.remaining} onMore={drawn.more}
                              colSpan={6} noun="events" />
                  </tbody>
                </table>
              </div>
            ))}

            {tab === 'record' && (grouped.length === 0 ? (
              <EmptyState title="No records match these filters">
                <TextLink onClick={clearAll}>Clear all filters</TextLink>
              </EmptyState>
            ) : (
              <div className="audit-groups">
                {grouped.map(([key, evts]) => (
                  <div key={key} className="audit-group">
                    <div className="audit-group-head">
                      <span className="audit-group-key">{key}</span>
                      <span className="audit-group-meta">
                        {n(evts.length)} {evts.length === 1 ? 'event' : 'events'} ·{' '}
                        {fmtWhen(evts[0].created_at)} → {fmtWhen(evts[evts.length - 1].created_at)}
                      </span>
                    </div>
                    <ol className="ui-feed-list">
                      {evts.map((e) => (
                        <li key={e.id} className={`tone-${CATEGORY_TONE[e.cat]}`}>
                          <span className="ui-feed-icon"><span className="audit-dot" /></span>
                          <span className="ui-feed-body">
                            <span className="ui-feed-title">
                              {eventName(e.event_type)} <CategoryChip c={e.cat} />
                            </span>
                            {e.details && <span className="ui-feed-detail" title={detailsText(e.details)}>
                              {detailsText(e.details)}</span>}
                          </span>
                          <time className="ui-feed-when" dateTime={e.created_at}>{fmtWhen(e.created_at)}</time>
                        </li>
                      ))}
                    </ol>
                  </div>
                ))}
              </div>
            ))}
          </section>
        </>
      )}
    </section>
  )
}
