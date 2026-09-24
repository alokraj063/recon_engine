import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, ChevronRight, Search } from 'lucide-react'
import { FilterChips } from './filters/FilterChips'
import { buildOptions } from './filters/facets'
import type { AuditEventRow, CustomerInfo } from '../types'
import { fetchAudit } from '../api'
import { fmtWhen, n } from '../format'
import {
  CustomerSelect, EmptyState, HelpLabel, MoreRows, Notice, PageHeader, RefreshButton, Stat, StatStrip,
  TextLink, useProgressiveRows,
} from './ui'
import { ColumnFilter } from './filters/ColumnFilter'

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
  // every analyst decision on the ledger: matches, and a credit's source
  if (t.startsWith('ledger.match_') || t.startsWith('ledger.non_ireps_')
      || t.startsWith('ledger.credit_source_')) return 'decision'
  if (t.startsWith('config.') || t.startsWith('customer.')
      || t.startsWith('user.') || t === 'auth.admin_seeded') return 'config'
  return 'other'
}

/** A signed-in user's request stamps its events with the user. Events
 *  from before users were recorded carry none: there, decisions and
 *  config edits were still the human actions. */
const actorOf = (e: AuditEventRow & { cat: Category }): 'user' | 'system' =>
  e.actor_user_id != null || e.cat === 'decision' || e.cat === 'config' ? 'user' : 'system'

/** Who, as the User column shows it: the name, System for the platform's
 *  own work, "Not recorded" for a human action older than user tracking. */
function actorName(e: AuditEventRow & { cat: Category }): string {
  if (e.actor) return e.actor
  if (e.actor_user_id != null) return `User #${e.actor_user_id}`
  // account changes made outside the app: the operator is not a signed-in user
  if (e.details?.via === 'cli') return 'Command line'
  if (e.details?.via === 'seed') return 'System'
  return actorOf(e) === 'user' ? 'Not recorded' : 'System'
}

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
  'ledger.match_superseded': 'Match replaced',
  'ledger.match_reopened': 'Match reopened',
  'ledger.match_created_manual': 'Manual match created',
  'ledger.non_ireps_approved': 'Non-IREPS receipt approved',
  'ledger.non_ireps_rejected': 'Marked as IREPS',
  'ledger.credit_source_undone': 'Source decision undone',
  'ledger.credit_source_set': 'Credit source changed',
  'run.reconcile_failed': 'Reconciliation failed',
  'run.selfcheck_mismatch': 'Statement totals mismatch',
  'run.selfcheck_error': 'Statement check errored',
  'pipeline.signal_coverage': 'Match signal missing',
  'config.rules_updated': 'Matching rules updated',
  'config.sources_updated': 'Document sources updated',
  'customer.created': 'Customer created',
  'config.rules_update_rejected': 'Matching rules change refused',
  'config.sources_update_rejected': 'Document sources change refused',
  'customer.create_rejected': 'Customer creation refused',
  'customer.data_wiped': 'Customer data wiped',
  'user.created': 'User created',
  'user.updated': 'User updated',
  'user.activated': 'User access restored',
  'user.deactivated': 'User access revoked',
  'auth.admin_seeded': 'First admin created',
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

/** What an audit record IS, in business terms (never a table name). */
const ENTITY_NAME: Record<string, string> = {
  match_ledger: 'Match',
  gold_bank_txn: 'Credit',
  gold_bill: 'Bill',
  exception_ledger: 'Exception',
  bronze_file: 'File',
  match_rule_set: 'Matching config',
  source_config: 'Source setup',
  customer: 'Customer',
  user: 'User',
}

/** The record an event concerns, as people say it: "Match M-42",
 *  "Credit · HSBC123", "File · statement.pdf", "Run · 1a2b3c4d". */
