import { useEffect, useState } from 'react'
import {
  AlertTriangle, ArrowRight, Check, GitMerge, Link2, RotateCcw, Settings, Unlock, Upload, X,
} from 'lucide-react'
import type { AuditEventRow } from '../types'
import { fetchAudit } from '../api'
import { fmtWhen, parseUtc } from '../format'

/* The Command Center's "what changed since I last looked": the human-
   meaningful slice of the audit_log stream. Per-file bronze/silver/gold
   bookkeeping and per-row ingest conflicts are the system doing its job
   — the Audit trail keeps them; this feed names the ingestion, the run
   and the decision they belong to. Ids, counts and labels only, the same
   no-financial-content rule the audit log itself follows. */

type Tone = 'ok' | 'info' | 'warn' | 'bad'

interface Item {
  id: number
  at: string
  icon: typeof Upload
  tone: Tone
  title: string
  detail?: string
}

const SOURCE_NAME: Record<string, string> = {
  bank_statement: 'Bank statement',
  bill_status: 'Bill status export',
}
const sourceName = (t: string) =>
  SOURCE_NAME[t] ?? (t.startsWith('lineage') ? 'Lineage report' : t)

const FRAME_NOUN: Record<string, string> = {
  bank_txns: 'transactions', bills: 'bills', recoveries: 'recovery lines',
}

const num = (v: unknown) => (typeof v === 'number' ? v : 0)
const n = (v: number) => v.toLocaleString('en-IN')

