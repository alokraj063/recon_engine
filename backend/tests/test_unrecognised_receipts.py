"""
Unrecognised receipts (gap_type UNRECOGNISED_RECEIPT: no match signal in
the narrative) are stored on the exception ledger, kept OUT of every
match-rate denominator, and reported as their own count — overview(),
ar_view() and the run payload. Legacy rows with NULL gap_type fall back
to "blank zone_guess"; a carried credit's code refreshes on the next run.
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
from tests.test_multi_statement import bills, world  # noqa: E402,F401


def credits(rows):
    """rows: (bank_ref, amount, zone_guess or None)."""
    df = ensure_schema(pd.DataFrame([
        {"bank_ref": r, "value_date": pd.Timestamp("2026-03-18"), "amount": a,
         "zone_guess": z, "narrative": f"NEFT FROM {r}", "used_in_recon": True}
        for r, a, z in rows]), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def _ingest(pk, frames, bronze_ids):
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, frames, bronze_ids)
        s.commit()


def _reconcile(client, key, stmt):
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [stmt], "mode": "incremental"})
    assert r.status_code == 200, r.text
    return r.json()


def _gaps(pk):
    with SessionLocal() as s:
        rows = list(s.execute(
            select(ExceptionLedger, GoldBankTxn)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == pk,
                   ExceptionLedger.exception_type == "BANK_ONLY",
                   ExceptionLedger.status == "OPEN")))
        return {t.bank_ref: e.gap_type for e, t in rows}


def test_unrecognised_receipts_are_counted_not_rated(world):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    # B1 (NR, 5000) is paid by C1; C2 has a zone but no bill; C3 has NO zone
    _ingest(pk, {"bills": bills(["B1"])}, {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([("C1", 5000.0, "NR"), ("C2", 7000.0, "NR"),
                                       ("C3", 9000.0, None)])},
            {"bank_txns": bz["a"]})

    payload = _reconcile(client, key, bz["a"])
    counts = payload["meta"]["counts"]
    assert counts["bank_credits"] == 3 and counts["bank_only"] == 2
    assert counts["unrecognised_receipts"] == 1

    assert _gaps(pk) == {"C2": "SIGNAL_BILL_NOT_FOUND", "C3": "UNRECOGNISED_RECEIPT"}

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
        ar = overview.ar_view(s, pk)
    assert ov["gold"]["credits"] == 3
    assert ov["matched_credits"] == 1 and ov["settled_credits"] == 1   # HIGH auto-locks
    assert ov["unrecognised_credits"] == 1 and ov["recognised_credits"] == 2
    assert ov["match_rate"] == 0.5                      # 1 of 2 RECOGNISED, not 1 of 3
    assert ov["open_exceptions"] == {"BANK_ONLY": 2, "BILL_ONLY": 0, "UNRECOGNISED": 1}
    assert ar["kpis"]["match_rate"] == 0.5 and ar["kpis"]["unrecognised"] == 1
    assert ar["runs"][0]["credits"] == 3 and ar["runs"][0]["unrecognised"] == 1

    # the ledger API exposes the code
    ledger = client.get("/api/ledger", params={"customer_id": key}).json()
    by_ref = {e["txn"]["bank_ref"]: e["gap_type"] for e in ledger["exceptions"] if e["txn"]}
    assert by_ref == {"C2": "SIGNAL_BILL_NOT_FOUND", "C3": "UNRECOGNISED_RECEIPT"}

    # legacy row (NULL gap_type) still counts as unrecognised via blank zone
    with SessionLocal() as s:
        for e in s.execute(select(ExceptionLedger).where(
                ExceptionLedger.customer_id == pk)).scalars():
            e.gap_type = None
        s.commit()
        ov = overview.overview(s, pk)
    assert ov["unrecognised_credits"] == 1 and ov["match_rate"] == 0.5

    # a zone backfill on C3 + the next run refreshes the carried row's code
    with SessionLocal() as s:
        t = s.execute(select(GoldBankTxn).where(GoldBankTxn.customer_id == pk,
                                                GoldBankTxn.bank_ref == "C3")).scalar_one()
        t.zone_guess = "NR"
        s.commit()
    payload = _reconcile(client, key, bz["a"])
    assert payload["meta"]["counts"]["unrecognised_receipts"] == 0
    assert _gaps(pk) == {"C2": "SIGNAL_BILL_NOT_FOUND", "C3": "SIGNAL_BILL_NOT_FOUND"}
    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    assert ov["unrecognised_credits"] == 0 and ov["recognised_credits"] == 3
    assert abs(ov["match_rate"] - 1 / 3) < 1e-9
