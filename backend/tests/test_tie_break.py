"""
AMBIGUOUS tie-break: among bills tied at the top score the matcher picks
the one whose compared date lies closest to the credit's value date (pure
smallest gap, undated last, bill index as the final tiebreak), reports the
candidates pick-first-then-closest, and says why. Pure-pandas synthetic
frames, no DB. Scoring itself is untouched — every case here still ties.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.engine import exception_queue, reconcile  # noqa: E402
from recon.matching.matcher import match_bank_to_billstatus  # noqa: E402

VALUE_DATE = datetime(2026, 8, 28)


def _bank(**over):
    row = {"bank_ref": "R1", "narrative": "NEFT FROM 3203JHS NCRE70",
           "amount": 656492.0, "value_date": VALUE_DATE, "zone_guess": "NCR",
           "customer_ref": "CUST-42"}
    row.update(over)
    return row


def _bill(n, advice=None, order=None, **over):
    row = {"bill_number": f"B{n}", "contract_no": "C1", "zone": "SER",
           "bill_status": "PAYMENT MADE", "net_payable_amount": 656492.0,
           "gross_amount": 668387.4, "approved_amount": 668387.0,
           "deduction_amount": 11894.6,
           "payment_advice_date": pd.Timestamp(advice) if advice else pd.NaT,
           "submission_ref": f"6-{n}",
           "submission_date": pd.Timestamp("2026-08-03"),
           "payment_order_ref": f"7-{n}",
           "payment_order_date": pd.Timestamp(order) if order else pd.NaT,
           "vendor_code": "CUST-42", "org_unit": "ADRA",
           "recoveries": {}, "recovery_count": 0, "return_reason": None,
           "sheet": "s", "data_row": n}
    row.update(over)
    return row


def _frames(bank_rows, bill_rows):
    return pd.DataFrame(bank_rows), pd.DataFrame(bill_rows)


def _one(results):
    assert len(results) == 1, [r.bill_number for r in results]
    return results[0]


def test_closest_advice_date_wins_and_orders_candidates():
    """The screenshot case: gaps 17 / 21 / 10 days, zone mismatch, so all
    three tie at score 0. Pick = 10d, cards ordered 10, 17, 21."""
    bank, bills = _frames(
        [_bank()],
        [_bill(1, advice="2026-08-11"),    # 17d
         _bill(2, advice="2026-08-07"),    # 21d
         _bill(3, advice="2026-08-18")])   # 10d
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.confidence == "AMBIGUOUS"
    assert r.tied_candidates == 3
    assert r.bill_number == "B3"
    assert r.date_gap_days == 10 and r.date_source == "advice"
    assert r.bill_indices == [2]
    assert r.candidate_indices == [2, 0, 1]
    assert r.candidate_gaps == [10, 17, 21]
    assert r.candidate_date_sources == ["advice"] * 3
    assert r.flag.startswith("AMBIGUOUS - 3 bills share this amount")
    assert "picked bill B3" in r.flag
    assert "closest advice date" in r.flag and "(10d; others 17d, 21d)" in r.flag
    assert "arbitrarily" not in r.flag


def test_candidates_payload_carries_gap_pick_first():
    bank, bills = _frames(
        [_bank()],
        [_bill(1, advice="2026-08-11"), _bill(2, advice="2026-08-07"),
         _bill(3, advice="2026-08-18")])
    out = reconcile(bank, bills, pd.DataFrame(), window_days=0)
    q = exception_queue(out)
    review = q[q["exception_type"] == "MATCH_REVIEW"]
    assert len(review) == 1
    row = review.iloc[0]
    cands = row["Candidates"]
    assert [c["bill_number"] for c in cands] == ["B3", "B1", "B2"]
    assert cands[0]["Picked"] is True and cands[0]["DateGapDays"] == 10
    assert cands[0]["DateSource"] == "advice"
    assert [c["DateGapDays"] for c in cands] == [10, 17, 21]
    assert all(c["Picked"] is False for c in cands[1:])
    assert row["CandidateSummary"].startswith("3 candidate(s): *B3 (SER, 2026-08-18, 10d)")
    # the review copy explains the rule instead of calling the pick arbitrary
    assert "closest" in row["action"] and "arbitrary" not in row["action"]
    # list helper columns never leak into the queue
    for col in ("candidate_gaps", "candidate_date_sources", "candidate_indices"):
        assert col not in q.columns
    assert "candidate_gaps" in out["matched"].columns


def test_fallback_date_competes_on_pure_gap():
    """A bill with no advice date but a CO7 date 6d away beats a bill
    advised 27d away — the golden AMBIGUOUS row's shape."""
    bank, bills = _frames(
        [_bank()],
        [_bill(1, advice="2026-08-01"),                 # 27d, advice
         _bill(2, advice=None, order="2026-08-22")])    # 6d, co7 fallback
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.confidence == "AMBIGUOUS"
    assert r.bill_number == "B2"
    assert r.date_gap_days == 6 and r.date_source == "co7"
    assert r.candidate_indices == [1, 0]
    assert r.candidate_date_sources == ["co7", "advice"]
    assert "closest pay order date" in r.flag and "(6d; others 27d)" in r.flag


