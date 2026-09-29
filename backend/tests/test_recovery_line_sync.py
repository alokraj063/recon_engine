"""
Recovery lines follow their bill across exports (db/ingest.
sync_recovery_lines). Before 2026-09-29 lines were written for NEW bills
only, so a bill first exported before IREPS applied its deductions kept
its updated totals but never its per-head lines — 94 bills on the dev
load, ₹61 lakh of deductions missing from the WebADI export.

Also covers db/recovery_backfill, which replays stored silver rows to
repair a database loaded under the old rule.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import SessionLocal, init_db  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, GoldBankTxn,  # noqa: E402
                       GoldBill, GoldFileRow, GoldRecovery, MatchLedger,
                       MatchLedgerBill, SilverRecord)
from db.recovery_backfill import backfill_recovery_lines  # noqa: E402
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402


@pytest.fixture()
def customer(tmp_path):
    init_db()
    key = f"recsync-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        c = Customer(key=key, name="recovery sync test")
        s.add(c)
        s.commit()
        pk = c.id
    files = iter(range(100))

    def bronze(s):
        p = tmp_path / f"bills{next(files)}.txt"
        p.write_text(uuid.uuid4().hex)
        return register_file(s, s.get(Customer, pk), "bill_status", p, p.name).id

    yield {"pk": pk, "bronze": bronze}
    with SessionLocal() as s:
        ids = [m.id for m in s.query(MatchLedger).filter_by(customer_id=pk)]
        s.execute(delete(MatchLedgerBill).where(MatchLedgerBill.match_ledger_id.in_(ids)))
        for model in (MatchLedger, AuditLog, GoldFileRow, GoldRecovery, GoldBill,
                      GoldBankTxn, SilverRecord, BronzeFile):
            s.execute(delete(model).where(model.customer_id == pk))
        s.execute(delete(Customer).where(Customer.id == pk))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def _frames(lines, net=1000.0):
    """One bill (INV-1 / CO6-1) carrying `lines` [(head, amt)]."""
    ded = sum(a for _h, a in lines)
    bills = ensure_schema(pd.DataFrame([{
        "bill_number": "INV-1", "submission_ref": "CO6-1", "net_payable_amount": net,
        "deduction_amount": ded, "recovery_count": len(lines), "recovery_sum": ded,
        "bill_status": "PAYMENT MADE"}]), "bills")
    bills["row_seq"] = [0]
    rec = ensure_schema(pd.DataFrame([
        {"bill_index": 0, "bill_number": "INV-1", "submission_ref": "CO6-1",
         "recovery_head": h, "recovery_amt": a} for h, a in lines]), "recoveries")
    rec["bill_row_seq"] = [0] * len(lines)
    rec["row_seq"] = range(len(lines))
    return {"bills": bills, "recoveries": rec}


def _ingest(customer, lines, net=1000.0):
    with SessionLocal() as s:
        fid = customer["bronze"](s)
        _ids, stats = ingest_gold_frames(s, customer["pk"], _frames(lines, net),
                                         {"bills": fid, "recoveries": fid})
        s.commit()
    return stats


def _lines(pk):
    with SessionLocal() as s:
        return sorted((r.recovery_head, r.recovery_amt) for r in s.execute(
            select(GoldRecovery).where(GoldRecovery.customer_id == pk)).scalars())


def _lock(pk):
    with SessionLocal() as s:
        bill = s.execute(select(GoldBill).where(GoldBill.customer_id == pk)).scalar_one()
        t = GoldBankTxn(customer_id=pk, bronze_file_id=bill.bronze_file_id, row_seq=900,
                        bank_ref="UTR", amount=1.0, used_in_recon=True)
        s.add(t)
        s.flush()
        m = MatchLedger(customer_id=pk, match_id="m0", gold_bank_txn_id=t.id,
                        confidence="HIGH", status="LOCKED")
        s.add(m)
        s.flush()
        s.add(MatchLedgerBill(match_ledger_id=m.id, gold_bill_id=bill.id, role="picked"))
        s.commit()


def test_lines_arrive_with_a_later_export(customer):
    pk = customer["pk"]
    _ingest(customer, [])                                   # before deductions
    assert _lines(pk) == []
    stats = _ingest(customer, [("GST TDS DEDUCTION", 20.0), ("INCOME TAX", 10.0)], net=970.0)
    assert _lines(pk) == [("GST TDS DEDUCTION", 20.0), ("INCOME TAX", 10.0)]
    assert stats["by_frame"]["recoveries"]["updated"] == 1
    # the same lines again: nothing moves
    stats = _ingest(customer, [("INCOME TAX", 10.0), ("GST TDS DEDUCTION", 20.0)], net=970.0)
    assert stats["by_frame"]["recoveries"] == {**stats["by_frame"]["recoveries"],
                                               "inserted": 0, "updated": 0}
    # changed lines replace the stored ones (latest export wins)
    _ingest(customer, [("GST TDS DEDUCTION", 25.0)], net=975.0)
    assert _lines(pk) == [("GST TDS DEDUCTION", 25.0)]
    with SessionLocal() as s:     # no sighting left pointing at a deleted line
        live = {r.id for r in s.execute(select(GoldRecovery)
                                        .where(GoldRecovery.customer_id == pk)).scalars()}
        seen = set(s.execute(select(GoldFileRow.gold_row_id).where(
            GoldFileRow.customer_id == pk, GoldFileRow.frame == "recoveries")).scalars())
        assert seen <= live


def test_locked_bill_only_fills_missing_lines(customer):
    pk = customer["pk"]
    _ingest(customer, [])
    # the bill's totals update before it locks, its lines were lost (old rule)
    with SessionLocal() as s:
        b = s.execute(select(GoldBill).where(GoldBill.customer_id == pk)).scalar_one()
        b.deduction_amount = b.recovery_sum = 30.0
        s.commit()
    _lock(pk)
    # lines that do not total the locked bill's recovery_sum: refused
    _ingest(customer, [("GST TDS DEDUCTION", 99.0)])
    assert _lines(pk) == []
    # lines that do: filled in
    _ingest(customer, [("GST TDS DEDUCTION", 20.0), ("INCOME TAX", 10.0)])
    assert _lines(pk) == [("GST TDS DEDUCTION", 20.0), ("INCOME TAX", 10.0)]
    # a locked bill that HAS lines never changes them
    _ingest(customer, [("GST TDS DEDUCTION", 30.0)])
    assert _lines(pk) == [("GST TDS DEDUCTION", 20.0), ("INCOME TAX", 10.0)]


def test_backfill_replays_silver(customer):
    """A database loaded under the old rule: two files, the second carries
    lines gold never stored. The backfill restores them from silver, and a
    second pass does nothing."""
    pk = customer["pk"]
    _ingest(customer, [])
    _ingest(customer, [("INCOME TAX - CONTR(Section-194Q)", 154.0)])
    with SessionLocal() as s:   # simulate the old rule: lines never written
        s.execute(delete(GoldFileRow).where(GoldFileRow.customer_id == pk,
                                            GoldFileRow.frame == "recoveries"))
        s.execute(delete(GoldRecovery).where(GoldRecovery.customer_id == pk))
        files = [f.id for f in s.execute(select(BronzeFile).where(
            BronzeFile.customer_id == pk).order_by(BronzeFile.id)).scalars()]
        # what the IREPS parser read for each file (silver, source-native)
        for fid, text in zip(files, [None, "INCOME TAX - CONTR(Section-194Q): 154"]):
            s.add(SilverRecord(customer_id=pk, bronze_file_id=fid, frame_name="bills",
                               row_seq=0, payload={
                                   "BillNumber": "INV-1", "CO6No": "CO6-1",
                                   "Status": "PAYMENT MADE", "NetAmt": 1000.0,
                                   "PassedAmt": 1154.0, "DeductedAmt": 154.0,
                                   "RecoveryDetails": text, "PartyCode": "X833"}))
        for f in s.execute(select(BronzeFile).where(BronzeFile.customer_id == pk)).scalars():
            f.adapter_key = "ireps"
        s.commit()
    assert _lines(pk) == []
    with SessionLocal() as s:
        report = backfill_recovery_lines(s, pk)
        s.commit()
    assert report["bills_refreshed"] == 1 and report["lines_inserted"] == 1
    assert _lines(pk) == [("INCOME TAX - CONTR(Section-194Q)", 154.0)]
    with SessionLocal() as s:
        again = backfill_recovery_lines(s, pk)
        s.commit()
    assert again["bills_refreshed"] == 0 and again["lines_inserted"] == 0
