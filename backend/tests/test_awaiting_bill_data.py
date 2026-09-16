"""
A credit valued PAST the bill export's coverage could not have matched:
the export carrying its payment advice has not been ingested yet. Such
credits are reported separately and kept out of the match-rate
denominator — but only until they go stale (AWAITING_BILL_DATA_CAP_DAYS),
so a feed that stops arriving surfaces instead of hiding.

Coverage is derived, never guessed (db/overview.coverage):
  bills_covered_through  latest payment advice in gold
  data_as_of             latest credit value date — the data's own
                         "today", so a historical replay ages like a live
                         feed instead of against wall-clock time.

Reporting only: the exception rows and their frozen gap codes are
untouched, so the golden master is unaffected.
"""

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, overview  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import ExceptionLedger  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from tests.test_multi_statement import world  # noqa: E402,F401

CAP = overview.AWAITING_BILL_DATA_CAP_DAYS      # 5 days


def credits(rows):
    """rows: (bank_ref, amount, value_day)."""
    df = ensure_schema(pd.DataFrame([
        {"bank_ref": r, "value_date": pd.Timestamp(day), "amount": a,
         "zone_guess": "NR", "narrative": f"NEFT FROM {r}",
         "used_in_recon": True}
        for r, a, day in rows]), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def bills(rows):
    """rows: (bill_number, amount, advice_day) — all PAYMENT MADE."""
    df = ensure_schema(pd.DataFrame([
        {"bill_number": n, "submission_ref": f"CO6-{n}",
         "submission_date": pd.Timestamp("2026-03-01"),
         "net_payable_amount": amt, "bill_status": "PAYMENT MADE",
         "payment_advice_date": pd.Timestamp(advice),
         "payment_order_date": pd.Timestamp(advice),
         "zone": "NR", "data_row": i + 2}
        for i, (n, amt, advice) in enumerate(rows)]), "bills")
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


def test_credit_past_bill_coverage_is_counted_not_rated(world):  # noqa: F811
    """Bills advised through 10 Mar; credits land on 10 and 11 Mar. The
    11 Mar credit has no bill data yet — its export has not arrived."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills([("B1", 4000.0, "2026-03-10")])},
            {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([
        ("C0", 4000.0, "2026-03-10"),     # settles B1
        ("C1", 7000.0, "2026-03-10"),     # within coverage -> a real gap
        ("C2", 8000.0, "2026-03-11"),     # past coverage -> awaiting data
    ])}, {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
        ar = overview.ar_view(s, pk)

    assert ov["bills_covered_through"] == "2026-03-10"
    assert ov["data_as_of"] == "2026-03-11"
    assert ov["awaiting_bill_data_credits"] == 1        # C2 only
    assert ov["recognised_credits"] == 2                # 3 - 1 awaiting
    assert ov["matched_credits"] == 1 and ov["match_rate"] == 0.5
    # nothing was resolved or relabelled — both credits are still open
    assert ov["open_exceptions"]["BANK_ONLY"] == 2
    # but only C1 is open WORK; C2 waits for its export, named apart
    assert ov["open_in_scope"]["bank_only"] == 1
    assert ov["open_in_scope"]["awaiting"] == 1
    assert ov["open_in_scope"]["awaiting_value"] == 8000.0
    assert [e["ref"] for e in ov["top_exceptions"]] == ["C1"]
    assert ar["kpis"]["awaiting_bill_data"] == 1
    assert ar["kpis"]["match_rate"] == 0.5


def test_the_excuse_expires_once_the_feed_falls_behind(world):  # noqa: F811
    """More statements arrive but no new bill export: the same credit is
    now stale beyond the cap and counts against the rate again."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills([("B1", 4000.0, "2026-03-10")])},
            {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([("C0", 4000.0, "2026-03-10"),
                                       ("C2", 8000.0, "2026-03-11")])},
            {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])
    with SessionLocal() as s:
        assert overview.overview(s, pk)["awaiting_bill_data_credits"] == 1

    # a later statement, still no bills export: data_as_of moves past the
    # cap while bill coverage stays at 10 Mar
    late = pd.Timestamp("2026-03-11") + pd.Timedelta(days=CAP + 1)
    _ingest(pk, {"bank_txns": credits([("C3", 9000.0, late.date().isoformat())])},
            {"bank_txns": bz["b"]})
    _reconcile(client, key, bz["b"])

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    assert ov["data_as_of"] == late.date().isoformat()
    # C2 aged out; C3 is itself past coverage but still inside the cap
    assert ov["awaiting_bill_data_credits"] == 1
    refs = _open_refs(pk)
    assert refs == {"C2", "C3"}                 # both still open exceptions
    assert ov["recognised_credits"] == 2        # 3 credits - 1 awaiting (C3)


def test_no_bills_at_all_excuses_nothing(world):  # noqa: F811
    """With an empty bill layer there is no coverage to reason from, so
    every credit keeps counting — an unreconciled customer must not look
    like a waiting one."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bank_txns": credits([("C1", 5000.0, "2026-03-10")])},
            {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    assert ov["bills_covered_through"] is None
    assert ov["awaiting_bill_data_credits"] == 0
    assert ov["recognised_credits"] == 1 and ov["match_rate"] == 0.0


def _open_refs(pk):
    from db.models import GoldBankTxn
    with SessionLocal() as s:
        return {t.bank_ref for t in s.execute(
            select(GoldBankTxn)
            .join(ExceptionLedger,
                  ExceptionLedger.gold_bank_txn_id == GoldBankTxn.id)
            .where(ExceptionLedger.customer_id == pk,
                   ExceptionLedger.exception_type == "BANK_ONLY",
                   ExceptionLedger.status == "OPEN")).scalars()}
