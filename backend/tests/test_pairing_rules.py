"""
Three pairing rules added 2026-09-30 after the 31 Aug-22 Sep real load:

  * a bill number written in the credit's narrative (ReferenceSignal) wins
    an AMBIGUOUS tie — ICF/RCF write it on every payment;
  * the pairing window (MatchRuleSet.max_pairing_gap_days) keeps a bill
    advised long before a credit — or after it — from pairing with it;
  * rescore_provisional releases an untouched weak match whose bill a later
    credit pairs HIGH with (the M-274 case), or whose bill the window no
    longer allows.
"""

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, incremental  # noqa: E402
from db.models import AuditLog, GoldBill, MatchLedgerBill  # noqa: E402
from recon.matching.matcher import match_bank_to_billstatus  # noqa: E402
from recon.matching.scoring import pairing_allowed, reference_hits  # noqa: E402
from recon.rules import FieldMapping  # noqa: E402
from tests.test_ledger_decisions import RULES, _bank, _bills  # noqa: E402
from tests.test_rescore_provisional import (_exc, _ingest, _matches,  # noqa: E402,F401
                                            world)

D = pd.Timestamp("2026-09-16")


# ------------------------------------------------------------------ #
# reference signal
# ------------------------------------------------------------------ #
def _ref(narrative, bill_number):
    return reference_hits({"narrative": narrative}, {"bill_number": bill_number})


def test_bill_number_in_narrative_is_found_standing_alone():
    n = "NEFT FROM 1301ICF2002260301912 10 SBINN52026091945080223 SBOI"
    assert _ref(n, "2002260301912")
    assert not _ref(n, "2002260300985")
    # a slice of a longer digit run (the UTR) is not a reference
    assert not _ref(n, "2026091945")
    # too short to trust
    assert not _ref("NEFT FROM 1301ICF12345 10", "12345")


def _ireps_bank(narratives):
    return pd.DataFrame({"bank_ref": [f"B{i}" for i in range(len(narratives))],
                         "narrative": narratives, "amount": 4173070.0,
                         "value_date": D, "zone_guess": "ICF"})


def _ireps_bills(numbers, advice=D):
    return pd.DataFrame({"bill_number": numbers, "zone": "ICF",
                         "net_payable_amount": 4173070.0,
                         "bill_status": "PAYMENT MADE",
                         "payment_advice_date": advice,
                         "payment_order_date": advice,
                         "contract_no": None, "payment_order_ref": None,
                         "org_unit": None})


def test_named_bill_breaks_an_ambiguous_tie():
    bank = _ireps_bank(["NEFT FROM 1301ICF2002260301912 10 SBINN5202609194"])
    bills = _ireps_bills(["2002260300985", "2002260301912"])
    (r,), _ = match_bank_to_billstatus(bank, bills)
    assert r.bill_number == "2002260301912"
    assert r.confidence == "HIGH" and r.tied_candidates == 1

    # switched off, it is the historical closest-date tie again
    (r,), _ = match_bank_to_billstatus(
        bank, bills, mapping=FieldMapping(reference_signals=()))
    assert r.confidence == "AMBIGUOUS"


def test_a_named_bill_with_a_worse_date_still_wins_but_stays_review():
    bank = _ireps_bank(["NEFT FROM 1301ICF2002260301912 10 SBINN5202609194"])
    bills = pd.concat([_ireps_bills(["2002260300985"]),
                       _ireps_bills(["2002260301912"], D - pd.Timedelta(days=9))],
                      ignore_index=True)
    (r,), _ = match_bank_to_billstatus(bank, bills)
    assert r.bill_number == "2002260301912"
    # the label still reads zone + date only: the date is off, so LOW
    assert r.confidence == "LOW" and "named in the credit's narrative" in r.flag


# ------------------------------------------------------------------ #
# pairing window
# ------------------------------------------------------------------ #
def test_pairing_window_bounds():
    assert pairing_allowed(D, D - pd.Timedelta(days=10), 2, 10)
    assert not pairing_allowed(D, D - pd.Timedelta(days=11), 2, 10)
    assert pairing_allowed(D, D + pd.Timedelta(days=2), 2, 10)
    # advised a week after the money arrived (M-274)
    assert not pairing_allowed(D, D + pd.Timedelta(days=7), 2, 10)
    assert pairing_allowed(D, None, 2, 10)                 # undated: not judged
    assert pairing_allowed(D, D - pd.Timedelta(days=400), 2, None)


def test_old_bill_is_no_candidate_inside_a_window():
    bank = _ireps_bank(["NEFT FROM 1301ICF SUPPLY SBINN5202609194"])
    old = _ireps_bills(["2002260302074"], D - pd.Timedelta(days=81))
    results, unmatched = match_bank_to_billstatus(bank, old)
    assert len(results) == 1 and results[0].confidence == "LOW"   # historical
    results, unmatched = match_bank_to_billstatus(bank, old,
                                                  max_pairing_gap_days=10)
    assert results == [] and list(unmatched["gap_type"]) == ["SIGNAL_BILL_NOT_FOUND"]


# ------------------------------------------------------------------ #
# rescore_provisional releases
# ------------------------------------------------------------------ #
def _dated_bank(rows, when):
    df = _bank(rows)
    df["value_date"] = when
    return df


