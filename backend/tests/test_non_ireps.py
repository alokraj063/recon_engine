"""
Non-IREPS receipts are kept apart from matching. A credit with no match
signal (blank zone under the default mapping) is never offered to the
matcher in an incremental run: it goes straight to the ledger as an OPEN
UNRECOGNISED_RECEIPT exception, even when a bill of its amount exists.
The Analyst queue's Non-IREPS tab then decides it:

  approve  -> RESOLVED as USER_NON_IREPS, still an "Other receipt", never
              counted as a resolved exception, never pooled again
  reject   -> the credit is IREPS money: stays OPEN (read-time gap
              MARKED_IREPS), and the next run DOES match it
  undo     -> drops the decision; an approved row re-opens

The Bank Transactions page's Source column reads the same definition.
"""

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, overview  # noqa: E402
from db.models import ExceptionLedger, GoldBankTxn, MatchLedger  # noqa: E402
from tests.test_multi_statement import bills, world  # noqa: E402,F401
from tests.test_unrecognised_receipts import (_ingest, _reconcile,  # noqa: E402
                                              credits)


def _ledger(client, key):
    r = client.get("/api/ledger", params={"customer_id": key})
    assert r.status_code == 200, r.text
    return {e["txn"]["bank_ref"]: e for e in r.json()["exceptions"] if e["txn"]}


def _sources(client, key):
    r = client.get("/api/gold/bank", params={"customer_id": key})
    assert r.status_code == 200, r.text
    return {row["bank_ref"]: row["source"] for row in r.json()["rows"]}


def _matched_refs(pk):
    with SessionLocal() as s:
        return set(s.execute(
            select(GoldBankTxn.bank_ref)
            .join(MatchLedger, MatchLedger.gold_bank_txn_id == GoldBankTxn.id)
            .where(MatchLedger.customer_id == pk,
                   MatchLedger.status != "REJECTED")).scalars())


