/**
 * Curated column presets shared between the per-run frozen frames
 * (SourceTable) and the live gold-layer tables (GoldTable). Everything
 * not curated is appended hidden, reachable via the columns menu.
 * bills_enriched stays private to SourceTable — it is a run artifact
 * with settlement stamps and has no gold counterpart.
 */

export interface FramePreset {
  curated: Array<[string, string]>
  hidden: string[]
}

export const SHARED_PRESETS: Record<'bank' | 'bills' | 'recoveries' | 'lineage', FramePreset> = {
  bank: {
    curated: [
      ['used_in_recon', 'Used'],
      ['txn_type', 'Type'],
      ['amount', 'Amount'],
      ['value_date', 'Value date'],
      ['zone_guess', 'Zone'],
      ['narrative', 'Narrative'],
      ['bank_ref', 'Bank ref'],
      ['customer_ref', 'Customer ref'],
      ['page', 'Page'],
    ],
    hidden: ['supplementary', 'timestamp', 'bronze_file_id', 'row_seq'],
  },
  bills: {
    curated: [
      ['bill_number', 'Bill no.'],
      ['contract_no', 'Contract'],
      ['zone', 'Zone'],
      ['bill_status', 'Status'],
      ['bill_date', 'Bill date'],
      ['gross_amount', 'Gross amt'],
      ['approved_amount', 'Approved amt'],
      ['deduction_amount', 'Deducted amt'],
      ['net_payable_amount', 'Net payable'],
      ['submission_ref', 'Submission ref'],
      ['submission_date', 'Submission date'],
      ['payment_order_ref', 'Pay order ref'],
      ['payment_order_date', 'Pay order date'],
      ['payment_advice_date', 'Advice date'],
      ['recovery_count', 'Recov.'],
      ['return_reason', 'Reason for return'],
      ['net_check', 'Net ✓'],
      ['recovery_check', 'Recov ✓'],
      ['sheet', 'Sheet'],
      ['data_row', 'Row'],
    ],
    hidden: ['vendor_name', 'vendor_code', 'unparsed_header', 'header_row',
             'recoveries', 'recovery_sum', 'org_unit', 'contract_date',
             'bronze_file_id', 'row_seq'],
  },
  recoveries: {
    curated: [
      ['bill_number', 'Bill no.'],
      ['submission_ref', 'Submission ref'],
      ['sheet', 'Sheet'],
      ['recovery_head', 'Recovery head'],
      ['recovery_amt', 'Amount'],
      ['recovery_text', 'Raw text'],
    ],
    hidden: ['bill_index', 'bronze_file_id', 'row_seq'],
  },
  // canonical unified view of gold.lineage_docs (RNOTE + CRN)
  lineage: {
    curated: [
      ['doc_type', 'Type'],
      ['doc_no', 'Doc no.'],
      ['doc_date', 'Doc date'],
      ['invoice_no', 'Invoice no.'],
      ['submission_ref', 'Submission ref'],
      ['payment_order_ref', 'Pay order ref'],
      ['po_no', 'PO no.'],
      ['po_date', 'PO date'],
      ['receipt_qty', 'Qty'],
      ['drr_or_challan_no', 'DRR / challan'],
      ['bill_reg_no', 'Bill reg no.'],
    ],
    hidden: ['bronze_file_id', 'row_seq'],
  },
}
