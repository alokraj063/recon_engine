import type { RunListItem } from '../types'
import { runLabelFor } from './RunFilter'

/**
 * Empty-ledger guidance that names WHY the ledger is empty. AR
 * Reconciliation and the Analyst queue read the durable match/exception
 * ledgers, which only INCREMENTAL runs feed — a snapshot run stores its
 * own result and nothing else. Rendered by those pages in place of their
 * generic empty note; `runs` is the customer's run list, newest first.
 */
export function SnapshotNotice({ runs, what, onGoToReconcile }: {
  runs: RunListItem[]
  /** subject of the sentence, e.g. "AR Reconciliation" / "The Analyst queue" */
  what: string
  onGoToReconcile: () => void
}) {
  const latest = runs[0]
  if (!latest) {
    return (
      <p className="frame-note">
        No reconciliation has run yet —{' '}
        <button className="link-btn" onClick={onGoToReconcile}>initiate one →</button>
      </p>
    )
  }
  if (latest.mode === 'snapshot') {
    return (
      <p className="frame-note">
        Your latest run ({runLabelFor(runs, latest.run_id)}) was a snapshot, which stores
        only its own result. {what} fills from incremental runs —{' '}
        <button className="link-btn" onClick={onGoToReconcile}>
          run it again in incremental mode →
        </button>
      </p>
    )
  }
  return (
    <p className="frame-note">
      {what} fills up as incremental runs settle matches and raise exceptions — the
      latest run left nothing here yet.
    </p>
  )
}
