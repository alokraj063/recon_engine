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
  // (BillNumber, NetAmt), so these two events only count when dated —
  // otherwise a bill with no advice would still show an "advice" entry.
  const invoiceDate = str(row.RN_InvoiceDate) ?? str(row.CR_InvoiceDate)
  if (invoiceDate) push(invoiceDate, 'Invoice raised', [`invoice ${row.BillNumber}`])
  push(str(row.RN_BillRegDate) ?? str(row.CR_BillRegDate), 'Bill registered', [
    str(row.Bill_Reg_No) && `reg no ${row.Bill_Reg_No}`,
  ])
  push(str(row.BillDate), 'Bill date', [
    str(row.BillNumber) && `bill ${row.BillNumber}`,
    money(row.BillAmt) && `bill amt ${money(row.BillAmt)}`,
  ])
  push(str(row.CO6Date), 'CO6 — bill accounted', [
    str(row.CO6No) && `CO6 ${row.CO6No}`,
    money(row.PassedAmt) && `passed ${money(row.PassedAmt)}`,
    typeof row.DeductedAmt === 'number' && row.DeductedAmt > 0
      ? `deducted ${money(row.DeductedAmt)}`
      : null,
  ])
  push(str(row.CO7Date), 'CO7 — payment order', [str(row.CO7No) && `CO7 ${row.CO7No}`])
  // earlier processing attempts of the same bill (grouped view): each
  // returned attempt is its own sienna event, merged into the date order
  if (Array.isArray(row.Attempts)) {
    for (const a of row.Attempts as Array<Record<string, string | number | boolean | null>>) {
      if (a.Current === true || typeof a !== 'object') continue
      const returned = a.Status === 'RETURNED'
      push(
        str(a.CO6Date),
        returned ? 'Bill returned — resubmitted later' : `Earlier attempt — ${a.Status}`,
        [
          str(a.CO6No) && `CO6 ${a.CO6No}`,
          str(a.CO7No) && `CO7 ${a.CO7No}`,
          money(a.NetAmt) && `net ${money(a.NetAmt)}`,
          str(a.ReasonForReturn),
        ],
        returned ? 'returned' : 'normal',
      )
    }
  }

  const adviceDate = str(row.PaymentAdviceDateToBank)
  if (adviceDate)
    push(adviceDate, 'Advice to bank',
         [money(row.NetAmt) && `IREPS instructed the bank to pay ${money(row.NetAmt)}`])

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
  if (str(row.CO7No) && !str(row.PaymentAdviceDateToBank)) {
    events.push({
      date: null,
      title: 'Advice not yet issued',
      detail: 'CO7 raised but no payment advice date in the export',
      kind: 'hollow',
    })
  }
  if (str(row.Status) === 'RETURNED') {
    events.push({
      date: null,
      title: 'Bill returned',
      detail: str(row.ReasonForReturn) ?? 'no reason recorded',
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
