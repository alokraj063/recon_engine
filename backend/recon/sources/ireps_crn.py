"""IREPS CRN (challan) report -> gold lineage documents."""

from typing import Dict

import pandas as pd

from ..gold import ensure_schema
from ..parsers import load_crn
from .base import SilverResult, SourceAdapter

# Silver CR_* names -> the canonical unified lineage_docs shape (see
# ireps_rnote.py — same seam, same right-hand-side names).
CRN_TO_GOLD = {
    "CRNNo": "doc_no",
    "CRNDate": "doc_date",
    "InvoiceNo": "invoice_no",
    "CO6No": "submission_ref",
    "CO7No": "payment_order_ref",
    "CR_PONo": "po_no",
    "CR_PODate": "po_date",
    "CR_Qty": "receipt_qty",
    "CR_ChallanNo": "drr_or_challan_no",
    "CR_BillRegNo": "bill_reg_no",
    "CR_InvoiceDate": "invoice_date",
    "CR_BillRegDate": "bill_reg_date",
}


class IrepsCrnAdapter(SourceAdapter):
    source_type = "lineage_crn"
    adapter_key = "ireps_crn"
    label = "IREPS CRN report"
    system = "IREPS"
    file_kinds = (".xlsx", ".xlsm")

    def parse(self, path, params: dict) -> SilverResult:
        return SilverResult({"crn": load_crn(path, params.get("sheet", 0))})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        df = silver.frames["crn"].rename(columns=CRN_TO_GOLD)
        df["doc_type"] = "CRN"
        df = ensure_schema(df, "lineage_docs")
        df["row_seq"] = range(len(df))
        return {"lineage_crn": df}
