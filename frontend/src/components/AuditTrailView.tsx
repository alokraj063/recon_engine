import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, RotateCw } from 'lucide-react'
import type { AuditEventRow, CustomerInfo } from '../types'
import { fetchAudit } from '../api'
import { fmtWhen } from '../format'

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

const CATEGORY_STAMP: Record<Category, string> = {
  decision: 'stamp-LOCKED',      // success tint
  conflict: 'stamp-BANK_ONLY',   // danger tint
  run: 'stamp-MATCH_REVIEW',     // info tint
  ledger: 'stamp-MATCH_REVIEW',
  config: 'stamp-BILL_ONLY',     // warning tint
  ingest: '',
  other: '',
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
  return <span className={`stamp ${CATEGORY_STAMP[c]}`}>{CATEGORY_LABEL[c]}</span>
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
    runs: counts.run,
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

  return (
    <section className="intake">
      <div className="ingest-head">
        <div>
          <h2>Audit trail</h2>
          <p className="strap-note">
            every ingest, run, decision and config change — written in the same
            transaction as the action itself
          </p>
        </div>
        <span className="cc-head-right">
          <label className="ctx-field">
            <span className="slot-label">Customer</span>
            <select value={customerId} onChange={(e) => onCustomerChange(e.target.value)}>
              {customers.map((c) => (
                <option key={c.key} value={c.key}>{c.name} ({c.key})</option>
              ))}
            </select>
          </label>
          <button className="btn-refresh btn-ic" onClick={load}>
            <RotateCw size={13} strokeWidth={1.75} /> refresh
          </button>
        </span>
      </div>

      {error && <p className="frame-note">could not load audit trail: {error}</p>}
      {!events && !error && <p className="frame-note"><span className="quill" /> loading…</p>}

      {events && (
        <>
          <div className="tiles cc-tiles audit-tiles">
            <div className="tile tone-neutral">
              <div className="tile-label">Events</div>
              <div className="tile-count">{kpis.total}</div>
              <div className="tile-amount">recorded for this customer</div>
            </div>
            <div className="tile">
              <div className="tile-label">Decisions</div>
              <div className="tile-count">{kpis.decisions}</div>
              <div className="tile-amount">accept / reject / unlock</div>
            </div>
            <div className="tile tone-review">
              <div className="tile-label">Runs</div>
              <div className="tile-count">{kpis.runs}</div>
              <div className="tile-amount">run lifecycle events</div>
            </div>
            <div className="tile tone-neutral">
              <div className="tile-label">Ingest</div>
              <div className="tile-count">{kpis.ingests}</div>
              <div className="tile-amount">bronze → silver → gold</div>
            </div>
            <div className="tile tone-neutral">
              <div className="tile-label">Last 24h</div>
              <div className="tile-count">{kpis.last24}</div>
              <div className="tile-amount">recent activity</div>
            </div>
          </div>

          <div className="cc-panel audit-filters">
            <div className="audit-filter-row">
              <input className="audit-search" placeholder="search event, entity, run, details…"
                     value={query} onChange={(e) => setQuery(e.target.value)} />
              <span className="audit-pills">
                <span className="slot-label">Actor</span>
                <span className="seg">
                  {(['all', 'user', 'system'] as Actor[]).map((a) => (
                    <button key={a} className={actor === a ? 'on' : ''}
                            onClick={() => setActor(a)}>
                      {a === 'all' ? 'All' : a === 'user' ? 'Human' : 'System'}
                    </button>
                  ))}
                </span>
              </span>
              <span className="audit-pills">
                <span className="slot-label">Window</span>
                <span className="seg">
                  {(['all', '24h', '7d'] as Window[]).map((w) => (
                    <button key={w} className={win === w ? 'on' : ''}
                            onClick={() => setWin(w)}>
                      {w === 'all' ? 'All' : w}
                    </button>
                  ))}
                </span>
              </span>
            </div>
            <div className="audit-cats">
              <span className="slot-label">Categories</span>
              <button className={`audit-cat${cat === 'all' ? ' on' : ''}`}
                      onClick={() => setCat('all')}>
                All <span className="audit-cat-n">{enriched.length}</span>
              </button>
              {CATEGORIES.filter((c) => counts[c] > 0).map((c) => (
                <button key={c}
                        className={`audit-cat${cat === c ? ' on' : ''}`}
                        onClick={() => setCat(cat === c ? 'all' : c)}>
                  {CATEGORY_LABEL[c]} <span className="audit-cat-n">{counts[c]}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="seg audit-tabs">
            <button className={tab === 'feed' ? 'on' : ''} onClick={() => setTab('feed')}>
              Activity feed ({filtered.length})
            </button>
            <button className={tab === 'record' ? 'on' : ''} onClick={() => setTab('record')}>
              By record ({grouped.length})
            </button>
          </div>

          {tab === 'feed' && (
            <div className="cc-panel">
              {filtered.length === 0 ? (
                <p className="frame-note">no events match the current filters</p>
              ) : (
                <table className="ledger">
                  <thead>
                    <tr>
                      <th>Time</th><th>Category</th><th>Event</th>
                      <th>Entity</th><th>Run</th><th>Severity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((e) => (
                      <Fragment key={e.id}>
                      <tr className={`xq-row${expanded[e.id] ? ' open' : ''}`}
                          onClick={() => setExpanded((x) => ({ ...x, [e.id]: !x[e.id] }))}>
                        <td className="mono-cell"><ChevronRight className="chev chev-ic" size={14} strokeWidth={2} aria-hidden /> {fmtWhen(e.created_at)}</td>
                        <td><CategoryChip c={e.cat} /></td>
                        <td className="mono-cell">{e.event_type}</td>
                        <td className="mono-cell">
                          {e.entity_label
                            ? <span className="audit-entity">{e.entity_label}</span>
                            : e.entity_type
                              ? `${e.entity_type} ${(e.entity_id ?? '').slice(0, 12)}`
                              : '—'}
                        </td>
                        <td className="mono-cell">{e.run_id ? e.run_id.slice(0, 8) : '—'}</td>
                        <td>{e.severity === 'INFO' ? '—'
                          : <span className="stamp stamp-BANK_ONLY">{e.severity}</span>}</td>
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
                                  <div className="dt-label">{k}</div>
                                  <div className="dt-value">
                                    {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                                  </div>
                                </div>
                              ))}
                              {!e.details && !e.context && (
                                <p className="frame-note">no detail payload</p>
                              )}
                              {onOpenMatch && e.entity_type === 'match_ledger' && e.entity_id && (
                                <div>
                                  <button className="btn-open"
                                          onClick={(ev) => { ev.stopPropagation(); onOpenMatch(e.entity_id!) }}>
                                    open in Analyst queue →
                                  </button>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {tab === 'record' && (
            <div className="audit-groups">
              {grouped.length === 0 ? (
                <p className="frame-note">no records match the current filters</p>
              ) : grouped.map(([key, evts]) => (
                <div key={key} className="cc-panel audit-group">
                  <div className="cc-panel-head">
                    <h3 className="ledger-h">
                      <span className="mono-cell">{key}</span>{' '}
                      <span className="chip-note">
                        {evts.length} event{evts.length === 1 ? '' : 's'} ·{' '}
                        {fmtWhen(evts[0].created_at)} → {fmtWhen(evts[evts.length - 1].created_at)}
                      </span>
                    </h3>
                  </div>
                  <div className="timeline audit-timeline">
                    {evts.map((e) => (
                      <div key={e.id} className="tl-event">
                        <span className="tl-date">{fmtWhen(e.created_at)}</span>
                        <span className={`tl-dot${e.cat === 'decision' ? '' :
                          e.cat === 'conflict' ? ' tl-returned' : ' tl-hollow'}`} />
                        <span className="tl-body">
                          <span className="tl-title">
                            {e.event_type} <CategoryChip c={e.cat} />
                          </span>
                          {e.details && <span className="tl-detail">{detailsText(e.details)}</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}
