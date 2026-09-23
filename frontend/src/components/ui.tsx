import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, Info, RotateCw } from 'lucide-react'
import type { CustomerInfo } from '../types'
import { n, pct, plural } from '../format'
import { COLUMN_HELP } from '../columnHelp'

/* The shared page kit — the Command Center's visual language, used by
   every page so the app reads as one product. Styles live in the
   "UI kit" section of styles.css (ui-* classes). Keep these small and
   dumb: layout and tone only, never data fetching or business rules. */

/** One page: a header, then a vertical stack of cards and notices. */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={`ui-page${className ? ` ${className}` : ''}`}>{children}</section>
}

/** The page head: title + a quiet context line on the left, tools on the
 *  right (filters, then refresh, a separator, then actions — primary last). */
export function PageHeader({ title, context, children }: {
  title: ReactNode
  context?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="ui-head">
      <div className="ui-head-title">
        <h2 className="page-title">{title}</h2>
        {context && <p className="ui-context">{context}</p>}
      </div>
      {children && <div className="ui-head-tools">{children}</div>}
    </header>
  )
}

/** the thin rule between a head's filters and its actions */
export const ToolSep = () => <span className="ui-head-sep" />

/** the small dot between items of a context line */
export const Dot = () => <span className="ui-dot" />

export function RefreshButton({ onClick, loading, label = 'Refresh' }: {
  onClick: () => void; loading?: boolean; label?: string
}) {
  return (
    <button type="button" className="ui-icon-btn" onClick={onClick} title={label} aria-label={label}>
      <RotateCw size={15} strokeWidth={1.75} className={loading ? 'spin' : undefined} />
    </button>
  )
}

/** Customer switcher — renders nothing when there is only one customer. */
export function CustomerSelect({ customers, value, onChange }: {
  customers: CustomerInfo[]; value: string; onChange: (key: string) => void
}) {
  if (customers.length <= 1) return null
  return (
    <label className="ui-customer">
      <span>Customer</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {customers.map((c) => (
          <option key={c.key} value={c.key}>{c.name} ({c.key})</option>
        ))}
      </select>
    </label>
  )
}

