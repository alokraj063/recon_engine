import { ArrowRight, Inbox } from 'lucide-react'
import type { RunListItem } from '../types'
import { runLabelFor } from './RunFilter'
import { EmptyState, TextLink } from './ui'

/**
 * Empty-ledger guidance that names WHY the ledger is empty. AR
 * Reconciliation and the Analyst queue read the durable match/exception
 * ledgers, which only INCREMENTAL runs feed — a snapshot run stores its
 * own result and nothing else. Rendered by those pages (inside a card)
 * in place of their tables; `runs` is the customer's run list, newest first.
 */
export function SnapshotNotice({ runs, what, onGoToReconcile }: {
  runs: RunListItem[]
  /** subject of the sentence, e.g. "AR Reconciliation" / "The Analyst queue" */
  what: string
  onGoToReconcile: () => void
}) {
  const latest = runs[0]
  const icon = <Inbox className="is-muted" size={22} strokeWidth={1.75} />
  if (!latest) {
    return (
      <EmptyState icon={icon} title="No reconciliation has run yet">
        <span>{what} fills up once an incremental run settles matches and raises exceptions.</span>
        <TextLink onClick={onGoToReconcile}>
          Initiate a reconciliation <ArrowRight size={13} strokeWidth={2} />
        </TextLink>
      </EmptyState>
    )
  }
  if (latest.mode === 'snapshot') {
    return (
      <EmptyState icon={icon} title="Your latest run was a snapshot">
        <span>
          {runLabelFor(runs, latest.run_id)} stores only its own result. {what} fills from
          incremental runs.
        </span>
        <TextLink onClick={onGoToReconcile}>
          Run it again in incremental mode <ArrowRight size={13} strokeWidth={2} />
        </TextLink>
      </EmptyState>
    )
  }
  return (
    <EmptyState icon={icon} title="Nothing here yet">
      <span>
        {what} fills up as incremental runs settle matches and raise exceptions — the latest
        run left nothing here.
      </span>
    </EmptyState>
  )
}
