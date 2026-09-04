"""IREPS "Bill Status" export -> gold bills + recoveries."""

import re
from typing import Dict, Optional

import pandas as pd

from ..gold import ensure_schema
from ..parsers import parse_bill_status
from .base import SelfCheckError, SilverResult, SourceAdapter

# The adapter seam: silver keeps the parser's source-native names; gold is
# the canonical source-agnostic schema. A new ERP source = a new adapter
# with its own map onto the SAME right-hand-side names.
#
# header_row/unparsed_header are NOT produced: they were artifacts of the
# old block-format export (a free-text header line per bill, split label
# by label). The current export has one ordinary header row for the whole
# sheet, so nothing feeds them any more — ensure_schema NA-fills them,
# same pattern as lineage's doc_type (set directly by the adapter, never
# renamed from a silver column).
BILLS_TO_GOLD = {
    "BillNumber": "bill_number",
    "BillDate": "bill_date",
    "ContractNo": "contract_no",
    "ContractDate": "contract_date",
    "CO6No": "submission_ref",
    "CO6Date": "submission_date",
    "CO7No": "payment_order_ref",
    "CO7Date": "payment_order_date",
    "PaymentAdviceDate": "payment_advice_date",
    "PartyCode": "vendor_code",
    "PartyName": "vendor_name",
    "AccountingUnit": "org_unit",
    "Zone": "zone",
    "Status": "bill_status",
    "ReasonForReturn": "return_reason",
    "BillAmt": "gross_amount",
    "PassedAmt": "approved_amount",
    "DeductedAmt": "deduction_amount",
    "NetAmt": "net_payable_amount",
    "Recoveries": "recoveries",
    "RecoveryCount": "recovery_count",
    "RecoverySum": "recovery_sum",
    "NetCheck": "net_check",
    "RecoveryCheck": "recovery_check",
    "Sheet": "sheet",
    "DataRow": "data_row",
}

RECOVERIES_TO_GOLD = {
    "BillIndex": "bill_index",
    "BillNumber": "bill_number",
    "CO6No": "submission_ref",
    "Sheet": "sheet",
    "RecoveryHead": "recovery_head",
    "RecoveryAmt": "recovery_amt",
    "RecoveryText": "recovery_text",
}

# 'Recovery Details' packs every deduction line into one cell:
# '<head>: <amt> <head>: <amt> ...'. A head never contains a colon, and an
# amount is always a plain number (never comma-grouped in this export),
# so 'text up to the next colon, then a number' segments it unambiguously.
_RECOVERY_ITEM_RE = re.compile(r"([^:]+):\s*(-?\d*\.?\d+)")


def _parse_recovery_details(text):
    """'<head>: <amt> <head>: <amt> ...' -> [(head, amt_text), ...]. Empty
    for a bill with no recoveries (a blank cell parses as None)."""
    if text is None:
        return []
    return [(h.strip(), a.strip())
           for h, a in _RECOVERY_ITEM_RE.findall(str(text))]


def _to_amount(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class IrepsBillsAdapter(SourceAdapter):
    source_type = "bill_status"
    adapter_key = "ireps"
    label = "IREPS bill status"
    system = "IREPS"
    file_kinds = (".xlsx", ".xlsm", ".xls")

    def parse(self, path, params: dict) -> SilverResult:
        return SilverResult({"bills": parse_bill_status(path)})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        bills = silver.frames["bills"].copy()

        # Recovery Details -> structured breakdown. A Gold-layer
        # derivation (not the parser's job): Silver keeps the single
        # free-text cell IREPS writes; matching/display/AR recon need the
        # per-head amounts, so they're built here, once, from that text.
        parsed = (bills["RecoveryDetails"].map(_parse_recovery_details)
                  if not bills.empty else pd.Series([], dtype=object))
        bills["Recoveries"] = parsed.map(lambda items: {h: a for h, a in items})
        bills["RecoveryCount"] = parsed.map(len)
        bills["RecoverySum"] = parsed.map(
            lambda items: sum(_to_amount(a) or 0 for _h, a in items))
        # Net Amt can be rounded by IREPS, so allow a rupee of slack
        bills["NetCheck"] = (
            (bills["PassedAmt"] - bills["DeductedAmt"] - bills["NetAmt"]).abs()
            < 1.0
        )
        bills["RecoveryCheck"] = (
            (bills["DeductedAmt"].fillna(0) - bills["RecoverySum"]).abs() < 1.0
        )

        recovery_rows = [
            {"BillIndex": idx, "BillNumber": bill.BillNumber,
             "CO6No": bill.CO6No, "Sheet": getattr(bill, "Sheet", None),
             "RecoveryHead": head, "RecoveryAmt": _to_amount(amt),
             "RecoveryText": amt}
            for idx, (bill, items) in enumerate(zip(bills.itertuples(), parsed))
            for head, amt in items
        ]

        gold_bills = ensure_schema(bills.rename(columns=BILLS_TO_GOLD), "bills")
        gold_bills["row_seq"] = range(len(gold_bills))

        recoveries = pd.DataFrame(recovery_rows, columns=list(RECOVERIES_TO_GOLD))
        gold_recoveries = ensure_schema(
            recoveries.rename(columns=RECOVERIES_TO_GOLD), "recoveries")
        # bill_index is positional into the bills frame, which carries a
        # default RangeIndex — so it IS the bill's row_seq. Made explicit
        # here so persistence can join recoveries to gold bill ids.
        gold_recoveries["bill_row_seq"] = gold_recoveries["bill_index"]
        gold_recoveries["row_seq"] = range(len(gold_recoveries))
        return {"bills": gold_bills, "recoveries": gold_recoveries}

    def selfcheck(self, gold, path, params: dict) -> Optional[dict]:
        """The export's own structure is the control total here: a Bill
        Status workbook always carries at least one bill row. Zero means
        the upload is not an IREPS Bill Status export (or its columns
        moved) — fail loud instead of silently ingesting an empty layer."""
        bills = gold["bills"]
        if len(bills) == 0:
            raise SelfCheckError(
                "no bill rows found: expected an IREPS 'Bill Status' "
                "table with a Contract No / Bill Number / CO6 No / ... "
                "header row",
                {"parsed_count": 0, "recovery_count": 0, "sheets": []},
            )
        sheets = ([str(s) for s in bills["sheet"].dropna().unique()]
                  if "sheet" in bills.columns else [])
        return {"parsed_count": len(bills),
                "recovery_count": len(gold["recoveries"]),
                "sheets": sheets}