/** A white card with an optional head (title, sub-line, right-side action). */
export function Card({ title, sub, action, children, className, ruled, id }: {
  title?: ReactNode
  sub?: ReactNode
  action?: ReactNode
  children?: ReactNode
  className?: string
  /** draw a rule under the head (cards whose body is a table or list) */
  ruled?: boolean
  id?: string
}) {
  return (
    <section id={id} className={`ui-card${ruled ? ' is-ruled' : ''}${className ? ` ${className}` : ''}`}>
      {(title || action) && (
        <header className="ui-card-head">
          <div className="ui-card-title">
            {title && <h3>{title}</h3>}
            {sub && <span className="ui-card-sub">{sub}</span>}
          </div>
          {action && <div className="ui-card-action">{action}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

/** A small inline link — teal, so it always reads as one. */
export function TextLink({ children, onClick, title, quiet }: {
  children: ReactNode; onClick: () => void; title?: string
  /** keeps the surrounding text colour until hovered (figures inside prose) */
  quiet?: boolean
}) {
  return (
    <button type="button" className={`ui-link${quiet ? ' is-quiet' : ''}`}
            onClick={onClick} title={title}>
      {children}
    </button>
  )
}

export function Notice({ tone = 'info', children, action }: {
  tone?: 'info' | 'warn' | 'error' | 'ok'
  children: ReactNode
  action?: ReactNode
}) {
  const Icon = tone === 'info' ? Info : tone === 'ok' ? CheckCircle2 : AlertTriangle
  return (
    <div className={`ui-notice is-${tone}`} role={tone === 'error' ? 'alert' : undefined}>
      <Icon size={15} strokeWidth={2} />
      <div className="ui-notice-body">{children}</div>
      {action}
    </div>
  )
}

export function EmptyState({ icon, title, children }: {
  icon?: ReactNode; title: ReactNode; children?: ReactNode
}) {
  return (
    <div className="ui-empty">
      {icon}
      <strong>{title}</strong>
      {children}
    </div>
  )
}

/** Placeholder cards while the first load is in flight. */
export function SkeletonCards({ heights }: { heights: number[] }) {
  return (
    <div className="ui-stack" aria-busy="true" aria-label="Loading">
      {heights.map((h, i) => <div key={i} className="ui-card sk" style={{ minHeight: h }} />)}
    </div>
  )
}

/** A headline figure: eyebrow label, big number, a quiet sub-line.
 *  Clickable when onOpen is given — the figure opens its own rows. */
/** What a figure portrays, in the Command Center's colours (settled
 *  green, review amber, unmatched red, awaiting slate) plus other
 *  receipts (violet): a top rule, a tinted label and value. */
export type StatAccent = 'settled' | 'review' | 'open' | 'awaiting' | 'other'

export function Stat({ label, value, sub, tone, accent, onOpen, title }: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  tone?: 'ok' | 'warn' | 'bad' | 'info'
  accent?: StatAccent
  onOpen?: () => void
  title?: string
}) {
  const body = (
    <>
      <span className="ui-eyebrow">{label}</span>
      <span className="ui-stat-value">{value}</span>
      {sub && <span className="ui-stat-sub">{sub}</span>}
    </>
  )
  const cls = `ui-stat${tone ? ` tone-${tone}` : ''}${accent ? ` accent-${accent}` : ''}`
  return onOpen ? (
    <button type="button" className={`${cls} is-link`} onClick={onOpen} title={title}>{body}</button>
  ) : (
    <div className={cls} title={title}>{body}</div>
  )
}

/** A row of Stats sharing one card, separated by rules. */
export function StatStrip({ children }: { children: ReactNode }) {
  return <div className="ui-card ui-stats">{children}</div>
}

export interface Part {
  key: string
  label: string
  count: number
  /** colour class: settled | review | open | awaiting | info | neutral | a1..a4 */
  tone: string
  onOpen?: () => void
  /** the hover text on the bar segment and legend item */
  title?: string
}

/** One partition drawn as a bar + legend. The legend names each part and
 *  its share (identity is always in the text, never in colour alone);
 *  exact counts ride in the hover title. */
export function PartitionBar({ parts, unit, showCount }: {
  parts: Part[]
  unit: [string, string]
  /** show the count beside the share in the legend */
  showCount?: boolean
}) {
  const total = parts.reduce((s, p) => s + p.count, 0)
  const hover = (p: Part) => p.title ?? `${p.label} · ${n(p.count)} ${plural(p.count, unit[0], unit[1])}`
  return (
    <>
      <div className="ui-bar" role="img"
           aria-label={parts.map((p) => `${p.label} ${p.count}`).join(', ')}>
        {parts.filter((p) => p.count > 0).map((p) => (
          <span key={p.key} className={`seg seg-${p.tone}`} style={{ flexGrow: p.count }} title={hover(p)} />
        ))}
      </div>
      <div className="ui-legend">
        {parts.map((p) => {
          const inner = (
            <>
              <span className={`ui-swatch sw-${p.tone}`} />
              {p.label}
              <span className="ui-legend-pct">
                {showCount && <>{n(p.count)}{' · '}</>}
                {total > 0 ? pct(p.count / total) : '—'}
              </span>
            </>
          )
          return p.onOpen ? (
            <button key={p.key} type="button" className="ui-legend-item" onClick={p.onOpen} title={hover(p)}>
              {inner}
            </button>
          ) : (
            <span key={p.key} className="ui-legend-item is-static" title={hover(p)}>{inner}</span>
          )
        })}
      </div>
    </>
  )
}

/* ---- progressive rows ------------------------------------------------
   Big tables (2,000+ rows) froze the tab while React drew every row at
   once. These render the first BATCH rows, then the next batch whenever
   the bottom of the table scrolls into view — sorting, filtering and
   expanding still act on the whole set; only the drawing is deferred. */

const BATCH = 150

/** The first `limit` rows of `rows`, growing on demand. The limit resets
 *  when the set itself changes (a filter, a sort, a new load) — keyed on
 *  its length and first row so an unrelated re-render never resets it. */
export function useProgressiveRows<T>(rows: T[]) {
  const [limit, setLimit] = useState(BATCH)
  const first = rows[0]
  useEffect(() => { setLimit(BATCH) }, [rows.length, first])
  // stable identities: MoreRows and deep-link effects depend on them
  const more = useCallback(() => setLimit((l) => l + BATCH), [])
  const reveal = useCallback((count: number) => setLimit((l) => Math.max(l, count)), [])
  return {
    shown: rows.length > limit ? rows.slice(0, limit) : rows,
    remaining: Math.max(0, rows.length - limit),
    more,
    /** make sure the first `count` rows are drawn (a deep link to row N) */
    reveal,
  }
}

/** The last row of a progressively drawn table: loads the next batch as
 *  it scrolls into view (IntersectionObserver clips by every scrolling
 *  ancestor, so it works inside the tables' own scroll boxes too), with
 *  a button as the fallback. Renders nothing once every row is drawn. */
export function MoreRows({ remaining, colSpan, onMore, noun = 'rows' }: {
  remaining: number
  colSpan: number
  onMore: () => void
  noun?: string
}) {
  const ref = useRef<HTMLTableCellElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || remaining <= 0) return
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) onMore()
    }, { rootMargin: '300px' })
    io.observe(el)
    return () => io.disconnect()
  }, [remaining, onMore])
  if (remaining <= 0) return null
  return (
    <tr className="ui-more-row">
      <td ref={ref} colSpan={colSpan}>
        <button type="button" className="ui-link" onClick={onMore}>
          Show more — {n(remaining)} {noun} not drawn yet
        </button>
      </td>
    </tr>
  )
}

