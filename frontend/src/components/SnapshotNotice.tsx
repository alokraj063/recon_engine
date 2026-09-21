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
      <EmptyState icon={icon} title="No reconciliation runs">
        <TextLink onClick={onGoToReconcile}>
          Run reconciliation <ArrowRight size={13} strokeWidth={2} />
        </TextLink>
      </EmptyState>
    )
  }
  if (latest.mode === 'snapshot') {
    return (
      <EmptyState icon={icon} title="Latest run is a snapshot">
        <span>{what} is populated by incremental runs only ({runLabelFor(runs, latest.run_id)} was a snapshot).</span>
        <TextLink onClick={onGoToReconcile}>
          Run incremental reconciliation <ArrowRight size={13} strokeWidth={2} />
        </TextLink>
      </EmptyState>
    )
  }
  return (
    <EmptyState icon={icon} title="No data" />
  )
}