function recordKey(e: AuditEventRow): string {
  const kind = e.entity_type ? (ENTITY_NAME[e.entity_type] ?? e.entity_type.replace(/_/g, ' ')) : null
  if (e.entity_label) {
    if (e.entity_type === 'match_ledger') return `Match ${e.entity_label}`
    if (e.entity_type === 'bronze_file') return `File · ${e.entity_label}`
    return e.entity_label
  }
  if (kind && e.entity_id) return `${kind} · ${e.entity_id.slice(0, 8)}`
  if (kind) return e.event_type.endsWith('_bulk') || e.details?.bulk ? `${kind}s (bulk)` : kind
  if (e.run_id) return `Run · ${e.run_id.slice(0, 8)}`
  // an ingestion summary concerns the upload as a whole, not one record
  if (e.event_type === 'ingestion.completed' || e.event_type.startsWith('gold.ingest')) return 'Ingestion'
  return 'System'
}

/** Readable names for the detail keys events carry; others are spelled out. */
const DETAIL_LABEL: Record<string, string> = {
  was: 'Was',
  source: 'Now',
  note: 'Note',
  via: 'Changed from',
  confidence: 'Confidence',
  exceptions_resolved: 'Exceptions closed',
  was_confidence: 'Was',
  superseded_by_seq: 'Replaced by match',
  exceptions_reopened: 'Exceptions reopened',
  user_overrode_pick: 'Different bill chosen',
  was_locked_by: 'Was locked by',
  bill_count: 'Bills',
  has_note: 'Has note',
  approved: 'Approved',
  requested: 'Requested',
  changes: 'Changes',
  signals: 'Match signals',
  copy_overridden: 'Custom wording',
  params_slots: 'Slots with settings',
  sources: 'Sources',
  bronze_file_id: 'File #',
  changed_field_names: 'Changed fields',
  rows_reported: 'Rows reported',
  rows_inserted: 'Rows added',
  changed_fields: 'Changed fields',
  error: 'Refused as',
  reason: 'Reason',
  deleted: 'Rows deleted',
  kept: 'Kept',
}
// shown inside another key (was_auto rides with "was") or not at all
const DETAIL_HIDDEN = new Set(['was_auto', 'bulk', 'user_id'])

const VALUE_TEXT: Record<string, string> = {
  IREPS: 'IREPS', NON_IREPS: 'Non-IREPS', AUTO_HIGH: 'System (high confidence)',
  USER: 'User', bank: 'Bank transactions', cli: 'Command line',
  seed: 'First-boot setup',
}

const words = (k: string) => {
  const w = k.replace(/_/g, ' ')
  return w.charAt(0).toUpperCase() + w.slice(1)
}
/** "weights.zone" -> "Weights › Zone" */
const fieldName = (path: string) => path.split('.').map(words).join(' › ')

function valueText(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'string') return VALUE_TEXT[v] ?? v
  if (Array.isArray(v)) return v.map(valueText).join(', ') || '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

interface Change { field: string; from: unknown; to: unknown }

/** An event's details as [label, text] pairs, in reading order. */
function detailPairs(d: AuditEventRow['details']): Array<[string, string]> {
  if (!d) return []
  const out: Array<[string, string]> = []
  for (const [k, v] of Object.entries(d)) {
    if (DETAIL_HIDDEN.has(k)) continue
    if (k === 'note' && (v === null || v === '')) continue
    if (k === 'changes' && Array.isArray(v)) {
      (v as Change[]).forEach((c) => out.push([fieldName(c.field),
                                               `${valueText(c.from)} → ${valueText(c.to)}`]))
      continue
    }
    let text = valueText(v)
    if (k === 'was' && d.was_auto) text += ' (automatic)'
    out.push([DETAIL_LABEL[k] ?? words(k), text])
  }
  return out
}

