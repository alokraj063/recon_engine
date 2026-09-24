import { useCallback, useState } from 'react'
import { MessageSquareText } from 'lucide-react'
import { acceptMatch, rejectMatch, reopenMatch, unlockMatch } from '../api'
import { ApiError, type LedgerBillInfo, type LedgerMatch } from '../types'
import { fmtWhen } from '../format'
import { DecisionDialog } from './ui'
import { CandidateCompare, type CompareField } from './CandidateCompare'

/** The subset of a ledger match a decision needs. Both hosts pass the
 *  LIVE row from /api/ledger — the Analyst queue's own list, or the
 *  overlay the Exception queue fetches over its frozen run frame. */
export type DecidableMatch = Pick<LedgerMatch, 'id' | 'status' | 'locked_by' | 'bills'>
  & Partial<Pick<LedgerMatch, 'seq' | 'txn'>>

export type DecisionAction = 'accept' | 'reject' | 'unlock' | 'reopen'

export interface DecisionResult {
  id: string
  status: string
  locked_by?: string | null
}

/** One place that talks to the four /api/matches/{id}/* routes. Owns the
 *  per-match busy flag; hands the authoritative response to the host,
 *  which applies it however it likes (the Analyst queue patches its list
 *  and refetches, the Exception queue bumps the app-wide ledger epoch). */
export function useMatchDecision(
  onDecided: (id: string, res: DecisionResult) => void,
  onError: (message: string) => void,
) {
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const decide = useCallback(async (id: string, action: DecisionAction,
                                    goldBillId?: string, note?: string) => {
    setBusy((b) => ({ ...b, [id]: true }))
    try {
      const res: DecisionResult = action === 'accept' ? await acceptMatch(id, goldBillId, note)
        : action === 'unlock' ? await unlockMatch(id, note)
        : action === 'reopen' ? await reopenMatch(id, note)
        : await rejectMatch(id, note)
      onDecided(id, res)
    } catch (e) {
      onError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy((b) => ({ ...b, [id]: false }))
    }
  }, [onDecided, onError])
  return { decide, busy }
}

export type Decide = ReturnType<typeof useMatchDecision>['decide']

const matchName = (m: DecidableMatch) => (m.seq !== null && m.seq !== undefined ? `M-${m.seq}` : 'this match')

/** Accept / Reject (OPEN), Unlock (LOCKED), Reopen (REJECTED) — the
 *  same buttons wherever a durable match is shown. Accept and Reject are
 *  one click; "+ note" opens the same decision with a note. Unlock and
 *  Reopen always confirm first: they move a settled decision back into
 *  review, which should never happen by a stray click. */
export function MatchDecision({ match, busy, decide }: {
  match: DecidableMatch
  busy: boolean
  decide: Decide
}) {
  const [dialog, setDialog] = useState<'note' | 'unlock' | 'reopen' | null>(null)
  const close = () => setDialog(null)
  const name = matchName(match)
  const act = (action: DecisionAction) => async (note: string) => {
    close()
    await decide(match.id, action, undefined, note)
  }
  return (
    <span className="decide">
      {match.status === 'OPEN' && (
        <>
          <button className="btn-accept" disabled={busy} title="Lock the match"
                  onClick={() => decide(match.id, 'accept')}>
            Accept
          </button>
          <button className="btn-reject" disabled={busy} title="Release the bills; the credit reopens"
                  onClick={() => decide(match.id, 'reject')}>
            Reject
          </button>
          <button type="button" className="decide-note" disabled={busy}
                  title="Accept or reject with a note" onClick={() => setDialog('note')}>
            + note
          </button>
        </>
      )}
      {match.status === 'LOCKED' && (
        <button className="btn-open" title="Return the match to review" disabled={busy}
                onClick={() => setDialog('unlock')}>
          Unlock
        </button>
      )}
      {match.status === 'REJECTED' && (
        <button className="btn-reopen" title="Undo the rejection and return the match to review"
                disabled={busy} onClick={() => setDialog('reopen')}>
          Reopen
        </button>
      )}
      {dialog === 'note' && (
        <DecisionDialog title={`Decide ${name}`} busy={busy} onClose={close}
          message="Accept locks the matcher's pick. Reject releases the bills and reopens the credit."
          actions={[{ label: 'Reject', tone: 'danger', run: act('reject') },
                    { label: 'Accept', tone: 'primary', run: act('accept') }]} />
      )}
      {dialog === 'unlock' && (
        <DecisionDialog title={`Unlock ${name}?`} busy={busy} onClose={close}
          notePlaceholder="Reason (optional)"
          message={<>The match goes back to <b>To review</b>. Its credit and bills stay held
                    until someone accepts or rejects it.</>}
          actions={[{ label: 'Unlock', tone: 'primary', run: act('unlock') }]} />
      )}
      {dialog === 'reopen' && (
        <DecisionDialog title={`Reopen ${name}?`} busy={busy} onClose={close}
          notePlaceholder="Reason (optional)"
          message={<>The match goes back to <b>To review</b> and claims its credit and bills
                    again.</>}
          actions={[{ label: 'Reopen', tone: 'primary', run: act('reopen') }]} />
      )}
    </span>
  )
}

