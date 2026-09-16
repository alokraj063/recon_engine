"""
Credits whose only same-amount bill is still IN FLIGHT in the source
system (bill_status PASSED / REGISTERED) could never have matched — the
matcher only considers paid-status bills — so they are reported on their
own count and kept OUT of the match-rate denominator, exactly like an
unrecognised receipt but for a different reason.

Reporting only: the exception rows themselves are untouched (same
BANK_ONLY row, same frozen gap code), which is what makes this safe to
change without re-baselining the golden master.

Guards covered here:
  * RETURNED is NOT in-flight — money against a returned bill stays in
    the rate, where an analyst can see it.
  * the date guard — a bill submitted AFTER the credit arrived cannot
    explain it, so that credit stays in the rate.
  * no double subtraction — an unrecognised credit that happens to share
    an amount with an in-flight bill is counted once, as unrecognised.
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, overview  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import ExceptionLedger, GoldBankTxn  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from tests.test_multi_statement import world  # noqa: E402,F401

VALUE_DAY = "2026-03-18"


def credits(rows):
    """rows: (bank_ref, amount, zone_guess or None)."""
    df = ensure_schema(pd.DataFrame([
        {"bank_ref": r, "value_date": pd.Timestamp(VALUE_DAY), "amount": a,
         "zone_guess": z, "narrative": f"NEFT FROM {r}", "used_in_recon": True}
        for r, a, z in rows]), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def bills(rows):
    """rows: (bill_number, amount, status, submission_day)."""
    df = ensure_schema(pd.DataFrame([
        {"bill_number": n, "submission_ref": f"CO6-{n}",
         "submission_date": pd.Timestamp(sub), "net_payable_amount": amt,
         "bill_status": status, "zone": "NR", "data_row": i + 2,
         # only a PAID bill carries an advice date; in-flight bills have
         # none, which is exactly why the matcher cannot use them
         "payment_advice_date": (pd.Timestamp(VALUE_DAY)
                                 if status == "PAYMENT MADE" else pd.NaT)}
        for i, (n, amt, status, sub) in enumerate(rows)]), "bills")
    df["row_seq"] = range(len(df))
    return df


def _ingest(pk, frames, bronze_ids):
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, frames, bronze_ids)
        s.commit()


def _reconcile(client, key, stmt):
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [stmt],
        "mode": "incremental"})
    assert r.status_code == 200, r.text
    return r.json()


def _open_bank_gaps(pk):
    with SessionLocal() as s:
        rows = list(s.execute(
            select(ExceptionLedger, GoldBankTxn)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == pk,
                   ExceptionLedger.exception_type == "BANK_ONLY",
                   ExceptionLedger.status == "OPEN")))
        return {t.bank_ref: e.gap_type for e, t in rows}


def test_in_flight_bills_are_counted_not_rated(world):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills([
        ("B1", 4000.0, "PAYMENT MADE", "2026-03-10"),   # settles C0
        ("P1", 5000.0, "PASSED", "2026-03-10"),         # C1 awaits status
        ("R1", 6000.0, "REGISTERED", "2026-03-10"),     # C2 awaits status
        ("T1", 7000.0, "RETURNED", "2026-03-10"),       # C3 stays rated
        ("F1", 8000.0, "PASSED", "2026-03-25"),         # submitted AFTER C4
        ("P2", 9000.0, "PASSED", "2026-03-10"),         # shares C5's amount
    ])}, {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([
        ("C0", 4000.0, "NR"), ("C1", 5000.0, "NR"), ("C2", 6000.0, "NR"),
        ("C3", 7000.0, "NR"), ("C4", 8000.0, "NR"),
        ("C5", 9000.0, None),                           # no signal at all
    ])}, {"bank_txns": bz["a"]})

    payload = _reconcile(client, key, bz["a"])
    counts = payload["meta"]["counts"]
    assert counts["bank_credits"] == 6 and counts["matched"] == 1
    assert counts["bank_only"] == 5

    # the exception rows are untouched — this is a REPORTING change only
    assert _open_bank_gaps(pk) == {
        "C1": "SIGNAL_BILL_NOT_FOUND", "C2": "SIGNAL_BILL_NOT_FOUND",
        "C3": "SIGNAL_BILL_NOT_FOUND", "C4": "SIGNAL_BILL_NOT_FOUND",
        "C5": "UNRECOGNISED_RECEIPT"}

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
        ar = overview.ar_view(s, pk)

    assert ov["gold"]["credits"] == 6
    assert ov["matched_credits"] == 1 and ov["settled_credits"] == 1
    # C1 (PASSED) + C2 (REGISTERED) only: C3 is RETURNED, C4's bill was
    # submitted after the credit, C5 is already counted as unrecognised
    assert ov["awaiting_status_credits"] == 2
    assert ov["unrecognised_credits"] == 1
    assert ov["recognised_credits"] == 3          # 6 - 1 unrecognised - 2 awaiting
    assert abs(ov["match_rate"] - 1 / 3) < 1e-9   # 1 settled of 3, not of 6
    # every credit is still an open exception; only the RATE changed
    assert ov["open_exceptions"]["BANK_ONLY"] == 5

    assert ar["kpis"]["awaiting_status"] == 2
    assert ar["kpis"]["unrecognised"] == 1
    assert abs(ar["kpis"]["match_rate"] - 1 / 3) < 1e-9


def test_paid_bill_never_counts_as_awaiting(world):  # noqa: F811
    """A credit with no bill at all keeps counting against the rate — the
    exclusion needs a real in-flight bill at the same amount."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills([("B1", 4000.0, "PAYMENT MADE", "2026-03-10")])},
            {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([("C0", 4000.0, "NR"),
                                       ("C9", 12345.0, "NR")])},
            {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    assert ov["awaiting_status_credits"] == 0
    assert ov["recognised_credits"] == 2 and ov["match_rate"] == 0.5
