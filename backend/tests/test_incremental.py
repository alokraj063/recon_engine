"""
Incremental-run scenario test (plan Phase 6 verification):

  (a) splitting the statement's credits into two incremental runs yields
      the same union of matches as one snapshot reconcile
  (b) re-ingesting the same bills export never duplicates gold rows
  (c) a bill consumed in run 1 never reappears in run 2's pool
  (d) rejecting an OPEN match releases its bills back into the pool and
      re-opens the credit; accepting locks it
  (g) reopening a REJECTED match re-claims both sides and closes the
      BANK_ONLY exception the rejection opened — unless a later match
      has claimed the credit in the meantime
  (e) a second incremental run while one is 'running' is refused
  (f) a changed amount on a LOCKED bill produces an ingest_conflicts row
      and leaves the gold row untouched

Runs under a throwaway customer so the 'default' customer's data is
never touched; everything it creates is deleted afterwards.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from make_golden import sample_config  # noqa: E402

from db import SessionLocal, incremental, init_db  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames, new_stats  # noqa: E402
from db.storage import storage  # noqa: E402
from db.models import (AuditLog, Customer, ExceptionLedger, GoldBankTxn,  # noqa: E402
                       GoldBill, GoldRecovery, IngestConflict, MatchLedger,
                       MatchLedgerBill, Run, RunMatchBill, SilverRecord,
                       BronzeFile)
from recon.engine import reconcile  # noqa: E402
from recon.sources import get_adapter  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Fresh throwaway customer + parsed sample frames + fake bronze files."""
    init_db()
    tmp = tmp_path_factory.mktemp("incr")
    cfg = sample_config()

    bank_adapter = get_adapter("bank_statement", "hsbc")
    bills_adapter = get_adapter("bill_status", "ireps")
    bank_gold = bank_adapter.to_gold(bank_adapter.parse(cfg.statement_pdf, {}), {})["bank_txns"]
    bills_gold = bills_adapter.to_gold(bills_adapter.parse(cfg.bill_status, {}), {})

    credits = bank_gold[bank_gold["used_in_recon"]].reset_index(drop=True)
    half = len(credits) // 2

    def as_stmt(df):
        df = df.reset_index(drop=True).copy()
        df["row_seq"] = range(len(df))
        return df

    key = f"ptest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        customer = Customer(key=key, name="incremental scenario test")
        s.add(customer)
        s.flush()

        def bronze(name, source_type):
            p = tmp / name
            p.write_text(name + uuid.uuid4().hex)   # distinct bytes
            return register_file(s, customer, source_type, p, name).id

        ids = {
            "stmt1": bronze("stmt_half1.txt", "bank_statement"),
            "stmt2": bronze("stmt_half2.txt", "bank_statement"),
            "bills": bronze("bills.txt", "bill_status"),
            "bills_v2": bronze("bills_v2.txt", "bill_status"),
        }
        s.commit()
        customer_pk = customer.id

    world = {
        "customer_pk": customer_pk,
        "bronze": ids,
        "bank_half1": as_stmt(credits.iloc[:half]),
        "bank_half2": as_stmt(credits.iloc[half:]),
        "bank_full": as_stmt(credits),
        "bills_gold": bills_gold,
    }
    yield world

    # cleanup: everything belonging to the throwaway customer
    with SessionLocal() as s:
        run_ids = list(s.execute(
            select(Run.id).where(Run.customer_id == customer_pk)).scalars())
        if run_ids:
            s.execute(delete(RunMatchBill).where(RunMatchBill.run_id.in_(run_ids)))
        ledger_ids = list(s.execute(
            select(MatchLedger.id).where(MatchLedger.customer_id == customer_pk)).scalars())
        if ledger_ids:
            s.execute(delete(MatchLedgerBill)
                      .where(MatchLedgerBill.match_ledger_id.in_(ledger_ids)))
        for model in (AuditLog, IngestConflict, ExceptionLedger, MatchLedger,
                      GoldRecovery, GoldBill, GoldBankTxn, SilverRecord,
                      Run, BronzeFile):
            s.execute(delete(model).where(model.customer_id == customer_pk))
        s.execute(delete(Customer).where(Customer.id == customer_pk))
        s.commit()

    # register_file() copies uploaded bytes to data/bronze/{key}/ on disk;
    # the DB deletes above don't touch that directory
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def _ingest(customer_pk, frames, bronze_ids):
    with SessionLocal() as s:
        ids, stats = ingest_gold_frames(s, customer_pk, frames, bronze_ids)
        s.commit()
    return ids, stats


def _finish(run_id, status="succeeded"):
    with SessionLocal() as s:
        s.get(Run, run_id).status = status
        s.commit()


