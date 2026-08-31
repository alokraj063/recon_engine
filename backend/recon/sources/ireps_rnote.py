"""IREPS RNOTE (receipt note) report -> gold lineage documents."""

from typing import Dict

import pandas as pd

from ..gold import ensure_schema
from ..parsers import load_rnote
from .base import SilverResult, SourceAdapter

# The adapter seam for lineage documents: silver keeps the parser's
# RN_* names; gold is the canonical unified lineage_docs shape. A new
# ERP's upstream document = its own map onto the SAME right-hand-side
# names plus its own doc_type value.
RNOTE_TO_GOLD = {
    "RNoteNo": "doc_no",
    "RNoteDate": "doc_date",
    "InvoiceNo": "invoice_no",
    "CO6No": "submission_ref",
    "CO7No": "payment_order_ref",
    "RN_PONo": "po_no",
    "RN_PODate": "po_date",
    "RNoteQty": "receipt_qty",
    "RN_DRRNo": "drr_or_challan_no",
    "RN_BillRegNo": "bill_reg_no",
    "RN_InvoiceDate": "invoice_date",
    "RN_BillRegDate": "bill_reg_date",
}


class IrepsRnoteAdapter(SourceAdapter):
    source_type = "lineage_rnote"
    adapter_key = "ireps_rnote"
    label = "IREPS RNOTE report"
    system = "IREPS"
    file_kinds = (".xlsx", ".xlsm")

    def parse(self, path, params: dict) -> SilverResult:
        # `sheet` finally reachable from config (the CLI never exposed it)
        return SilverResult({"rnote": load_rnote(path, params.get("sheet", 0))})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        df = silver.frames["rnote"].rename(columns=RNOTE_TO_GOLD)
        df["doc_type"] = "RNOTE"
        df = ensure_schema(df, "lineage_docs")
        df["row_seq"] = range(len(df))
        return {"lineage_rnote": df}
