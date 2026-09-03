"""
Per-customer advisory copy (plan Phase 1): the frozen codes stay stable,
the text keyed by them is configurable. Defaults must be byte-identical
to the historical constants (the golden gate proves this end-to-end on
the sample docs; here we prove the override path and the merge).
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.engine import (BANK_ACTIONS, BILL_ACTIONS,  # noqa: E402
                          REVIEW_ACTIONS, exception_queue, reconcile,
                          resolve_copy)


def _bank(**over):
    row = {"bank_ref": "R1", "narrative": "NEFT FROM SOMEWHERE",
           "amount": 1000.0, "value_date": datetime(2026, 3, 18),
           "zone_guess": None, "customer_ref": "C-1"}
    row.update(over)
    return pd.DataFrame([row])


def _bills(**over):
    row = {"bill_number": "B1", "contract_no": "C1", "zone": "NR",
           "bill_status": "PAYMENT MADE", "net_payable_amount": 1000.0,
           "gross_amount": 1000.0, "approved_amount": 1000.0,
           "deduction_amount": 0.0,
           "payment_advice_date": pd.Timestamp("2026-03-18"),
           "submission_ref": "6-1",
           "submission_date": pd.Timestamp("2026-03-10"),
           "payment_order_ref": "7-1",
           "payment_order_date": pd.Timestamp("2026-03-15"),
           "vendor_code": "V1", "org_unit": "AU1",
           "recoveries": {}, "recovery_count": 0, "return_reason": None,
           "sheet": "s", "data_row": 1}
    row.update(over)
    return pd.DataFrame([row])


def test_resolve_copy_defaults_and_partial_merge():
    resolved = resolve_copy(None)
    assert resolved["gap_type"] == BANK_ACTIONS
    assert resolved["expected_basis"] == BILL_ACTIONS
    assert resolved["review"] == REVIEW_ACTIONS
    assert resolve_copy({}) == resolved

    partial = resolve_copy(
        {"gap_type": {"UNRECOGNISED_RECEIPT": "custom text"}})
    assert partial["gap_type"]["UNRECOGNISED_RECEIPT"] == "custom text"
    # the sibling code keeps its default
    assert partial["gap_type"]["SIGNAL_BILL_NOT_FOUND"] == \
        BANK_ACTIONS["SIGNAL_BILL_NOT_FOUND"]
    assert partial["review"] == REVIEW_ACTIONS
    # unknown sections/codes are ignored (the API rejects them upstream)
    assert resolve_copy({"nope": {"X": "y"}})["gap_type"] == BANK_ACTIONS


def test_default_reconcile_stamps_historical_text():
    # unmatched credit (no bill shares the amount) -> BANK_ONLY
    out = reconcile(_bank(amount=42.0), _bills())
    assert list(out["bank_only"]["action"]) == \
        [BANK_ACTIONS["UNRECOGNISED_RECEIPT"]]
    # advised bill with no credit -> BILL_ONLY with the default text
    assert list(out["bill_only"]["action"]) == [BILL_ACTIONS["ADVICE_DATE"]]


def test_copy_overrides_reach_both_exception_sides_and_review():
    over = {
        "gap_type": {"UNRECOGNISED_RECEIPT": "route to metro ledger"},
        "expected_basis": {"ADVICE_DATE": "chase the payer"},
        "review": {"LOW": "double-check this one"},
    }
    out = reconcile(_bank(amount=42.0), _bills(), copy_overrides=over)
    assert list(out["bank_only"]["action"]) == ["route to metro ledger"]
    assert list(out["bill_only"]["action"]) == ["chase the payer"]

    # a LOW match (date agrees, zone does not) picks up the review text
    out2 = reconcile(_bank(amount=1000.0), _bills(zone=None),
                     copy_overrides=over)
    assert list(out2["matched"]["confidence"]) == ["LOW"]
    assert list(out2["match_review"]["action"]) == ["double-check this one"]
    # ...and the queue carries it through
    q = exception_queue(out2)
    assert "double-check this one" in set(q["action"])
