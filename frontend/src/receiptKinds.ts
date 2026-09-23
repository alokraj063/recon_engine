/**
 * What a non-IREPS receipt IS, read from its narrative.
 *
 * 170 open receipts on a four-day load is not a queue anyone works one
 * row at a time — 125 of them are the same ₹25,027.40 deposit-interest
 * line repeated daily. Grouping them by kind turns that into one
 * decision, so the Non-IREPS tab can offer "approve all 125".
 *
 * Display only: the server stores nothing about kinds, and a wrong guess
 * costs nothing but a group heading. The analyst still approves.
 */
export interface ReceiptKind {
  key: string
  label: string
  /** first match wins, so the specific patterns lead */
  test: RegExp
}

export const RECEIPT_KINDS: ReceiptKind[] = [
  { key: 'interest', label: 'Deposit interest', test: /DEPOSIT\s+INTEREST|INT\.?\s+ON\s+DEPOSIT/ },
  { key: 'sweep', label: 'Treasury sweep', test: /SWEEP/ },
  { key: 'deposit', label: 'Deposit', test: /DEPOSIT/ },
  { key: 'return', label: 'Returned transfer', test: /RETURN/ },
  { key: 'rtgs', label: 'RTGS receipt', test: /RTGS/ },
  { key: 'neft', label: 'NEFT receipt', test: /NEFT/ },
  { key: 'imps', label: 'IMPS receipt', test: /IMPS/ },
  { key: 'cms', label: 'CMS collection', test: /\bCMS\b/ },
  { key: 'cheque', label: 'Cheque', test: /\bCHQ\b|CHEQUE/ },
]

const OTHER: ReceiptKind = { key: 'other', label: 'Other receipt', test: /.^/ }

export function receiptKind(narrative: string | null | undefined): ReceiptKind {
  const n = (narrative ?? '').toUpperCase()
  return RECEIPT_KINDS.find((k) => k.test.test(n)) ?? OTHER
}

export const receiptKindLabel = (key: string) =>
  (key === 'other' ? OTHER : RECEIPT_KINDS.find((k) => k.key === key))?.label ?? key