/** One button of a DecisionDialog. `tone` picks its look: primary (the
 *  default action, Ctrl+Enter), danger, or plain. */
export interface DialogAction {
  label: string
  tone?: 'primary' | 'danger' | 'plain'
  run: (note: string) => void | Promise<void>
}

export const NOTE_MAX = 500

/** A small modal for a decision that deserves a moment: a title, one line
 *  on what will happen, an optional note, and the actions. Esc or the
 *  backdrop cancels; Ctrl/⌘+Enter runs the first primary action. The only
 *  modal in the app — use it sparingly (unlock/reopen, or a decision the
 *  analyst chose to annotate), never in front of every click. Portalled to
 *  <body>: its hosts sit in sticky table cells, whose stacking context
 *  would otherwise draw the table over it. */
export function DecisionDialog({ title, message, actions, onClose, busy, withNote = true,
                                 notePlaceholder = 'Add a note (optional)' }: {
  title: ReactNode
  message?: ReactNode
  actions: DialogAction[]
  onClose: () => void
  busy?: boolean
  withNote?: boolean
  notePlaceholder?: string
}) {
  const [note, setNote] = useState('')
  const noteRef = useRef<HTMLTextAreaElement>(null)
  const primary = actions.find((a) => a.tone === 'primary') ?? actions[0]
  useEffect(() => {
    noteRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const run = (a: DialogAction) => { if (!busy) void a.run(note.trim()) }
  return createPortal(
    <div className="ui-dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ui-dialog" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}
           onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && primary) run(primary) }}>
        <h3 className="ui-dialog-title">{title}</h3>
        {message && <div className="ui-dialog-msg">{message}</div>}
        {withNote && (
          <label className="ui-dialog-note">
            <textarea ref={noteRef} rows={3} maxLength={NOTE_MAX} value={note}
                      placeholder={notePlaceholder} onChange={(e) => setNote(e.target.value)} />
            <span className="ui-dialog-count">{note.length}/{NOTE_MAX}</span>
          </label>
        )}
        <div className="ui-dialog-actions">
          <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>Cancel</button>
          {actions.map((a) => (
            <button key={a.label} type="button" disabled={busy} onClick={() => run(a)}
                    className={`ui-btn${a.tone === 'primary' ? ' ui-btn-primary'
                      : a.tone === 'danger' ? ' ui-btn-danger' : ''}`}>
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** A table header's text with its description (columnHelp.ts) on hover —
 *  `k` is the column key, or a `ui:` key for a header that is not a data
 *  column. With no description written it renders the text plainly. */
export function HelpLabel({ k, children }: { k: string; children: ReactNode }) {
  const help = COLUMN_HELP[k]
  return help ? <span className="has-help" title={help}>{children}</span> : <>{children}</>
}
