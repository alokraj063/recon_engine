import { TriangleAlert } from 'lucide-react'
import type { FrameIngestStats, IngestStats } from '../types'

/** Human wording for a bronze file's dedup outcome (API values are
 *  frozen: `registered` | `deduped`). */
export function fileOutcomeLabel(outcome: string): string {
  if (outcome === 'registered') return 'new file'
  if (outcome === 'deduped') return 'identical file already ingested'
  return outcome
}

/** Two-word version for chips in dense tables; the long wording above
 *  stays on the tooltip. */
export function fileOutcomeShort(outcome: string): string {
  if (outcome === 'registered') return 'new'
  if (outcome === 'deduped') return 'already ingested'
  return outcome
}

/** Short role tag for a source_type / gold frame key: bank_statement ->
 *  BANK, bill_status -> BILLS, lineage_rnote -> RNOTE, lineage_grn -> GRN. */
export function sourceRoleTag(sourceType: string): string {
  if (sourceType === 'bank_statement' || sourceType === 'bank_txns') return 'BANK'
  if (sourceType === 'bill_status' || sourceType === 'bills') return 'BILLS'
  if (sourceType === 'recoveries') return 'RECOV'
  return sourceType.replace(/^lineage_?/, '').toUpperCase() || 'DOC'
}

/** Which counter a chip carries — the part of the composition bar it
 *  paints and the short label it gets in compact mode. `total` is the
 *  denominator (rows the file REPORTED), never a bar segment. */
type ChipKey = 'total' | 'inserted' | 'updated' | 'unchanged' | 'conflicts'

interface Chip {
  key: ChipKey
  label: string
  value: number
  tone?: 'total' | 'warn'
}

interface KindLine {
  frame: string
  title: string
  chips: Chip[]
}

/** Vocabulary per gold frame. Only the counts that can actually move for
 *  that kind are listed — bank transactions are never updated in place,
 *  recovery lines only ever ride in with a NEW bill, and conflicts exist
 *  for bills alone (a LOCKED bill refusing a newer export's change). */
function kindLine(frame: string, s: FrameIngestStats): KindLine {
  if (frame === 'bank_txns') {
    return {
      frame,
      title: 'Bank transactions',
      chips: [
        { key: 'total', label: 'Total transactions', value: s.reported, tone: 'total' },
        { key: 'inserted', label: 'New transactions', value: s.inserted },
        { key: 'unchanged', label: 'Duplicate transactions (not added)', value: s.unchanged },
      ],
    }
  }
  if (frame === 'bills') {
    return {
      frame,
      title: 'Bills',
      chips: [
        { key: 'total', label: 'Total bills', value: s.reported, tone: 'total' },
        { key: 'inserted', label: 'New bills', value: s.inserted },
        { key: 'updated', label: 'Updated bills', value: s.updated },
        { key: 'unchanged', label: 'Duplicate bills (not added)', value: s.unchanged },
        { key: 'conflicts', label: 'Conflicts', value: s.conflicts,
          tone: s.conflicts > 0 ? 'warn' : undefined },
      ],
    }
  }
  if (frame === 'recoveries') {
    return {
      frame,
      title: 'Recovery lines',
      chips: [{ key: 'inserted', label: 'New recovery lines', value: s.inserted }],
    }
  }
  // lineage_<slot> — any upstream document kind (RNOTE, CRN, GRN, …)
  const doc = frame.replace(/^lineage_?/, '').toUpperCase() || 'LINEAGE'
  return {
    frame,
    title: `${doc} documents`,
    chips: [
      { key: 'total', label: 'Total documents', value: s.reported, tone: 'total' },
      { key: 'inserted', label: 'New documents', value: s.inserted },
      { key: 'unchanged', label: 'Duplicate documents (not added)', value: s.unchanged },
    ],
  }
}

const KIND_ORDER = ['bank_txns', 'bills', 'recoveries']
const frameRank = (f: string) => {
  const i = KIND_ORDER.indexOf(f)
  return i === -1 ? KIND_ORDER.length : i
}

/** Ingestions recorded before the per-frame breakdown existed only carry
 *  the flat counters — say the same things in the same words, just
 *  without naming the entity kind. */
function flatLine(s: IngestStats): KindLine {
  const chips: Chip[] = []
  if (s.rows_reported !== undefined) {
    chips.push({ key: 'total', label: 'Total rows', value: s.rows_reported, tone: 'total' })
  }
  chips.push(
    { key: 'inserted', label: 'New rows', value: s.rows_inserted },
    { key: 'updated', label: 'Updated bills', value: s.bills_updated },
    { key: 'unchanged', label: 'Duplicates (not added)', value: s.rows_reused },
    { key: 'conflicts', label: 'Conflicts', value: s.conflicts,
      tone: s.conflicts > 0 ? 'warn' : undefined },
  )
  return { frame: 'all', title: 'All rows', chips }
}

function linesOf(stats: IngestStats): KindLine[] {
  const by = stats.by_frame
  if (by && Object.keys(by).length > 0) {
    return Object.keys(by)
      .sort((a, b) => frameRank(a) - frameRank(b) || a.localeCompare(b))
      .map((f) => kindLine(f, by[f]))
  }
  return [flatLine(stats)]
}