def _incremental_run(customer_pk, statement_bronze_id):
    from recon.rules import MatchRuleSet
    run_id = incremental.start_run(customer_pk, {})
    with SessionLocal() as s:
        out, bank_ids, bill_ids = incremental.run_matching(
            s, customer_pk, statement_bronze_id, MatchRuleSet())
        stats, links, _ledger_ids = incremental.finalize_ledger(
            s, customer_pk, run_id, out, bank_ids, bill_ids)
        s.commit()
    return run_id, out, stats


def _ledger_pairs(customer_pk):
    """{(bank_ref, frozenset(gold bill ids))} for non-REJECTED matches."""
    with SessionLocal() as s:
        pairs = set()
        for m in s.execute(select(MatchLedger)
                           .where(MatchLedger.customer_id == customer_pk,
                                  MatchLedger.status != "REJECTED")).scalars():
            ref = s.get(GoldBankTxn, m.gold_bank_txn_id).bank_ref
            bills = frozenset(s.execute(
                select(MatchLedgerBill.gold_bill_id)
                .where(MatchLedgerBill.match_ledger_id == m.id,
                       MatchLedgerBill.role == "picked")).scalars())
            pairs.add((ref, bills))
        return pairs


def test_incremental_scenario(world):
    cust = world["customer_pk"]
    bz = world["bronze"]

    # ingest the bills export + statement half 1
    bill_ids_map, stats1 = _ingest(
        cust,
        {"bills": world["bills_gold"]["bills"],
         "recoveries": world["bills_gold"]["recoveries"],
         "bank_txns": world["bank_half1"]},
        {"bills": bz["bills"], "recoveries": bz["bills"],
         "bank_txns": bz["stmt1"]})
    n_bills = len(world["bills_gold"]["bills"])
    assert len(bill_ids_map["bills"]) == n_bills

    # run 1 over half 1
    run1, out1, ledger1 = _incremental_run(cust, bz["stmt1"])
    assert ledger1["matches_created"] > 0

    # (e) the running-run guard refuses a concurrent incremental run
    with pytest.raises(incremental.RunInProgress):
        incremental.start_run(cust, {})
    _finish(run1)

    # (c) bills consumed in run 1 never reappear in run 2's pool
    with SessionLocal() as s:
        consumed = {b for _, b in (
            (m, bid) for m in s.execute(
                select(MatchLedger.id)
                .where(MatchLedger.customer_id == cust,
                       MatchLedger.status.in_(("OPEN", "LOCKED")))).scalars()
            for bid in s.execute(
                select(MatchLedgerBill.gold_bill_id)
                .where(MatchLedgerBill.match_ledger_id == m,
                       MatchLedgerBill.role == "picked")).scalars())}
        pool2 = incremental.build_pool(s, cust, bz["stmt2"])
    assert consumed, "run 1 should have consumed bills"
    assert consumed.isdisjoint(set(pool2["bill_ids"]))

    # (b) re-ingesting the same bills export duplicates nothing
    with SessionLocal() as s:
        before = s.execute(select(GoldBill.id)
                           .where(GoldBill.customer_id == cust)).scalars()
        before = len(list(before))
    _, stats_re = _ingest(
        cust,
        {"bills": world["bills_gold"]["bills"],
         "recoveries": world["bills_gold"]["recoveries"]},
        {"bills": bz["bills"], "recoveries": bz["bills"]})
    assert stats_re["files_reused"] >= 1 and stats_re["rows_inserted"] == 0
    with SessionLocal() as s:
        after = len(list(s.execute(
            select(GoldBill.id).where(GoldBill.customer_id == cust)).scalars()))
    assert after == before

    # ingest + run half 2
    _ingest(cust, {"bank_txns": world["bank_half2"]},
            {"bank_txns": bz["stmt2"]})
    run2, out2, ledger2 = _incremental_run(cust, bz["stmt2"])
    _finish(run2)

    # (a) union of ledger matches == one snapshot reconcile over everything
    snapshot = reconcile(
        world["bank_full"].drop(columns=["used_in_recon", "row_seq"]),
        world["bills_gold"]["bills"].drop(columns=["row_seq"]),
        None, None)
    snap_pairs = set()
    for r in snapshot["matched"].itertuples():
        bills = frozenset(bill_ids_map["bills"][int(i)] for i in r.bill_indices)
        snap_pairs.add((r.bank_ref, bills))
    assert _ledger_pairs(cust) == snap_pairs

    # (d) reject releases; accept locks
    with SessionLocal() as s:
        open_matches = list(s.execute(
            select(MatchLedger).where(MatchLedger.customer_id == cust,
                                      MatchLedger.status == "OPEN")).scalars())
    assert open_matches, "sample data should produce review-confidence matches"
    rejected = open_matches[0]
    state = incremental.reject_match(rejected.id)
    assert state["status"] == "REJECTED"
    with SessionLocal() as s:
        released = set(s.execute(
            select(MatchLedgerBill.gold_bill_id)
            .where(MatchLedgerBill.match_ledger_id == rejected.id,
                   MatchLedgerBill.role == "picked")).scalars())
        pool3 = incremental.build_pool(s, cust, bz["stmt2"])
        assert released <= set(pool3["bill_ids"])   # bills back in the pool
        reopened = s.execute(
            select(ExceptionLedger)
            .where(ExceptionLedger.customer_id == cust,
                   ExceptionLedger.gold_bank_txn_id == rejected.gold_bank_txn_id,
                   ExceptionLedger.status == "OPEN")).scalar_one_or_none()
        assert reopened is not None                 # credit re-opened

    # (g) reopen undoes the rejection: both sides re-claimed, links and
    #     roles intact, and the reject-opened exception closed again
    state = incremental.reopen_match(rejected.id)
    assert state == {"id": rejected.id, "status": "OPEN", "locked_by": None}
    with SessionLocal() as s:
        roles = list(s.execute(
            select(MatchLedgerBill.gold_bill_id, MatchLedgerBill.role)
            .where(MatchLedgerBill.match_ledger_id == rejected.id)))
        assert {b for b, r in roles if r == "picked"} == released
        assert s.execute(
            select(ExceptionLedger)
            .where(ExceptionLedger.customer_id == cust,
                   ExceptionLedger.gold_bank_txn_id == rejected.gold_bank_txn_id,
                   ExceptionLedger.status == "OPEN")).scalar_one_or_none() is None
        held_txns, held_bills = incremental._consumed(s, cust)
        assert rejected.gold_bank_txn_id in held_txns
        assert released <= held_bills

    # ...and it refuses to double-claim a credit some other match took
    # while this one sat rejected
    incremental.reject_match(rejected.id)
    with SessionLocal() as s:
        squatter = MatchLedger(customer_id=cust, run_id=run2, match_id="mSquat",
                               gold_bank_txn_id=rejected.gold_bank_txn_id,
                               confidence="LOW", status="OPEN")
        s.add(squatter)
        s.commit()
        squatter_id = squatter.id
    with pytest.raises(incremental.LedgerConflict):
        incremental.reopen_match(rejected.id)
    with SessionLocal() as s:
        s.execute(delete(MatchLedger).where(MatchLedger.id == squatter_id))
        s.commit()
    assert incremental.reopen_match(rejected.id)["status"] == "OPEN"

    if len(open_matches) > 1:
        state = incremental.accept_match(open_matches[1].id)
        assert state == {"id": open_matches[1].id, "status": "LOCKED",
                         "locked_by": "USER"}
        # unlock reopens the decision without releasing anything...
        state = incremental.unlock_match(open_matches[1].id)
        assert state == {"id": open_matches[1].id, "status": "OPEN",
                         "locked_by": None}
        with SessionLocal() as s:
            still_held = set(s.execute(
                select(MatchLedgerBill.gold_bill_id)
                .where(MatchLedgerBill.match_ledger_id == open_matches[1].id,
                       MatchLedgerBill.role == "picked")).scalars())
            pool_after = incremental.build_pool(s, cust, bz["stmt2"])
            assert not still_held & set(pool_after["bill_ids"])  # still consumed
        # ...and re-accepting, this time explicitly choosing the picked
        # bill, locks it again (the chooser path)
        state = incremental.accept_match(open_matches[1].id,
                                         gold_bill_id=next(iter(still_held)))
        assert state == {"id": open_matches[1].id, "status": "LOCKED",
                         "locked_by": "USER"}

    # (f) a changed amount on a LOCKED bill -> conflict row, gold untouched
    with SessionLocal() as s:
        locked_bill_id = s.execute(
            select(MatchLedgerBill.gold_bill_id)
            .join(MatchLedger, MatchLedger.id == MatchLedgerBill.match_ledger_id)
            .where(MatchLedger.customer_id == cust,
                   MatchLedger.status == "LOCKED",
                   MatchLedgerBill.role == "picked").limit(1)).scalar_one()
        locked = s.get(GoldBill, locked_bill_id)
        locked_key, locked_co6, old_net = (locked.bill_number,
                                           locked.submission_ref,
                                           locked.net_payable_amount)
    bills_v2 = world["bills_gold"]["bills"].copy()
    hit = (bills_v2["bill_number"].astype(str).str.strip() == str(locked_key)) & \
          (bills_v2["submission_ref"].astype(str).str.strip() == str(locked_co6))
    assert hit.any()
    bills_v2.loc[hit, "net_payable_amount"] = (old_net or 0) + 999.0
    _, stats_v2 = _ingest(cust, {"bills": bills_v2}, {"bills": bz["bills_v2"]})
    assert stats_v2["conflicts"] >= 1
    with SessionLocal() as s:
        assert s.get(GoldBill, locked_bill_id).net_payable_amount == old_net
        conflict = s.execute(
            select(IngestConflict)
            .where(IngestConflict.customer_id == cust,
                   IngestConflict.gold_bill_id == locked_bill_id)
        ).scalars().first()
        assert conflict is not None
        assert "net_payable_amount" in conflict.changed_fields
