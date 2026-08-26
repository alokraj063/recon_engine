import type { Row } from '../types'
import { inr } from '../format'

/**
 * The bill's document history as a vertical timeline: date on the left,
 * dot on the line, what happened on the right. Dated events sort
 * chronologically (ISO strings, so lexicographic order works); documents
 * that exist but carry no date in the exports are appended dimmed.
 */

interface TlEvent {
  date: string | null
  title: string
  detail: string
  kind: 'normal' | 'gold' | 'hollow' | 'returned'
}

const str = (v: unknown): string | null => {
  if (v === null || v === undefined || v === '') return null
  const s = String(v)
  return s === 'nan' ? null : s
}

const money = (v: unknown): string | null =>
  typeof v === 'number' ? inr(v) : null

function buildEvents(row: Row): TlEvent[] {
  const dated: TlEvent[] = []
  const undated: TlEvent[] = []

  const push = (date: string | null, title: string, parts: Array<string | null>, kind: TlEvent['kind'] = 'normal') => {
    const detail = parts.filter(Boolean).join(' · ')
    if (!date && !detail) return
    ;(date ? dated : undated).push({ date, title, detail, kind })
  }

  const source =
    str(row.LineageStatus) === 'RNOTE+CRN' ? 'per RNOTE + CRN'
    : str(row.LineageStatus) === 'RNOTE' ? 'per RNOTE'
    : str(row.LineageStatus) === 'CRN' ? 'per CRN'
    : null

  push(str(row.PO_Date), 'PO placed', [str(row.PO) && `PO ${row.PO}`])
  push(str(row.Receipt_Date), 'Goods received', [
    str(row.Receipt_Doc) && `doc ${row.Receipt_Doc}`,
    str(row.Receipt_Qty) && `qty ${row.Receipt_Qty}`,
    str(row.DRR_or_Challan) && `DRR/challan ${row.DRR_or_Challan}`,
    source,
  ])
  // invoice and advice details derive from fields that always exist
  // (bill_number, net_payable_amount), so these two events only count
  // when dated — otherwise a bill with no advice would still show an
  // "advice" entry.
  const invoiceDate = str(row.RN_InvoiceDate) ?? str(row.CR_InvoiceDate)
  if (invoiceDate) push(invoiceDate, 'Invoice raised', [`invoice ${row.bill_number}`])
  push(str(row.RN_BillRegDate) ?? str(row.CR_BillRegDate), 'Bill registered', [
    str(row.Bill_Reg_No) && `reg no ${row.Bill_Reg_No}`,
  ])
  push(str(row.bill_date), 'Bill date', [
    str(row.bill_number) && `bill ${row.bill_number}`,
    money(row.gross_amount) && `bill amt ${money(row.gross_amount)}`,
  ])
  push(str(row.submission_date), 'CO6 — bill accounted', [
    str(row.submission_ref) && `CO6 ${row.submission_ref}`,
    money(row.approved_amount) && `passed ${money(row.approved_amount)}`,
    typeof row.deduction_amount === 'number' && row.deduction_amount > 0
      ? `deducted ${money(row.deduction_amount)}`
      : null,
  ])
  push(str(row.payment_order_date), 'CO7 — payment order',
       [str(row.payment_order_ref) && `CO7 ${row.payment_order_ref}`])
  // earlier processing attempts of the same bill (grouped view): each
  // returned attempt is its own sienna event, merged into the date order.
  // Attempt keys mirror engine.ATTEMPT_FIELDS.
  if (Array.isArray(row.Attempts)) {
    for (const a of row.Attempts as Array<Record<string, string | number | boolean | null>>) {
      if (a.Current === true || typeof a !== 'object') continue
      const returned = a.bill_status === 'RETURNED'
      push(
        str(a.submission_date),
        returned ? 'Bill returned — resubmitted later' : `Earlier attempt — ${a.bill_status}`,
        [
          str(a.submission_ref) && `CO6 ${a.submission_ref}`,
          str(a.payment_order_ref) && `CO7 ${a.payment_order_ref}`,
          money(a.net_payable_amount) && `net ${money(a.net_payable_amount)}`,
          str(a.return_reason),
        ],
        returned ? 'returned' : 'normal',
      )
    }
  }

  const adviceDate = str(row.payment_advice_date)
  if (adviceDate)
    push(adviceDate, 'Advice to bank',
         [money(row.net_payable_amount)
          && `IREPS instructed the bank to pay ${money(row.net_payable_amount)}`])

  // closure: the credit landing at HSBC (HIGH-confidence matches only —
  // the Settled token is injected by the bills_enriched view)
  if (row.Settled === 'SETTLED')
    push(str(row.Settled_ValueDate), 'Credit received at HSBC', [
      money(row.Settled_CreditAmt) && `${money(row.Settled_CreditAmt)} credited`,
      str(row.Settled_BankRef) && `ref ${row.Settled_BankRef}`,
      'HIGH confidence match',
    ], 'gold')

  const events = [
    ...dated.sort((a, b) => (a.date! < b.date! ? -1 : a.date! > b.date! ? 1 : 0)),
    ...undated.map((e) => ({ ...e, detail: [e.detail, 'date not in exports'].filter(Boolean).join(' · ') })),
  ]

  // missing-step markers
  if (str(row.LineageStatus) === 'NO_UPSTREAM_DOC') {
    events.unshift({
      date: null,
      title: 'No receipt document',
      detail: 'nothing found in the RNOTE / CRN exports — see whether the bill predates the export window',
      kind: 'hollow',
    })
  }
  if (str(row.payment_order_ref) && !str(row.payment_advice_date)) {
    events.push({
      date: null,
      title: 'Advice not yet issued',
      detail: 'CO7 raised but no payment advice date in the export',
      kind: 'hollow',
    })
  }
  if (str(row.bill_status) === 'RETURNED') {
    events.push({
      date: null,
      title: 'Bill returned',
      detail: str(row.return_reason) ?? 'no reason recorded',
      kind: 'returned',
    })
  }
  return events
}

export function LineageTimeline({ row }: { row: Row }) {
  const events = buildEvents(row)
  if (!events.length) return <p className="frame-note">no dated documents for this bill</p>
  return (
    <div className="timeline">
      {events.map((e, i) => (
        <div key={i} className={`tl-event${e.date ? '' : ' dim'}`}>
          <span className="tl-date">{e.date ?? '—'}</span>
          <span className={`tl-dot tl-${e.kind}`} />
          <span className="tl-body">
            <span className="tl-title">{e.title}</span>
            {e.detail && <span className="tl-detail">{e.detail}</span>}
          </span>
        </div>
      ))}
    </div>
  )
}