def _dated_bills(rows, advice):
    df = _bills(rows)
    df["payment_advice_date"] = advice
    return df


def _run_with(cust, stmt_bronze, rules):
    from db.models import Run
    run_id = incremental.start_run(cust, {})
    with SessionLocal() as s:
        rescored = incremental.rescore_provisional(s, cust, run_id, stmt_bronze, rules)
        out, bank_pos, bill_pos = incremental.run_matching(s, cust, stmt_bronze, rules)
        incremental.finalize_ledger(s, cust, run_id, out, bank_pos, bill_pos)
        s.get(Run, run_id).status = "succeeded"
        s.commit()
    return rescored


def _picked(match_id):
    with SessionLocal() as s:
        return {b.bill_number for b in s.execute(
            select(GoldBill).join(MatchLedgerBill, MatchLedgerBill.gold_bill_id == GoldBill.id)
            .where(MatchLedgerBill.match_ledger_id == match_id,
                   MatchLedgerBill.role == "picked")).scalars()}


def _release_events(cust):
    with SessionLocal() as s:
        return [e.details for e in s.execute(select(AuditLog).where(
            AuditLog.customer_id == cust,
            AuditLog.event_type == "ledger.match_superseded")).scalars()]


def test_a_later_credit_that_pairs_high_takes_the_bill_from_a_weak_match(world):
    """M-274: an 08-Sep credit took a bill advised 15-Sep (LOW); the exact
    credit arrived 16-Sep and must get it."""
    cust, bz = world["cust"], world["bz"]
    early = D - pd.Timedelta(days=8)
    _ingest(cust, _dated_bank([("T1", "V1", 1000.0), ("T2", "V8", 500.0)], early),
            _dated_bills([("B1", "V1", 1000.0)], D - pd.Timedelta(days=1)),
            bz["stmt"], bz["bills"])
    _run_with(cust, bz["stmt"], RULES)
    (weak,) = _matches(cust)
    assert weak.confidence == "LOW" and weak.status == "OPEN"

    _ingest(cust, _dated_bank([("T3", "V1", 1000.0)], D), _bills([]),
            bz["stmt2"], bz["bills2"])
    rescored = _run_with(cust, bz["stmt2"], RULES)

    assert rescored["provisional_released"] == 1
    old, new = _matches(cust)
    assert (old.status, old.locked_by) == ("REJECTED", incremental.SUPERSEDED_BY)
    assert old.decision_note == incremental.RELEASE_NOTES["BILL_TAKEN_BY_HIGH"]
    assert (new.confidence, new.status) == ("HIGH", "LOCKED")
    assert new.gold_bank_txn_id != old.gold_bank_txn_id and _picked(new.id) == {"B1"}
    # the early credit is an open exception again
    assert [e.gold_bank_txn_id for e in _exc(cust, exception_type="BANK_ONLY",
                                             status="OPEN")
            if e.gold_bank_txn_id == old.gold_bank_txn_id]
    assert [d["reason"] for d in _release_events(cust)] == ["BILL_TAKEN_BY_HIGH"]


def test_a_weak_match_outside_a_new_pairing_window_is_released(world):
    cust, bz = world["cust"], world["bz"]
    _ingest(cust, _dated_bank([("T1", "V1", 1000.0), ("T2", "V8", 500.0)], D),
            _dated_bills([("OLD", "V1", 1000.0)], D - pd.Timedelta(days=60)),
            bz["stmt"], bz["bills"])
    _run_with(cust, bz["stmt"], RULES)                     # no window yet
    (weak,) = _matches(cust)
    assert weak.status == "OPEN"

    windowed = replace(RULES, max_pairing_gap_days=10)
    rescored = _run_with(cust, bz["stmt"], windowed)
    assert rescored["provisional_released"] == 1
    (old,) = _matches(cust)                                # nothing re-pairs it
    assert old.status == "REJECTED"
    assert old.decision_note == incremental.RELEASE_NOTES["OUTSIDE_PAIRING_WINDOW"]
    assert [e for e in _exc(cust, exception_type="BANK_ONLY", status="OPEN")
            if e.gold_bank_txn_id == old.gold_bank_txn_id]

    # idempotent: the next run has nothing left to release
    assert _run_with(cust, bz["stmt"], windowed)["provisional_released"] == 0


def test_a_decided_weak_match_is_never_released(world):
    cust, bz = world["cust"], world["bz"]
    _ingest(cust, _dated_bank([("T1", "V1", 1000.0), ("T2", "V8", 500.0)], D),
            _dated_bills([("OLD", "V1", 1000.0)], D - pd.Timedelta(days=60)),
            bz["stmt"], bz["bills"])
    _run_with(cust, bz["stmt"], RULES)
    (weak,) = _matches(cust)
    with SessionLocal() as s:
        s.get(type(weak), weak.id).decided_by_user_id = 7
        s.commit()
    rescored = _run_with(cust, bz["stmt"], replace(RULES, max_pairing_gap_days=10))
    assert rescored["provisional_released"] == 0
    assert [(m.id, m.status) for m in _matches(cust)] == [(weak.id, "OPEN")]