function describe(e: AuditEventRow, finalized: Map<string, Record<string, unknown>>): Item | null {
  const d = (e.details ?? {}) as Record<string, unknown>
  const base = { id: e.id, at: e.created_at }
  const label = e.entity_label ?? 'Match'
  const bill = e.context?.bill_number ? `bill ${String(e.context.bill_number)}` : undefined
  switch (e.event_type) {
    case 'ingestion.completed': {
      const files = (d.files as Array<{ source_type: string }> | undefined) ?? []
      const stats = (d.stats ?? {}) as Record<string, unknown>
      const byFrame = (stats.by_frame ?? {}) as Record<string, Record<string, number>>
      const parts = Object.entries(byFrame)
        .filter(([k]) => FRAME_NOUN[k])
        .map(([k, f]) => `${n(num(f.inserted))} new ${FRAME_NOUN[k]}`)
      if (!parts.length && stats.rows_inserted !== undefined) parts.push(`${n(num(stats.rows_inserted))} new rows`)
      const conflicts = num(stats.conflicts)
      if (conflicts) parts.push(`${n(conflicts)} conflicts`)
      const names = [...new Set(files.map((f) => sourceName(f.source_type)))]
      return { ...base, icon: Upload, tone: conflicts ? 'warn' : 'info',
               title: `${names.join(' + ') || 'Documents'} ingested`, detail: parts.join(' · ') }
    }
    case 'run.succeeded': {
      const mode = String(d.mode ?? '')
      const f = e.run_id ? finalized.get(e.run_id) : undefined
      const detail = f
        ? [`${n(num(f.matches_created))} matches`, `${n(num(f.auto_locked))} auto-locked`,
           `${n(num(f.exceptions_opened))} exceptions opened`, `${n(num(f.exceptions_resolved))} resolved`].join(' · ')
        : undefined
      return { ...base, icon: GitMerge, tone: 'ok',
               title: `${mode ? mode[0].toUpperCase() + mode.slice(1) + ' r' : 'R'}econciliation completed`, detail }
    }
    case 'run.failed': case 'run.parse_failed': case 'run.selfcheck_failed':
      return { ...base, icon: AlertTriangle, tone: 'bad', title: 'Reconciliation failed',
               detail: e.event_type === 'run.selfcheck_failed' ? 'bank statement totals did not tie'
                 : e.event_type === 'run.parse_failed' ? 'a document could not be parsed' : undefined }
    case 'ledger.match_accepted':
      return { ...base, icon: Check, tone: 'ok', title: `${label} accepted`, detail: bill }
    case 'ledger.match_rejected':
      return { ...base, icon: X, tone: 'warn', title: `${label} rejected`, detail: bill }
    case 'ledger.match_superseded':
      return { ...base, icon: RotateCcw, tone: 'info', title: `${label} replaced`,
               detail: d.superseded_by_seq ? `by M-${String(d.superseded_by_seq)}` : undefined }
    case 'ledger.match_unlocked':
      return { ...base, icon: Unlock, tone: 'warn', title: `${label} unlocked`, detail: bill }
    case 'ledger.match_reopened':
      return { ...base, icon: RotateCcw, tone: 'info', title: `${label} reopened`, detail: bill }
    case 'ledger.match_created_manual':
      return { ...base, icon: Link2, tone: 'ok', title: `${label} matched manually`, detail: bill }
    case 'ledger.non_ireps_approved':
      return { ...base, icon: Check, tone: 'ok', title: 'Non-IREPS receipt approved' }
    case 'ledger.non_ireps_rejected':
      return { ...base, icon: RotateCcw, tone: 'info', title: 'Receipt marked as IREPS' }
    case 'ledger.credit_source_undone':
      return { ...base, icon: RotateCcw, tone: 'info', title: 'Source decision undone' }
    case 'ledger.credit_source_set': {
      const to = d.source === 'NON_IREPS' ? 'Non-IREPS' : d.source === 'IREPS' ? 'IREPS' : 'auto'
      return { ...base, icon: Settings, tone: 'info', title: `Credit source set to ${to}` }
    }
    case 'config.rules_updated':
      return { ...base, icon: Settings, tone: 'info', title: 'Matching config updated' }
    case 'config.sources_updated':
      return { ...base, icon: Settings, tone: 'info', title: 'Document sources updated' }
    case 'config.zones_updated':
      return { ...base, icon: Settings, tone: 'info', title: 'Zone directory updated' }
    case 'customer.created':
      return { ...base, icon: Settings, tone: 'info', title: 'Customer created' }
    default:
      return null
  }
}

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - parseUtc(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`
  if (s < 7 * 86_400) return `${Math.floor(s / 86_400)}d ago`
  return fmtWhen(iso)
}

export function RecentActivity({ customerId, refreshKey, onOpenAudit }: {
  customerId: string
  refreshKey: number
  onOpenAudit: () => void
}) {
  const [items, setItems] = useState<Item[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
    fetchAudit(customerId, 300)
      .then((rows) => {
        const finalized = new Map<string, Record<string, unknown>>()
        for (const r of rows) {
          if (r.event_type === 'ledger.finalized' && r.run_id && r.details) finalized.set(r.run_id, r.details)
        }
        setItems(rows.map((r) => {
          const item = describe(r, finalized)
          // who did it, when a signed-in user did
          return item && r.actor
            ? { ...item, detail: [item.detail, `by ${r.actor}`].filter(Boolean).join(' · ') }
            : item
        }).filter((i): i is Item => i !== null).slice(0, 25))
      })
      .catch(() => { setItems([]); setFailed(true) })
  }, [customerId, refreshKey])

  return (
    <section className="ui-card cc-activity">
      <header className="ui-card-head">
        <h3>Recent activity</h3>
        <button type="button" className="ui-link" onClick={onOpenAudit}>
          Audit trail <ArrowRight size={12} strokeWidth={2} />
        </button>
      </header>
      <div className="ui-fill">
        <div className="ui-fill-inner">
          {items === null ? (
            <p className="ui-feed-note">Loading…</p>
          ) : items.length === 0 ? (
            <p className="ui-feed-note">
              {failed ? 'Activity could not be loaded.' : 'No recent activity.'}
            </p>
          ) : (
            <ol className="ui-feed-list">
              {items.map((i) => {
                const Icon = i.icon
                return (
                  <li key={i.id} className={`tone-${i.tone}`}>
                    <span className="ui-feed-icon"><Icon size={13} strokeWidth={2} /></span>
                    <span className="ui-feed-body">
                      <span className="ui-feed-title">{i.title}</span>
                      {i.detail && <span className="ui-feed-detail">{i.detail}</span>}
                    </span>
                    <time className="ui-feed-when" dateTime={i.at} title={fmtWhen(i.at)}>
                      {ago(i.at)}
                    </time>
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      </div>
    </section>
  )
}
