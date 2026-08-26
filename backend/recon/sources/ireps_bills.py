"""IREPS "View Bills Status" export -> gold bills + recoveries."""

from typing import Dict

import pandas as pd

from ..gold import ensure_schema
from ..parsers import parse_bill_status
from .base import SilverResult, SourceAdapter

# The adapter seam: silver keeps the parser's source-native names; gold is
# the canonical source-agnostic schema. A new ERP source = a new adapter
# with its own map onto the SAME right-hand-side names.
BILLS_TO_GOLD = {
    "BillNumber": "bill_number",
    "BillDate": "bill_date",
    "ContractNo": "contract_no",
    "ContractDate": "contract_date",
    "CO6No": "submission_ref",
    "CO6Date": "submission_date",
    "CO7No": "payment_order_ref",
    "CO7Date": "payment_order_date",
    "PaymentAdviceDateToBank": "payment_advice_date",
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
    "HeaderRow": "header_row",
    "DataRow": "data_row",
    "UnparsedHeader": "unparsed_header",
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


class IrepsBillsAdapter(SourceAdapter):
    source_type = "bill_status"
    adapter_key = "ireps"
    label = "IREPS bill status"
    system = "IREPS"
    file_kinds = (".xlsx", ".xlsm")

    def parse(self, path, params: dict) -> SilverResult:
        bills, recoveries = parse_bill_status(path, return_recoveries=True)
        return SilverResult({"bills": bills, "recoveries": recoveries})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        bills = silver.frames["bills"].rename(columns=BILLS_TO_GOLD)
        bills = ensure_schema(bills, "bills")
        bills["row_seq"] = range(len(bills))
        recoveries = silver.frames["recoveries"].rename(columns=RECOVERIES_TO_GOLD)
        recoveries = ensure_schema(recoveries, "recoveries")
        # bill_index is positional into the bills frame, which carries a
        # default RangeIndex — so it IS the bill's row_seq. Made explicit
        # here so persistence can join recoveries to gold bill ids.
        recoveries["bill_row_seq"] = recoveries["bill_index"]
        recoveries["row_seq"] = range(len(recoveries))
        return {"bills": bills, "recoveries": recoveries}
