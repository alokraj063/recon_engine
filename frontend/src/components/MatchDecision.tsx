import { useCallback, useState } from 'react'
import { acceptMatch, rejectMatch, reopenMatch, unlockMatch } from '../api'
import { ApiError, type LedgerBillInfo, type LedgerMatch } from '../types'
import { inr } from '../format'

/** The subset of a ledger match a decision needs. Both hosts pass the
 *  LIVE row from /api/ledger — the Analyst queue's own list, or the
 *  overlay the Exception queue fetches over its frozen run frame. */
export type DecidableMatch = Pick<LedgerMatch, 'id' | 'status' | 'locked_by' | 'bills'>

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
  const decide = useCallback(async (id: string, action: DecisionAction, goldBillId?: string) => {
    setBusy((b) => ({ ...b, [id]: true }))
    try {
      const res: DecisionResult = action === 'accept' ? await acceptMatch(id, goldBillId)
        : action === 'unlock' ? await unlockMatch(id)
        : action === 'reopen' ? await reopenMatch(id)
        : await rejectMatch(id)
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

/** Accept / Reject (OPEN), Unlock (LOCKED), Reopen (REJECTED) — the
 *  same buttons wherever a durable match is shown. */
export function MatchDecision({ match, busy, decide }: {
  match: DecidableMatch
  busy: boolean
  decide: Decide
}) {
  if (match.status === 'OPEN') {
    return (
      <span className="decide">
        <button className="btn-accept" disabled={busy}
                onClick={() => decide(match.id, 'accept')}>
          Accept
        </button>
        <button className="btn-reject" disabled={busy}
                onClick={() => decide(match.id, 'reject')}>
          Reject
        </button>
      </span>
    )
  }
  if (match.status === 'LOCKED') {
    return (
      <span className="decide">
        <button className="btn-open"
                title="Reopen this decision — the match returns to OPEN for review"
                disabled={busy}
                onClick={() => decide(match.id, 'unlock')}>
          Unlock
        </button>
      </span>
    )
  }
  return (
    <span className="decide">
      <button className="btn-reopen"
              title="Undo this rejection — the match returns to OPEN and re-claims its credit and bills"
              disabled={busy}
              onClick={() => decide(match.id, 'reopen')}>
        Reopen
      </button>
    </span>
  )
}

/** "Pick the settling bill" — one accept button per bill the match
 *  knows (picked + candidates); accepting locks the credit to THAT bill. */
export function PickList({ match, busy, decide }: {
  match: DecidableMatch
  busy: boolean
  decide: Decide
}) {
  if (match.status !== 'OPEN' || match.bills.length === 0) return null
  return (
    <>
      <div className="detail-section">
        Pick the settling bill — accepting locks the credit to YOUR choice
      </div>
      <div className="pick-list">
        {match.bills.map((b: LedgerBillInfo) => (
          <span key={b.gold_bill_id} className="pick-row">
            <span className="chip chip-bill">
              {b.bill_number ?? b.gold_bill_id.slice(0, 8)}
              {' · '}{inr(b.net_payable_amount)}
              {b.zone ? ` · ${b.zone}` : ''}
            </span>
            {b.role === 'picked' && <span className="chip-note">matcher's pick</span>}
            <button className="btn-accept" disabled={busy}
                    onClick={() => decide(match.id, 'accept', b.gold_bill_id)}>
              Accept this bill
            </button>
          </span>
        ))}
      </div>
    </>
  )
}

/** Find the ledger bill a ReviewEvidence candidate card refers to (cards
 *  carry bill numbers, the API wants gold ids). */
export function billByNumber(match: DecidableMatch, billNumber: unknown): LedgerBillInfo | undefined {
  return match.bills.find((x) => String(x.bill_number) === String(billNumber))
}

/** Human status for the live ledger state of a match. */
export function ledgerStatusLabel(m: Pick<LedgerMatch, 'status' | 'locked_by'>): string {
  if (m.status === 'LOCKED') return m.locked_by === 'USER' ? 'LOCKED by user' : 'LOCKED (auto)'
  return m.status
}
