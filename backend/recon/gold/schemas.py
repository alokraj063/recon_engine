"""
The gold layer: one CANONICAL, source-agnostic schema per input kind,
whatever the source. snake_case role names end-to-end — engine in-memory
frames, database columns (db/gold.py maps are identity), API payloads and
frontend all speak the same vocabulary. Source-native names live in the
SILVER layer only; each adapter's to_gold() owns the silver->gold rename
map (see sources/ireps_bills.py BILLS_TO_GOLD — that map is the seam a
new bank/ERP source plugs into).

Roles behind the IREPS-flavored history: submission_ref/date was CO6
(bill registration — also the entity-upsert identity together with
bill_number), payment_order_ref/date was CO7 (the pay order — the
fallback due-date signal), payment_advice_date was "Payment Advice Date
to Bank". Every matching signal has a gold column: amount,
payment_advice_date, payment_order_date, zone / zone_guess, bill_status.

Scope: canonicalization covers these SOURCE frames and columns derived
from them. Engine-derived run-artifact vocabulary is not gold schema and
deliberately keeps its names: the trail columns (PO, Receipt_Doc, ...),
LineageStatus, ExpectedBasis, SettledInStatement/Settled_*,
Attempts/AttemptCount, Candidates, exception_type, gap_type. Lineage is
canonical end-to-end since the Phase-2 canonicalization: adapters emit
the unified lineage_docs shape directly and RN_*/CR_* names survive only
in SILVER (parser output) and as extras keys on old gold rows.

Reserved for future sources — documented, NOT created (adding one later
is a one-line model change + migration): tds_amount, gst_tds_amount,
tax_amount, taxable_amount, retention_amount, penalty_amount, utr_ref,
paid_amount, payment_date, due_date, invoice_number.
"""

import pandas as pd

GOLD_COLUMNS = {
    # bank statement transactions (credits AND debits; used_in_recon flags
    # what the matcher sees)
    "bank_txns": [
        "bank_ref", "customer_ref", "txn_type", "supplementary", "narrative",
        "value_date", "amount", "timestamp", "page", "zone_guess",
        "used_in_recon",
    ],
    # payables / bill status
    "bills": [
        "contract_no", "contract_date", "bill_date", "bill_number", "zone",
        "vendor_name", "vendor_code", "payment_advice_date", "org_unit",
        "unparsed_header", "sheet", "header_row", "submission_ref",
        "submission_date", "bill_status", "gross_amount", "approved_amount",
        "deduction_amount", "net_payable_amount", "payment_order_ref",
        "payment_order_date", "data_row", "recoveries", "recovery_count",
        "return_reason", "net_check", "recovery_sum", "recovery_check",
    ],
    # long-format recovery lines; bill_index is positional into bills
    "recoveries": [
        "bill_index", "bill_number", "submission_ref", "sheet",
        "recovery_head", "recovery_amt", "recovery_text",
    ],
    # upstream lineage documents: ONE canonical unified shape whatever
    # the source — doc_type discriminates (RNOTE / CRN / a future ERP's
    # own document kinds). Matches gold.lineage_docs' typed columns; any
    # source-specific extra column rides through to `extras`.
    "lineage_docs": [
        "doc_type", "doc_no", "doc_date", "invoice_no", "submission_ref",
        "payment_order_ref", "po_no", "po_date", "receipt_qty",
        "drr_or_challan_no", "bill_reg_no", "invoice_date", "bill_reg_date",
    ],
}


def ensure_schema(df: pd.DataFrame, frame_name: str) -> pd.DataFrame:
    """Guarantee the declared gold columns exist.

    An empty parse used to return a frame with NO columns, which broke
    everything downstream; this gives it the full schema. Non-empty frames
    keep their column order (byte-compat with the pre-refactor engine) and
    only gain any missing declared columns, filled with NA. Extra
    source-specific columns pass through untouched.
    """
    cols = GOLD_COLUMNS[frame_name]
    if len(df.columns) == 0:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df