/* ---- compact (table-cell) rendering: stat strip + composition bar ---- */

const SHORT_LABEL: Record<ChipKey, string> = {
  total: 'total',
  inserted: 'new',
  updated: 'updated',
  unchanged: 'duplicate',
  conflicts: 'conflicts',
}

/** Bar segments in paint order. Duplicates are the neutral track — what
 *  CHANGED is the subject, what was already there is context. Conflicts
 *  are a status and only ever painted when > 0. */
const SEGMENT_KEYS: ChipKey[] = ['inserted', 'updated', 'unchanged', 'conflicts']

const fmtInt = (n: number) => n.toLocaleString('en-IN')

/** One thin part-to-whole bar: each part's width is its share of `total`.
 *  Segments are separated by a surface-colour gap (CSS), never a stroke.
 *  A total of 0 renders the empty track; a MISSING total (pre-
 *  rows_reported ingestions) gets no bar at all — see StatStrip. */
function CompositionBar({ total, parts, title }: {
  total: number
  parts: Chip[]
  title: string
}) {
  const denom = total > 0 ? total : 0
  const drawn = parts.filter((p) => p.value > 0 && denom > 0)
  return (
    <div className="ingest-bar" title={title} role="img" aria-label={title}>
      {drawn.map((p) => (
        <span key={p.key}
              className={`ingest-bar-seg seg-${p.key}`}
              style={{ flexGrow: p.value / denom }} />
      ))}
      {drawn.length === 0 && <span className="ingest-bar-seg seg-empty" />}
    </div>
  )
}

function stripSentence(line: KindLine, total: Chip | undefined, parts: Chip[]): string {
  const head = total
    ? `${fmtInt(total.value)} ${line.title.toLowerCase()}`
    : line.title
  const tail = parts.map((p) => `${fmtInt(p.value)} ${p.label.toLowerCase()}`).join(', ')
  return tail ? `${head}: ${tail}` : head
}

function StatStrip({ line }: { line: KindLine }) {
  const total = line.chips.find((c) => c.key === 'total')
  const parts = line.chips.filter((c) => c.key !== 'total')
  // Recovery lines carry only an inserted count: it IS the headline, and
  // a one-part bar would say nothing.
  const headlineOnly = !total && parts.length === 1
  const headline = total ?? (headlineOnly ? parts[0] : undefined)
  const sentence = stripSentence(line, total, parts)
  return (
    <div className="ingest-strip">
      <span className="ingest-kind-title">{line.title}</span>
      <span className={`ingest-total${headline && headline.value === 0 ? ' zero' : ''}`}
            title={headline ? headline.label : 'total not recorded for this ingestion'}>
        {headline ? fmtInt(headline.value) : '—'}
      </span>
      {headlineOnly || !total
        ? <span className="ingest-bar-slot" />
        : <CompositionBar total={total.value}
                          parts={SEGMENT_KEYS.flatMap((k) => parts.filter((p) => p.key === k))}
                          title={sentence} />}
      <span className="ingest-parts">
        {!headlineOnly && parts.map((p) => {
          const warn = p.key === 'conflicts' && p.value > 0
          return (
            <span key={p.key}
                  className={`ingest-part part-${p.key}${p.value === 0 ? ' zero' : ''}${warn ? ' warn' : ''}`}
                  title={`${p.label}: ${fmtInt(p.value)}`}>
              {warn
                ? <TriangleAlert size={11} strokeWidth={2} className="part-icon" />
                : <span className="dot" />}
              <span className="part-value">{fmtInt(p.value)}</span>
              <span className="part-label">{SHORT_LABEL[p.key]}</span>
            </span>
          )
        })}
      </span>
    </div>
  )
}

interface Props {
  stats: IngestStats | null | undefined
  /** one stat strip per kind (table cells) instead of chip rows */
  compact?: boolean
  className?: string
}

/** What an ingestion did to gold, one line per entity kind — the single
 *  place the "Total bills / New bills / Duplicate bills (not added)"
 *  vocabulary lives (Ingest result card, All ingestions table, run
 *  summary). */
export function IngestStatsSummary({ stats, compact, className }: Props) {
  if (!stats) return compact ? <>—</> : null
  const lines = linesOf(stats)

  if (compact) {
    return (
      <div className={`ingest-kinds compact${className ? ` ${className}` : ''}`}>
        {lines.map((l) => <StatStrip key={l.frame} line={l} />)}
      </div>
    )
  }

  return (
    <div className={`ingest-kinds${className ? ` ${className}` : ''}`}>
      {lines.map((l) => (
        <div key={l.frame} className="ingest-kind">
          <span className="ingest-kind-title">{l.title}</span>
          <div className="stat-chips">
            {l.chips.map((c) => (
              <span key={c.label}
                    className={`chip${c.tone === 'total' ? ' chip-settled' : ''}${c.tone === 'warn' ? ' chip-attempts' : ''}`}>
                {c.label} {c.value}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
