"""
Every credit is either IN SCOPE (carries this source's match signal — an
IREPS railway payment) or OUT OF SCOPE (a receipt from anywhere else:
interest, sweeps, other payers). The overview reports both as counts AND
money, and the match rate's denominator is built from the same split, so
the two can never drift apart.

The split rides the stored gap code, which the engine stamps through the
customer's own field map — deliberately NOT a hardcoded zone column, so a
customer matching on some other signal splits correctly too.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, overview  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from tests.test_multi_statement import world  # noqa: E402,F401

DAY = "2026-03-18"


def credits(rows):
    """rows: (bank_ref, amount, zone_guess or None)."""
    df = ensure_schema(pd.DataFrame([
        {"bank_ref": r, "value_date": pd.Timestamp(DAY), "amount": a,
         "zone_guess": z, "narrative": f"NEFT FROM {r}", "used_in_recon": True}
        for r, a, z in rows]), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def bills(rows):
    """rows: (bill_number, amount) — all PAYMENT MADE, advised on DAY."""
    df = ensure_schema(pd.DataFrame([
        {"bill_number": n, "submission_ref": f"CO6-{n}",
         "submission_date": pd.Timestamp("2026-03-10"),
         "net_payable_amount": amt, "bill_status": "PAYMENT MADE",
         "payment_advice_date": pd.Timestamp(DAY),
         "payment_order_date": pd.Timestamp(DAY),
         "zone": "NR", "data_row": i + 2}
        for i, (n, amt) in enumerate(rows)]), "bills")
    df["row_seq"] = range(len(df))
    return df


def _ingest(pk, frames, bronze_ids):
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, frames, bronze_ids)
        s.commit()


def test_in_scope_and_out_of_scope_split(world):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills([("B1", 4000.0)])}, {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([
        ("C0", 4000.0, "NR"),      # in scope, settles B1
        ("C1", 7000.0, "NR"),      # in scope, no bill -> a real gap
        ("C2", 9000.0, None),      # out of scope (bank interest & co)
        ("C3", 500.0, None),       # out of scope
    ])}, {"bank_txns": bz["a"]})
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [bz["a"]],
        "mode": "incremental"})
    assert r.status_code == 200, r.text

    with SessionLocal() as s:
        ov = overview.overview(s, pk)

    assert ov["gold"]["credits"] == 4
    assert ov["in_scope_credits"] == 2 and ov["out_of_scope_credits"] == 2
    # money, not just counts — the value story is why this split exists
    assert ov["in_scope_value"] == 11000.0      # 4000 + 7000
    assert ov["out_of_scope_value"] == 9500.0   # 9000 + 500
    # the two sides account for every credit and every rupee
    assert ov["in_scope_credits"] + ov["out_of_scope_credits"] == ov["gold"]["credits"]
    assert ov["in_scope_value"] + ov["out_of_scope_value"] == 20500.0

    # ONE definition: the rate's denominator comes from the same split
    assert ov["unrecognised_credits"] == ov["out_of_scope_credits"]
    assert ov["recognised_credits"] == 2        # nothing is awaiting here
    assert ov["match_rate"] == 0.5              # 1 settled of 2 in scope


def test_split_follows_the_customers_signal_not_a_zone_column(world):  # noqa: F811
    """The signal is per-customer config. A customer matching on
    `customer_ref` must still split correctly, which is why the count
    reads the engine-stamped gap code rather than zone_guess."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    r = client.put(f"/api/customers/{key}/config", json={
        "date_tolerance_days": 2, "amount_tolerance": 0.0, "window_days": 0,
        "co7_lookback_days": 5, "allow_batched": True, "max_batch_size": 3,
        "paid_statuses": ["PAYMENT MADE", "CO7 DONE"],
        "weights": {"advice_date": 4, "zone": 2, "co7_date": 1},
        # the API takes a COMPLETE field map, so the defaults ride along
        # and only the signal differs from the stock mapping
        "field_map": {
            "bank_amount_field": "amount",
            "bill_amount_field": "net_payable_amount",
            "bank_date_field": "value_date",
            "bill_date_primary": "payment_advice_date",
            "bill_date_fallback": "payment_order_date",
            "eligibility_field": "bill_status",
            "fallback_due_statuses": ["CO7 DONE"],
            "exact_signals": [
                {"bank_field": "customer_ref", "bill_field": "bill_number",
                 "weight": 2, "key": "zone"}],
        },
    })
    assert r.status_code == 200, r.text

    df = credits([("C1", 5000.0, None), ("C2", 6000.0, None)])
    # the configured signal carries the value; zone_guess stays empty
    df.loc[0, "customer_ref"] = "B1"
    _ingest(pk, {"bank_txns": df}, {"bank_txns": bz["a"]})
    _ingest(pk, {"bills": bills([("B1", 5000.0)])}, {"bills": bz["bills"]})
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [bz["a"]],
        "mode": "incremental"})
    assert r.status_code == 200, r.text

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    # C1 carries the signal (and matches); C2 carries nothing at all
    assert ov["in_scope_credits"] == 1 and ov["out_of_scope_credits"] == 1
    assert ov["in_scope_value"] == 5000.0 and ov["out_of_scope_value"] == 6000.0


def test_gold_bank_rows_carry_the_bucket_each_credit_is_counted_in(world):  # noqa: F811
    """The Command Center's funnel figures open Bank Transactions filtered
    on `credit_scope`, so the per-row reading must partition the credits
    exactly the way the overview counts them — else "IREPS credits 2"
    opens a table of 3."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills([("B1", 4000.0)])}, {"bills": bz["bills"]})
    df = credits([
        ("C0", 4000.0, "NR"),      # in scope, settles B1
        ("C1", 7000.0, "NR"),      # in scope, real gap
        ("C2", 9000.0, None),      # other receipt
    ])
    debit = df.iloc[[0]].copy()
    debit["bank_ref"], debit["amount"], debit["used_in_recon"] = "D0", 100.0, False
    debit["row_seq"] = 3
    df = pd.concat([df, debit], ignore_index=True)
    _ingest(pk, {"bank_txns": df}, {"bank_txns": bz["a"]})
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [bz["a"]],
        "mode": "incremental"})
    assert r.status_code == 200, r.text

    r = client.get("/api/gold/bank", params={"customer_id": key})
    assert r.status_code == 200, r.text
    scope = {row["bank_ref"]: row["credit_scope"] for row in r.json()["rows"]}
    assert scope == {"C0": "RECOGNISED", "C1": "RECOGNISED",
                     "C2": "UNRECOGNISED_RECEIPT", "D0": None}

    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    in_scope = {"RECOGNISED", "AWAITING_STATUS", "AWAITING_BILL_DATA"}
    assert sum(v in in_scope for v in scope.values()) == ov["in_scope_credits"]
    assert sum(v == "RECOGNISED" for v in scope.values()) == ov["recognised_credits"]
    assert sum(v is not None for v in scope.values()) == ov["gold"]["credits"]