def test_undated_bill_sorts_last_and_all_undated_is_arbitrary():
    bank, bills = _frames(
        [_bank()],
        [_bill(1), _bill(2, advice="2026-08-11")])
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.bill_number == "B2"
    assert r.candidate_indices == [1, 0]
    assert r.candidate_gaps == [17, None]
    assert "(17d; others no date)" in r.flag

    bank, bills = _frames([_bank()], [_bill(1), _bill(2)])
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.confidence == "AMBIGUOUS"
    assert r.bill_number == "B1"          # lowest bill index
    assert r.date_gap_days is None
    assert r.flag.endswith("picked bill B1 arbitrarily (no bill dates to compare)")


def test_equal_gaps_keep_legacy_index_order():
    bank, bills = _frames(
        [_bank()],
        [_bill(1, advice="2026-08-18"), _bill(2, advice="2026-08-18"),
         _bill(3, advice="2026-08-18")])
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.bill_number == "B1"
    assert r.candidate_indices == [0, 1, 2]
    assert r.candidate_gaps == [10, 10, 10]


def test_stolen_closest_bill_still_lists_actual_pick_first():
    """Two credits, same amount. The first credit claims the closest bill;
    the second must lead its candidate list with the bill it actually got,
    not the one taken from it."""
    bank, bills = _frames(
        [_bank(bank_ref="R1"), _bank(bank_ref="R2")],
        [_bill(1, advice="2026-08-18"),    # 10d — claimed by R1
         _bill(2, advice="2026-08-11")])   # 17d — left for R2
    results, _ = match_bank_to_billstatus(bank, bills)
    by_ref = {r.bank_ref: r for r in results}
    assert by_ref["R1"].bill_number == "B1"
    assert by_ref["R1"].candidate_indices == [0, 1]
    assert by_ref["R2"].bill_number == "B2"
    assert by_ref["R2"].candidate_indices == [1, 0]
    assert by_ref["R2"].candidate_gaps == [17, 10]
    assert by_ref["R2"].date_gap_days == 17


def test_non_tied_outcomes_unchanged():
    """Scoring is untouched: a HIGH pairing is still HIGH with no flag,
    and a single over-tolerance bill is LOW with the legacy wording."""
    bank, bills = _frames([_bank(zone_guess="SER")],
                          [_bill(1, advice="2026-08-28")])
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.confidence == "HIGH" and r.flag == ""
    assert r.candidate_indices == [0] and r.candidate_gaps == [0]

    bank, bills = _frames([_bank(zone_guess="SER")],
                          [_bill(1, advice="2026-08-07")])
    r = _one(match_bank_to_billstatus(bank, bills)[0])
    assert r.confidence == "LOW"
    assert r.flag.startswith("REVIEW - amount and zone matched; date unconfirmed (gap 21d")