def test_non_ireps_kept_out_of_matching_and_decided(world):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    # B1 = 5000, B2 = 5001, both NR. C1 (NR) pays B1. C3 has NO zone but
    # the amount of B2 — before this change it matched B2 on amount alone.
    # C4 has no zone and no bill.
    _ingest(pk, {"bills": bills(["B1", "B2"])}, {"bills": bz["bills"]})
    # D1 is a debit: never reconciled, its Source reads DEBIT
    bank = credits([("C1", 5000.0, "NR"), ("C3", 5001.0, None),
                    ("C4", 7000.0, None), ("D1", 900.0, None)])
    bank.loc[bank["bank_ref"] == "D1", "used_in_recon"] = False
    _ingest(pk, {"bank_txns": bank}, {"bank_txns": bz["a"]})

    # before any run the Source column already answers by the zone rule
    assert _sources(client, key) == {"C1": "IREPS", "C3": "NON_IREPS",
                                     "C4": "NON_IREPS", "D1": "DEBIT"}

    _reconcile(client, key, bz["a"])
    assert _matched_refs(pk) == {"C1"}                 # C3 was never scored
    exc = _ledger(client, key)
    assert {r: e["gap_detail"] for r, e in exc.items()} == {
        "C3": "UNRECOGNISED_RECEIPT", "C4": "UNRECOGNISED_RECEIPT"}
    assert _sources(client, key) == {"C1": "IREPS", "C3": "NON_IREPS",
                                     "C4": "NON_IREPS", "D1": "DEBIT"}

    # --- reject C3: it is IREPS money after all ----------------------------
    r = client.post(f"/api/exceptions/{exc['C3']['id']}/non-ireps/reject")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": exc["C3"]["id"], "status": "OPEN",
                        "resolved_by": None, "source_decision": "IREPS"}
    exc = _ledger(client, key)
    assert exc["C3"]["gap_detail"] == "MARKED_IREPS"
    assert exc["C3"]["source_decision"] == "IREPS"
    assert exc["C3"]["gap_label"]                       # copy resolves
    assert _sources(client, key)["C3"] == "IREPS"
    with SessionLocal() as s:
        ov = overview.overview(s, pk)
        assert overview.credit_scopes(s, pk)[
            s.execute(select(GoldBankTxn.id).where(
                GoldBankTxn.customer_id == pk,
                GoldBankTxn.bank_ref == "C3")).scalar_one()] == "RECOGNISED"
    assert ov["unrecognised_credits"] == 1               # C4 only
    assert ov["open_in_scope"]["bank_only"] == 1         # C3 is IREPS work now

    # ...so the next run offers it to the matcher, and it pairs with B2
    _reconcile(client, key, bz["a"])
    assert _matched_refs(pk) == {"C1", "C3"}

    # --- approve C4: a genuine non-IREPS receipt ----------------------------
    r = client.post(f"/api/exceptions/{exc['C4']['id']}/non-ireps/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "RESOLVED"
    assert r.json()["resolved_by"] == "USER_NON_IREPS"
    # a second decision on a resolved row is a conflict, not a silent no-op
    r = client.post(f"/api/exceptions/{exc['C4']['id']}/non-ireps/reject")
    assert r.status_code == 409
    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    # still an "Other receipt", not an open one, not a resolved exception
    assert ov["unrecognised_credits"] == 1 and ov["out_of_scope_credits"] == 1
    assert ov["open_exceptions"]["UNRECOGNISED"] == 0
    assert _sources(client, key)["C4"] == "NON_IREPS"

    # an approved receipt never re-opens on a later run
    _reconcile(client, key, bz["a"])
    with SessionLocal() as s:
        rows = list(s.execute(
            select(ExceptionLedger).join(
                GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == pk,
                   GoldBankTxn.bank_ref == "C4")).scalars())
    assert [(e.status, e.resolved_by) for e in rows] == [("RESOLVED", "USER_NON_IREPS")]

    # --- undo re-opens it ---------------------------------------------------
    r = client.post(f"/api/exceptions/{exc['C4']['id']}/non-ireps/undo")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "OPEN" and r.json()["source_decision"] is None
    assert _ledger(client, key)["C4"]["gap_detail"] == "UNRECOGNISED_RECEIPT"

    assert client.post(f"/api/exceptions/{exc['C4']['id']}/non-ireps/bogus").status_code == 404
    assert client.post("/api/exceptions/nope/non-ireps/approve").status_code == 404


def _put_source(client, key, txn_id, source):
    return client.put(f"/api/bank/{txn_id}/source",
                      json={"customer_id": key, "source": source})


def _bank_rows(client, key):
    r = client.get("/api/gold/bank", params={"customer_id": key})
    assert r.status_code == 200, r.text
    return {row["bank_ref"]: row for row in r.json()["rows"]}


def test_source_edited_from_bank_transactions(world):  # noqa: F811
    """The Bank Transactions Source edit is the same decision as the queue's
    Approve / Reject / Undo, made from the credit, and every reader follows."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills(["B1", "B2"])}, {"bills": bz["bills"]})
    bank = credits([("C1", 5000.0, "NR"), ("C5", 5001.0, None),
                    ("C6", 9000.0, "NR"), ("D1", 900.0, None)])
    bank.loc[bank["bank_ref"] == "D1", "used_in_recon"] = False
    _ingest(pk, {"bank_txns": bank}, {"bank_txns": bz["a"]})
    rows = _bank_rows(client, key)
    ids = {r: row["gold_bank_txn_id"] for r, row in rows.items()}
    assert rows["C5"]["source"] == "NON_IREPS" and rows["C5"]["source_decided"] is False

    # C5 has no zone but IS IREPS money: marked before any run, it is matched
    r = _put_source(client, key, ids["C5"], "IREPS")
    assert r.status_code == 200, r.text
    rows = _bank_rows(client, key)
    assert rows["C5"]["source"] == "IREPS" and rows["C5"]["source_decided"] is True
    _reconcile(client, key, bz["a"])
    assert _matched_refs(pk) == {"C1", "C5"}

    # a matched credit cannot become non-IREPS until its match is rejected
    assert _put_source(client, key, ids["C1"], "NON_IREPS").status_code == 409
    # a debit has no source to edit
    assert _put_source(client, key, ids["D1"], "IREPS").status_code == 400
    assert _put_source(client, key, ids["C6"], "BOGUS").status_code == 400

    # C6 (zone, no bill) is an open IREPS exception; set NON_IREPS from the
    # bank page = approved: resolved, counted as an other receipt at once
    assert _ledger(client, key)["C6"]["status"] == "OPEN"
    with SessionLocal() as s:
        before = overview.overview(s, pk)["unrecognised_credits"]
    r = _put_source(client, key, ids["C6"], "NON_IREPS")
    assert r.status_code == 200, r.text
    assert r.json()["exception_status"] == "RESOLVED"
    exc = _ledger(client, key)["C6"]
    assert exc["resolved_by"] == "USER_NON_IREPS" and exc["source_decision"] == "NON_IREPS"
    with SessionLocal() as s:
        ov = overview.overview(s, pk)
    assert ov["unrecognised_credits"] == before + 1
    assert _bank_rows(client, key)["C6"]["source"] == "NON_IREPS"
    # ...and a later run neither pools it nor re-opens it
    _reconcile(client, key, bz["a"])
    assert _ledger(client, key)["C6"]["status"] == "RESOLVED"

    # auto: the decision is dropped and the approval re-opens
    r = _put_source(client, key, ids["C6"], None)
    assert r.status_code == 200 and r.json()["exception_status"] == "OPEN"
    rows = _bank_rows(client, key)
    assert rows["C6"]["source"] == "IREPS" and rows["C6"]["source_decided"] is False


def test_source_set_on_a_credit_no_run_has_seen(world):  # noqa: F811
    """Setting NON_IREPS before any run records the approval right away, so
    "Other receipts" and the Non-IREPS tab agree with the Source column."""
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bank_txns": credits([("C7", 1234.0, "NR")])}, {"bank_txns": bz["a"]})
    txn = _bank_rows(client, key)["C7"]["gold_bank_txn_id"]
    r = _put_source(client, key, txn, "NON_IREPS")
    assert r.status_code == 200, r.text
    exc = _ledger(client, key)["C7"]
    assert exc["status"] == "RESOLVED" and exc["resolved_by"] == "USER_NON_IREPS"
    with SessionLocal() as s:
        assert overview.overview(s, pk)["unrecognised_credits"] == 1
    assert _bank_rows(client, key)["C7"]["source"] == "NON_IREPS"


def test_incremental_expects_bills_only_from_bank_coverage(world):  # noqa: F811
    """Matching stays wide open, but a bill due before the first credit the
    ledger has seen is not reported as unpaid: there is no bank data to
    judge it by. The floor is the earliest credit, so it moves back when
    older statements are reconciled."""
    import pandas as pd
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    old = bills(["OLD", "NEW", "PAID"])
    old.loc[0, "payment_advice_date"] = pd.Timestamp("2026-02-01")   # before coverage
    old.loc[2, "payment_advice_date"] = pd.Timestamp("2026-02-02")   # before, but paid
    _ingest(pk, {"bills": old}, {"bills": bz["bills"]})
    # credits valued 2026-03-18; PAID's amount (5002) arrives -> matched anyway
    _ingest(pk, {"bank_txns": credits([("C1", 5002.0, "NR")])}, {"bank_txns": bz["a"]})
    payload = _reconcile(client, key, bz["a"])
    assert payload["meta"]["expected_from"] == "2026-03-18"
    assert _matched_refs(pk) == {"C1"}                  # an old bill still matches
    with SessionLocal() as s:
        from db.models import GoldBill
        open_bills = set(s.execute(
            select(GoldBill.bill_number)
            .join(ExceptionLedger, ExceptionLedger.gold_bill_id == GoldBill.id)
            .where(ExceptionLedger.customer_id == pk,
                   ExceptionLedger.status == "OPEN")).scalars())
    assert open_bills == {"NEW"}                        # OLD is before coverage
