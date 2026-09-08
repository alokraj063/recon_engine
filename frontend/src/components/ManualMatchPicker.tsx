import { useEffect, useMemo, useState } from 'react'
import { createManualMatch, fetchCustomerConfig } from '../api'
import { ApiError, type LedgerException } from '../types'
import { inr } from '../format'

/** Pair an OPEN bank credit with OPEN bill(s) — or an OPEN bill with a
 *  credit — by hand (item 3.2). Lists the customer's open exceptions of
 *  the other side, nearest amount first; no tolerance is enforced, the
 *  running variance is shown and the picker WARNS (never blocks) when
 *  it exceeds the customer's amount tolerance. Same component in the
 *  Analyst queue and the run-scoped Exception queue. */
export function ManualMatchPicker({ customerId, anchor, candidates, onDone, onCancel }: {
  customerId: string
  /** the OPEN exception the analyst started from */
  anchor: LedgerException
  /** OPEN exceptions of the other side to choose from */
  candidates: LedgerException[]
  onDone: (result: { id: string; seq: number | null; variance: number }) => void
  onCancel: () => void
}) {
  const fromCredit = anchor.exception_type === 'BANK_ONLY'
  const [picked, setPicked] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tolerance, setTolerance] = useState<number | null>(null)

  useEffect(() => {
    let live = true
    fetchCustomerConfig(customerId)
      .then((c) => { if (live) setTolerance(c.rules.amount_tolerance) })
      .catch(() => { /* the warning is best-effort */ })
    return () => { live = false }
  }, [customerId])

  const anchorAmount = fromCredit ? anchor.txn?.amount ?? 0 : anchor.bill?.net_payable_amount ?? 0

  const candAmount = (e: LedgerException) =>
    fromCredit ? e.bill?.net_payable_amount ?? 0 : e.txn?.amount ?? 0
  const candText = (e: LedgerException) => fromCredit
    ? [e.bill?.bill_number, e.bill?.submission_ref, e.bill?.zone, e.bill?.bill_status]
    : [e.txn?.bank_ref, e.txn?.value_date, e.txn?.zone, e.txn?.narrative]

  const sorted = useMemo(() => {
    const q = query.trim().toLowerCase()
    return candidates
      .filter((e) => e.status === 'OPEN' && e.id !== anchor.id)
      .filter((e) => !q || candText(e).some((t) => t != null && String(t).toLowerCase().includes(q))
                        || String(candAmount(e)).includes(q))
      .sort((a, b) => Math.abs(candAmount(a) - anchorAmount) - Math.abs(candAmount(b) - anchorAmount))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, query, anchor.id, anchorAmount, fromCredit])

  const chosen = candidates.filter((e) => picked.includes(e.id))
  const chosenTotal = chosen.reduce((s, e) => s + candAmount(e), 0)
  // variance = credit − Σ bills, whichever side the analyst started from
  const variance = fromCredit ? anchorAmount - chosenTotal : chosenTotal - anchorAmount
  const overTolerance = tolerance != null && chosen.length > 0 && Math.abs(variance) > tolerance

  const toggle = (e: LedgerException) => {
    setPicked((p) => {
      if (p.includes(e.id)) return p.filter((x) => x !== e.id)
      // a bill can only take ONE credit; a credit may cover several bills
      return fromCredit ? [...p, e.id] : [e.id]
    })
  }

  const confirm = async () => {
    if (chosen.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const txnId = fromCredit ? anchor.gold_bank_txn_id : chosen[0].gold_bank_txn_id
      const billIds = fromCredit
        ? chosen.map((e) => e.gold_bill_id).filter((x): x is string => !!x)
        : [anchor.gold_bill_id].filter((x): x is string => !!x)
      if (!txnId || billIds.length === 0) throw new Error('the chosen rows carry no gold ids')
      const res = await createManualMatch(customerId, txnId, billIds, note.trim() || undefined)
      onDone(res)
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mm-picker" onClick={(e) => e.stopPropagation()}>
      <div className="mm-head">
        <span className="detail-section">
          {fromCredit
            ? <>Match this credit ({inr(anchorAmount)}) to bill{'(s)'} — pick from the open bills below</>
            : <>Match this bill ({inr(anchorAmount)}) to a credit — pick from the open credits below</>}
        </span>
        <input className="mm-search" placeholder={fromCredit ? 'bill no. / zone / amount…' : 'bank ref / zone / amount…'}
               value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {sorted.length === 0 ? (
        <p className="frame-note">no open {fromCredit ? 'bills' : 'credits'} match “{query}”.</p>
      ) : (
        <div className="mm-list">
          {sorted.slice(0, 60).map((e) => {
            const on = picked.includes(e.id)
            const diff = candAmount(e) - anchorAmount
            return (
              <label key={e.id} className={`mm-row${on ? ' on' : ''}`}>
                <input type={fromCredit ? 'checkbox' : 'radio'} checked={on} onChange={() => toggle(e)} />
                <span className="mm-amt">{inr(candAmount(e))}</span>
                <span className="mm-text">{candText(e).filter(Boolean).join(' · ')}</span>
                <span className={`mm-diff${Math.abs(diff) < 0.005 ? ' exact' : ''}`}>
                  {Math.abs(diff) < 0.005 ? 'exact' : `${diff > 0 ? '+' : '−'}${inr(Math.abs(diff))}`}
                </span>
              </label>
            )
          })}
          {sorted.length > 60 && (
            <p className="chip-note">showing the 60 nearest of {sorted.length} — refine the search</p>
          )}
        </div>
      )}
      <div className="mm-foot">
        <span className={`mm-variance${overTolerance ? ' warn' : ''}`}>
          {chosen.length === 0
            ? 'nothing picked yet'
            : <>
                {chosen.length} picked · variance <strong>{inr(variance)}</strong>
                {overTolerance && ` — exceeds the ${inr(tolerance ?? 0)} tolerance; the match is still allowed`}
              </>}
        </span>
        <input className="mm-note" maxLength={500} placeholder="note (optional, e.g. short-paid, agreed with vendor)"
               value={note} onChange={(e) => setNote(e.target.value)} />
        <span className="decide">
          <button className="btn-accept" disabled={busy || chosen.length === 0} onClick={confirm}>
            {busy ? 'matching…' : 'Match by user'}
          </button>
          <button className="btn-open" disabled={busy} onClick={onCancel}>Cancel</button>
        </span>
      </div>
      {error && <p className="flag-note">{error}</p>}
    </div>
  )
}
