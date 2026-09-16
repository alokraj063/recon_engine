"""
db/bill_merge.py: repair the duplicate bills the old ingest rule stored —
a blank bill_number re-reported by several exports, one copy per export,
each with its own BILL_ONLY exception.

The fixture builds that damage directly (three copies per CO6, as in the
real data) and checks the merge keeps the matched copy, deletes the
phantom exceptions and recovery lines, moves every link to the survivor,
and is a no-op the second time.
"""

import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import SessionLocal, init_db  # noqa: E402
from db.bill_merge import merge_duplicate_bills  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, ExceptionLedger,  # noqa: E402
                       GoldBankTxn, GoldBill, GoldFileRow, GoldRecovery,
                       MatchLedger, MatchLedgerBill)
from db.storage import storage  # noqa: E402


@pytest.fixture()
def world(tmp_path):
    init_db()
    key = f"mergetest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        c = Customer(key=key, name="bill merge test")
        s.add(c)
        s.flush()
        files = []
        for i in range(3):
            p = tmp_path / f"bills{i}.txt"
            p.write_text(f"bills-{i}-{uuid.uuid4().hex}")
            files.append(register_file(s, c, "bill_status", p, p.name).id)

        def bill(fid, seq, co6, status, number="-"):
            b = GoldBill(customer_id=c.id, bronze_file_id=fid, row_seq=seq,
                         bill_number=number, submission_ref=co6, zone="SCoR",
                         net_payable_amount=1000.0, bill_status=status)
            s.add(b)
            s.flush()
            s.add(GoldFileRow(customer_id=c.id, bronze_file_id=fid, frame="bills",
                              gold_row_id=b.id, row_seq=seq))
            r = GoldRecovery(customer_id=c.id, bronze_file_id=fid, row_seq=seq,
                             gold_bill_id=b.id, recovery_head="IT", recovery_amt=5.0)
            s.add(r)
            s.flush()
            s.add(GoldFileRow(customer_id=c.id, bronze_file_id=fid, frame="recoveries",
                              gold_row_id=r.id, row_seq=seq))
            return b

        def exc(b, status="OPEN"):
            s.add(ExceptionLedger(customer_id=c.id, exception_type="BILL_ONLY",
                                  gold_bill_id=b.id, status=status))

        statuses = ["CO7 DONE", "PAYMENT MADE", "PAYMENT MADE"]
        # A: the MIDDLE copy is settled by a LOCKED match; the other two are open
        a = [bill(files[i], 0, "CO6-A", statuses[i]) for i in range(3)]
        exc(a[0]); exc(a[2])
        txn = GoldBankTxn(customer_id=c.id, bronze_file_id=files[0], row_seq=99,
                          bank_ref="CR-A", amount=1000.0, value_date=date(2026, 8, 19),
                          used_in_recon=True)
        s.add(txn)
        s.flush()
        m = MatchLedger(customer_id=c.id, match_id="m0", gold_bank_txn_id=txn.id,
                        confidence="HIGH", status="LOCKED", locked_by="AUTO_HIGH")
        s.add(m)
        s.flush()
        s.add(MatchLedgerBill(match_ledger_id=m.id, gold_bill_id=a[1].id, role="picked"))
        # a candidate link on a dropped copy must fold into the survivor
        s.add(MatchLedgerBill(match_ledger_id=m.id, gold_bill_id=a[0].id, role="candidate"))
        # B: unmatched, every copy raised an exception
        b = [bill(files[i], 1, "CO6-B", statuses[i]) for i in range(3)]
        for x in b:
            exc(x)
        # a real bill and a '-' bill with its own CO6: untouched
        real = bill(files[0], 2, "CO6-R", "PAYMENT MADE", number="INV-1")
        lone = bill(files[0], 3, "CO6-L", "CO7 DONE")
        exc(lone)
        s.commit()
        ids = {"pk": c.id, "a": [x.id for x in a], "b": [x.id for x in b],
               "real": real.id, "lone": lone.id, "match": m.id, "key": key}
    yield ids
    with SessionLocal() as s:
        pk = ids["pk"]
        s.execute(delete(MatchLedgerBill).where(MatchLedgerBill.match_ledger_id == ids["match"]))
        for model in (ExceptionLedger, MatchLedger, AuditLog, GoldFileRow, GoldRecovery,
                      GoldBill, GoldBankTxn, BronzeFile):
            s.execute(delete(model).where(model.customer_id == pk))
        s.execute(delete(Customer).where(Customer.id == pk))
        s.commit()
    import shutil
    shutil.rmtree(storage.root / "bronze" / ids["key"], ignore_errors=True)


def _count(s, model, *where):
    return s.execute(select(func.count()).select_from(model).where(*where)).scalar()


def test_merge_keeps_matched_copy_and_drops_phantoms(world):
    pk = world["pk"]
    with SessionLocal() as s:
        report = merge_duplicate_bills(s, pk)
        s.commit()
    assert report["groups_found"] == 2 and report["groups_merged"] == 2
    assert report["bills_deleted"] == 4 and report["recoveries_deleted"] == 4

    with SessionLocal() as s:
        # A: the matched middle copy survives, unmutated (it is LOCKED)
        a_keep = world["a"][1]
        assert [b.id for b in s.execute(select(GoldBill).where(
            GoldBill.submission_ref == "CO6-A", GoldBill.customer_id == pk)).scalars()] == [a_keep]
        assert _count(s, ExceptionLedger, ExceptionLedger.gold_bill_id.in_(world["a"])) == 0
        links = s.execute(select(MatchLedgerBill.gold_bill_id, MatchLedgerBill.role)
                          .where(MatchLedgerBill.match_ledger_id == world["match"])).all()
        assert links == [(a_keep, "picked")]

        # B: the earliest copy survives with the LATEST status and ONE open exception
        b_keep = s.get(GoldBill, world["b"][0])
        assert b_keep.bill_status == "PAYMENT MADE"
        assert s.get(GoldBill, world["b"][1]) is None
        excs = s.execute(select(ExceptionLedger).where(
            ExceptionLedger.gold_bill_id.in_(world["b"]))).scalars().all()
        assert [(e.gold_bill_id, e.status) for e in excs] == [(b_keep.id, "OPEN")]

        # every file still reported each bill; recovery lines: first copy only
        for keep in (a_keep, b_keep.id):
            assert _count(s, GoldFileRow, GoldFileRow.frame == "bills",
                          GoldFileRow.gold_row_id == keep) == 3
        assert _count(s, GoldRecovery, GoldRecovery.customer_id == pk) == 4   # A, B, real, lone
        assert _count(s, GoldFileRow, GoldFileRow.customer_id == pk,
                      GoldFileRow.frame == "recoveries") == 4

        # untouched: a real bill and a '-' bill with no twin
        assert s.get(GoldBill, world["real"]) is not None
        assert _count(s, ExceptionLedger, ExceptionLedger.gold_bill_id == world["lone"]) == 1
        assert _count(s, AuditLog, AuditLog.customer_id == pk,
                      AuditLog.event_type == "gold.bills_merged") == 1


def test_merge_is_idempotent_and_dry_run_writes_nothing(world):
    pk = world["pk"]
    with SessionLocal() as s:
        merge_duplicate_bills(s, pk)
        s.rollback()                     # a dry run
    with SessionLocal() as s:
        assert _count(s, GoldBill, GoldBill.customer_id == pk) == 8
        merge_duplicate_bills(s, pk)
        s.commit()
    with SessionLocal() as s:
        again = merge_duplicate_bills(s, pk)
        s.commit()
    assert again["groups_found"] == 0 and again.get("bills_deleted", 0) == 0