/** Who decided a match (the Decided by column; the time on hover) —
 *  System for an automatic lock. */
export function DecidedBy({ match }: { match: LedgerMatch }) {
  if (!match.decided_at && !match.locked_by) return null
  const who = match.decided_by
    ?? (match.locked_by === 'AUTO_HIGH' && !match.decided_at ? 'System' : null)
  const when = match.decided_at ?? match.locked_at
  if (!who && !when) return null
  return (
    <span title={when ? fmtWhen(when) : undefined}>
      {who ?? 'User'}
      {match.decision_note && (
        <span className="decision-note-ic" title={match.decision_note} aria-label="Has a note">
          <MessageSquareText size={12} strokeWidth={2} />
        </span>
      )}
    </span>
  )
}

/** The note left with the latest decision, for a row's expanded detail. */
export function DecisionNote({ match }: { match: LedgerMatch }) {
  if (!match.decision_note) return null
  return (
    <div className="decision-note-block">
      <div className="dt-label">
        Decision note{match.decided_by ? ` · ${match.decided_by}` : ''}
        {match.decided_at ? ` · ${fmtWhen(match.decided_at)}` : ''}
      </div>
      <p className="decision-note-text">{match.decision_note}</p>
    </div>
  )
}

/** the ledger's own bill fields, for a match with no engine evidence */
const LEDGER_BILL_FIELDS: CompareField[] = [
  { key: 'net_payable_amount', label: 'Net payable', refKey: 'amount' },
  { key: 'zone', label: 'Zone', refKey: 'zone' },
  { key: 'bill_status', label: 'Status' },
  { key: 'submission_ref', label: 'Submission ref' },
  { key: 'due_date', label: 'Due date' },
]

/** "Pick the settling bill" from the ledger's own bill fields — the
 *  comparison view for a match whose run evidence is not a review row;
 *  accepting locks the credit to THAT bill. */
export function PickList({ match, busy, decide }: {
  match: DecidableMatch
  busy: boolean
  decide: Decide
}) {
  if (match.status !== 'OPEN' || match.bills.length === 0) return null
  return (
    <>
      <div className="detail-section">Select the bill to accept</div>
      <CandidateCompare
        busy={busy}
        fields={LEDGER_BILL_FIELDS}
        credit={match.txn ? { amount: match.txn.amount, zone: match.txn.zone } : undefined}
        columns={match.bills.map((b: LedgerBillInfo) => ({
          key: b.gold_bill_id,
          title: b.bill_number ?? b.gold_bill_id.slice(0, 8),
          picked: b.role === 'picked',
          values: b as unknown as Record<string, string | number | null>,
          onAccept: (note?: string) => decide(match.id, 'accept', b.gold_bill_id, note),
        }))} />
    </>
  )
}

/** Find the ledger bill a ReviewEvidence candidate refers to (candidates
 *  carry bill numbers, the API wants gold ids). */
export function billByNumber(match: DecidableMatch, billNumber: unknown): LedgerBillInfo | undefined {
  return match.bills.find((x) => String(x.bill_number) === String(billNumber))
}

/** Human status for the live ledger state of a match. */
export function ledgerStatusLabel(m: Pick<LedgerMatch, 'status' | 'locked_by'>): string {
  if (m.status === 'LOCKED') return m.locked_by === 'USER' ? 'LOCKED by user' : 'LOCKED (auto)'
  return m.status
}