function detailsText(d: AuditEventRow['details']): string {
  return detailPairs(d).map(([k, v]) => `${k}: ${v}`).join(' · ')
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
  const [users, setUsers] = useState<string[]>([])
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
      if (actor !== 'all' && actorOf(e) !== actor) return false
      if (users.length && !users.includes(actorName(e))) return false
      if (!within(win, e.created_at)) return false
      if (q) {
        const blob = `${e.event_type} ${eventName(e.event_type)} ${recordKey(e)} ${e.entity_id ?? ''} `
          + `${actorName(e)} ${detailsText(e.context ?? null)} `
          + `${e.run_id ?? ''} ${detailsText(e.details)}`
        if (!blob.toLowerCase().includes(q)) return false
      }
      return true
    })
  }, [enriched, cat, actor, win, query, users])

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
    { key: 'users', label: 'User', values: users, onRemove: () => setUsers([]) },
    { key: 'win', label: 'Window', values: win === 'all' ? [] : [win], onRemove: () => setWin('all') },
    { key: 'cat', label: 'Category', values: cat === 'all' ? [] : [cat],
      format: (v: string) => CATEGORY_LABEL[v as Category], onRemove: () => setCat('all') },
  ]
  const anyChip = chips.some((c) => c.values.length > 0)
  const clearAll = () => { setQuery(''); setActor('all'); setWin('all'); setCat('all'); setUsers([]) }
  const only = (c: Category) => { clearAll(); setCat(c) }

  return (
    <section className="ui-page is-fill">
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

          <section className="ui-card is-fill">
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
              <div className="ledger-wrap is-fill audit-wrap">
                <table className="ledger audit-table">
                  <thead>
                    <tr>
                      <th><HelpLabel k="ui:audit_when">When</HelpLabel></th>
                      <th>
                        <HelpLabel k="ui:audit_user">User</HelpLabel>
                        <ColumnFilter label="User" value={users} onApply={setUsers}
                                      options={buildOptions(enriched, actorName)} />
                      </th>
                      <th><HelpLabel k="ui:audit_category">Category</HelpLabel></th>
                      <th><HelpLabel k="ui:audit_event">Event</HelpLabel></th>
                      <th><HelpLabel k="ui:audit_record">Record</HelpLabel></th>
                      <th><HelpLabel k="ui:run">Run</HelpLabel></th>
                      <th><HelpLabel k="ui:audit_severity">Severity</HelpLabel></th>
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
                        <td className={`nowrap${e.actor ? '' : ' muted'}`}>{actorName(e)}</td>
                        <td><CategoryChip c={e.cat} /></td>
                        <td>
                          <div className="party-cell">
                            <span className="audit-event">{eventName(e.event_type)}</span>
                            <span className="party-ref">{e.event_type}</span>
                          </div>
                        </td>
                        <td><span className="audit-entity">{recordKey(e)}</span></td>
                        <td className="mono">{e.run_id ? e.run_id.slice(0, 8) : '—'}</td>
                        <td>{e.severity === 'INFO' ? <span className="muted">—</span>
                          : <span className={`ui-pill ${e.severity === 'WARNING' ? 'tone-warn' : 'tone-bad'}`}>
                              {e.severity.toLowerCase()}
                            </span>}</td>
                      </tr>
                      {expanded[e.id] && (
                        <tr className="xq-detail">
                          <td colSpan={7}>
                            <div className="detail-grid">
                              <div>
                                <div className="dt-label">User</div>
                                <div className="dt-value">{actorName(e)}</div>
                              </div>
                              {e.context && Object.entries(e.context)
                                .filter(([, v]) => v !== null && v !== undefined)
                                .map(([k, v]) => (
                                <div key={`ctx-${k}`}>
                                  <div className="dt-label">{words(k)}</div>
                                  <div className="dt-value">{valueText(v)}</div>
                                </div>
                              ))}
                              {detailPairs(e.details).map(([k, v], i) => (
                                <div key={`${k}-${i}`}>
                                  <div className="dt-label">{k}</div>
                                  <div className="dt-value">{v}</div>
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
                              colSpan={7} noun="events" />
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
                              <span className="decision-who"> · {actorName(e)}</span>
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
