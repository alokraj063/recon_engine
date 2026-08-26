import type { ReconResponse, Row } from './types'

/**
 * Runs persisted BEFORE the gold-schema canonicalization carry the old
 * column vocabulary in their frozen payloads/frames. The payloads stay
 * untouched evidence on the server; this translates their keys to the
 * canonical names at LOAD time so every display component (which reads
 * canonical names only) renders old runs correctly.
 *
 * Rule: a legacy key is moved ONLY when its canonical target is absent —
 * identity for post-rename rows, which never carry legacy keys. Frozen
 * engine-artifact names (LineageStatus, Settled_*, TRAIL, the RN_ / CR_
 * lineage internals, ExpectedBasis, gap_type, zone_from_narrative,
 * bill_zone) are not listed and pass through untouched.
 */
const LEGACY_KEYS: Record<string, string> = {
  // queue display spine (old exception_queue vocabulary)
  Amount: 'amount',
  Value_Date: 'value_date',
  Zone: 'zone',
  Bank_Ref: 'bank_ref',
  Bank_Narrative: 'bank_narrative',
  // bills frame / candidate / attempt fields
  BillNumber: 'bill_number',
  ContractNo: 'contract_no',
  ContractDate: 'contract_date',
  BillDate: 'bill_date',
  Status: 'bill_status',
  AccountingUnit: 'org_unit',
  PartyCode: 'vendor_code',
  PartyName: 'vendor_name',
  CO6No: 'submission_ref',
  CO6Date: 'submission_date',
  CO7No: 'payment_order_ref',
  CO7Date: 'payment_order_date',
  PaymentAdviceDateToBank: 'payment_advice_date',
  BillAmt: 'gross_amount',
  PassedAmt: 'approved_amount',
  DeductedAmt: 'deduction_amount',
  NetAmt: 'net_payable_amount',
  RecoverySum: 'recovery_sum',
  RecoveryCount: 'recovery_count',
  Recoveries: 'recoveries',
  ReasonForReturn: 'return_reason',
  NetCheck: 'net_check',
  RecoveryCheck: 'recovery_check',
  Sheet: 'sheet',
  HeaderRow: 'header_row',
  DataRow: 'data_row',
  UnparsedHeader: 'unparsed_header',
  BillIndex: 'bill_index',
  RecoveryHead: 'recovery_head',
  RecoveryAmt: 'recovery_amt',
  RecoveryText: 'recovery_text',
  UsedInRecon: 'used_in_recon',
  // old MatchResult field names (matched frame + review queue rows)
  status: 'bill_status',
  accounting_unit: 'org_unit',
  co7_no: 'payment_order_ref',
  co7_date: 'payment_order_date',
  advice_date: 'payment_advice_date',
}

// list-of-dict cells whose element keys follow the same vocabulary
const NESTED_LISTS = ['Candidates', 'Attempts']

export function normalizeRow(row: Row): Row {
  let out: Row | null = null
  for (const [legacy, canonical] of Object.entries(LEGACY_KEYS)) {
    if (legacy in row && !(canonical in row)) {
      out = out ?? { ...row }
      out[canonical] = out[legacy]
      delete out[legacy]
    }
  }
  for (const cell of NESTED_LISTS) {
    const v = (out ?? row)[cell]
    if (Array.isArray(v) && v.some((d) => d && typeof d === 'object')) {
      out = out ?? { ...row }
      out[cell] = (v as Row[]).map((d) =>
        d && typeof d === 'object' && !Array.isArray(d) ? normalizeRow(d) : d) as Row[keyof Row]
    }
  }
  return out ?? row
}

export function normalizeRows(rows: Row[]): Row[] {
  return rows.map(normalizeRow)
}

export function normalizeRun(payload: ReconResponse): ReconResponse {
  return {
    ...payload,
    matched: normalizeRows(payload.matched),
    exceptions: normalizeRows(payload.exceptions),
  }
}
