"""
A review match nobody has touched is re-judged by the next run: when a bill
that arrived AFTER the credit now pairs it HIGH, the weak match is
superseded (the match-81 story: an NER credit paired to a same-amount bill
of another zone, the right NER bill listed days later).

Synthetic customer over plain gold frames, independent of the sample docs.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, incremental, init_db  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, ExceptionLedger,  # noqa: E402
                       GoldBankTxn, GoldBill, GoldFileRow, GoldRecovery,
                       IngestConflict, MatchLedger, MatchLedgerBill, Run,
                       RunFrame, RunMatchBill, SilverRecord, SourceConfig)
from db.storage import storage  # noqa: E402
from recon.engine import REVIEW_CONFIDENCE  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from tests.test_ledger_decisions import RULES, _bank, _bills  # noqa: E402


def _ingest(cust, bank, bills, bronze_bank, bronze_bills):
    frames = {"recoveries": ensure_schema(pd.DataFrame(), "recoveries").assign(row_seq=[])}
    files = {"recoveries": bronze_bills}
    if bank is not None:
        frames["bank_txns"], files["bank_txns"] = bank, bronze_bank
    frames["bills"], files["bills"] = bills, bronze_bills
    with SessionLocal() as s:
        ids, _ = ingest_gold_frames(s, cust, frames, files)
        s.commit()
    return ids


def _run(cust, stmt_bronze):
    run_id = incremental.start_run(cust, {})
    with SessionLocal() as s:
        rescored = incremental.rescore_provisional(s, cust, run_id, stmt_bronze, RULES)
        out, bank_pos, bill_pos = incremental.run_matching(s, cust, stmt_bronze, RULES)
        stats, _l, _i = incremental.finalize_ledger(s, cust, run_id, out, bank_pos, bill_pos)
        s.get(Run, run_id).status = "succeeded"
        s.commit()
    return run_id, rescored, stats


def _matches(cust):
    with SessionLocal() as s:
        return list(s.execute(select(MatchLedger).where(
            MatchLedger.customer_id == cust).order_by(MatchLedger.seq)).scalars())


def _exc(cust, **kw):
    with SessionLocal() as s:
        q = select(ExceptionLedger).where(ExceptionLedger.customer_id == cust)
        for k, v in kw.items():
            q = q.where(getattr(ExceptionLedger, k) == v)
        return list(s.execute(q).scalars())


@pytest.fixture()
def world(tmp_path):
    init_db()
    key = f"rescore-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        customer = Customer(key=key, name="rescore test")
        s.add(customer)
        s.flush()

        def bronze(name, source_type):
            p = tmp_path / name
            p.write_text(name + uuid.uuid4().hex)
            return register_file(s, customer, source_type, p, name).id

        bz = {n: bronze(n + ".txt", t) for n, t in
              (("stmt", "bank_statement"), ("stmt2", "bank_statement"),
               ("bills", "bill_status"), ("bills2", "bill_status"))}
        s.commit()
        cust = customer.id
    yield {"cust": cust, "bz": bz}
    with SessionLocal() as s:
        run_ids = list(s.execute(select(Run.id).where(Run.customer_id == cust)).scalars())
        if run_ids:
            s.execute(delete(RunMatchBill).where(RunMatchBill.run_id.in_(run_ids)))
            s.execute(delete(RunFrame).where(RunFrame.run_id.in_(run_ids)))
        ledger_ids = list(s.execute(
            select(MatchLedger.id).where(MatchLedger.customer_id == cust)).scalars())
        if ledger_ids:
            s.execute(delete(MatchLedgerBill)
                      .where(MatchLedgerBill.match_ledger_id.in_(ledger_ids)))
        for model in (AuditLog, IngestConflict, ExceptionLedger, MatchLedger,
                      GoldFileRow, GoldRecovery, GoldBill, GoldBankTxn,
                      SilverRecord, Run, SourceConfig, BronzeFile):
            s.execute(delete(model).where(model.customer_id == cust))
        s.execute(delete(Customer).where(Customer.id == cust))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def _first_day(world):
    """credit T1 (signal V1) meets OLD (signal V9, same amount) -> a weak
    weak review match (signal disagrees) that claims both."""
    cust, bz = world["cust"], world["bz"]
    # T2 has no bill: it keeps the run's pool non-empty, as a real one is
    _ingest(cust, _bank([("T1", "V1", 1000.0), ("T2", "V8", 500.0)]),
            _bills([("OLD", "V9", 1000.0)]), bz["stmt"], bz["bills"])
    _run(cust, bz["stmt"])


def test_late_bill_supersedes_a_weak_match(world):
    cust, bz = world["cust"], world["bz"]
    _first_day(world)
    (weak,) = _matches(cust)
    assert weak.status == "OPEN" and weak.confidence in REVIEW_CONFIDENCE

    # the right bill (same signal as the credit) is listed a run later
    _ingest(cust, None, _bills([("NEW", "V1", 1000.0)]), None, bz["bills2"])
    run2, rescored, _stats = _run(cust, bz["stmt"])

    old, new = _matches(cust)
    assert rescored == {"provisional_reviewed": 1, "provisional_superseded": 1,
                        "provisional_kept_conflict": 0}
    assert (old.status, old.locked_by) == ("REJECTED", incremental.SUPERSEDED_BY)
    assert old.decided_by_user_id is None and f"M-{new.seq}" in old.decision_note
    assert (new.confidence, new.status, new.locked_by) == ("HIGH", "LOCKED", "AUTO_HIGH")
    assert new.run_id == run2 and new.gold_bank_txn_id == old.gold_bank_txn_id
    with SessionLocal() as s:
        picked = {b.bill_number for b in s.execute(
            select(GoldBill).join(MatchLedgerBill, MatchLedgerBill.gold_bill_id == GoldBill.id)
            .where(MatchLedgerBill.match_ledger_id == new.id,
                   MatchLedgerBill.role == "picked")).scalars()}
        events = list(s.execute(select(AuditLog).where(
            AuditLog.customer_id == cust,
            AuditLog.event_type == "ledger.match_superseded")).scalars())
    assert picked == {"NEW"}
    assert len(events) == 1 and events[0].details["superseded_by_seq"] == new.seq

    # NEW went straight to the new match; OLD, the bill the weak match gave
    # up, is free again and this run's own matching reports it
    with SessionLocal() as s:
        open_bills = {s.get(GoldBill, e.gold_bill_id).bill_number
                      for e in _exc(cust, exception_type="BILL_ONLY", status="OPEN")}
    assert open_bills == {"OLD"}


def test_supersede_closes_the_bill_only_row_an_earlier_run_opened(world):
    """The match-81 order: the right bill was reported as an exception by a
    run in which the weak match was already standing."""
    cust, bz = world["cust"], world["bz"]
    _first_day(world)
    (weak,) = _matches(cust)
    with SessionLocal() as s:            # freeze the weak match for one run
        s.get(MatchLedger, weak.id).decided_by_user_id = 7
        s.commit()
    _ingest(cust, None, _bills([("NEW", "V1", 1000.0)]), None, bz["bills2"])
    _run(cust, bz["stmt"])
    (open_bill,) = _exc(cust, exception_type="BILL_ONLY", status="OPEN")

    with SessionLocal() as s:            # nobody actually decided it
        s.get(MatchLedger, weak.id).decided_by_user_id = None
        s.commit()
    run3, rescored, _ = _run(cust, bz["stmt"])
    assert rescored["provisional_superseded"] == 1
    (row,) = [e for e in _exc(cust, exception_type="BILL_ONLY")
              if e.id == open_bill.id]
    assert (row.status, row.resolved_by, row.resolved_by_run_id) == ("RESOLVED", "RUN", run3)


def test_rescore_is_idempotent_and_leaves_still_weak_matches_alone(world):
    cust, bz = world["cust"], world["bz"]
    _first_day(world)
    (weak,) = _matches(cust)
    # a second run with nothing new changes nothing and mints no new M-seq
    _run_id, rescored, _ = _run(cust, bz["stmt"])
    assert rescored["provisional_superseded"] == 0
    (same,) = _matches(cust)
    assert (same.id, same.status, same.seq) == (weak.id, "OPEN", weak.seq)
    # another wrong-signal bill is no upgrade either
    _ingest(cust, None, _bills([("ALSO", "V7", 1000.0)]), None, bz["bills2"])
    _run_id, rescored, _ = _run(cust, bz["stmt"])
    assert rescored["provisional_superseded"] == 0
    assert [(m.id, m.status) for m in _matches(cust)] == [(weak.id, "OPEN")]


def test_a_match_an_analyst_touched_is_never_replaced(world):
    cust, bz = world["cust"], world["bz"]
    _first_day(world)
    (weak,) = _matches(cust)
    with SessionLocal() as s:            # e.g. decided once, then reopened
        s.get(MatchLedger, weak.id).decided_by_user_id = 7
        s.commit()
    _ingest(cust, None, _bills([("NEW", "V1", 1000.0)]), None, bz["bills2"])
    _run_id, rescored, _ = _run(cust, bz["stmt"])
    assert rescored["provisional_reviewed"] == 0
    assert [(m.id, m.status) for m in _matches(cust)] == [(weak.id, "OPEN")]
    # the good bill stays an exception for the analyst to act on
    assert _exc(cust, exception_type="BILL_ONLY", status="OPEN")


def test_a_fresh_credit_keeps_the_late_bill_over_an_old_weak_one(world):
    """Two credits want the same late bill equally well. The re-score must
    not let the OLD weak one jump the queue: the run's own credit takes it,
    exactly as it would have before re-scoring existed."""
    cust, bz = world["cust"], world["bz"]
    _first_day(world)
    (weak,) = _matches(cust)
    _ingest(cust, _bank([("T3", "V1", 1000.0)]), _bills([("NEW", "V1", 1000.0)]),
            bz["stmt2"], bz["bills2"])
    _run_id, rescored, _ = _run(cust, bz["stmt2"])
    assert rescored["provisional_superseded"] == 0
    by_status = {(m.gold_bank_txn_id == weak.gold_bank_txn_id, m.status, m.confidence)
                 for m in _matches(cust)}
    assert by_status == {(True, "OPEN", weak.confidence), (False, "LOCKED", "HIGH")}
