"""
A nil-payable bill (deductions consumed the whole bill; IREPS still says
PAYMENT MADE) can never be paid by a credit, so an incremental run must
never hold it OPEN as a BILL_ONLY exception — and a row opened for one
before that rule existed is closed by the next run (RUN, no match).
The run's bill_only FRAME still lists it, labelled ZERO_NET_NOTHING_DUE
("Nil payable"): engine output is unchanged for every caller.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, incremental, init_db, overview  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, ExceptionLedger,  # noqa: E402
                       GoldBankTxn, GoldBill, GoldFileRow, GoldRecovery,
                       IngestConflict, MatchLedger, MatchLedgerBill, Run,
                       RunFrame, RunMatchBill, SilverRecord, SourceConfig)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from recon.rules import ExactSignal, FieldMapping, MatchRuleSet  # noqa: E402

RULES = MatchRuleSet(
    field_map=FieldMapping(
        exact_signals=(ExactSignal("customer_ref", "vendor_code", 3),),
        bill_date_fallback=None, fallback_due_statuses=()),
)

DAY = pd.Timestamp("2026-08-20")


def _run(cust, stmt_id):
    run_id = incremental.start_run(cust, {})
    with SessionLocal() as s:
        out, bank_pos, bill_pos = incremental.run_matching(s, cust, stmt_id, RULES)
        stats, _, _ = incremental.finalize_ledger(s, cust, run_id, out,
                                                  bank_pos, bill_pos)
        run = s.get(Run, run_id)
        run.status = "succeeded"
        run.payload = {"meta": {"counts": {"bank_credits": 1}}}
        s.commit()
    return run_id, out, stats


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    init_db()
    tmp = tmp_path_factory.mktemp("zeronet")
    key = f"ztest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        customer = Customer(key=key, name="zero-net test")
        s.add(customer)
        s.flush()

        def bronze(name, source_type):
            p = tmp / name
            p.write_text(name + uuid.uuid4().hex)
            return register_file(s, customer, source_type, p, name).id

        bz = {"stmt": bronze("stmt.txt", "bank_statement"),
              "bills": bronze("bills.txt", "bill_status")}
        s.commit()
        cust = customer.id

    bank = pd.DataFrame([("T1", "V1", 1000.0, DAY)],
                        columns=["bank_ref", "customer_ref", "amount", "value_date"])
    bank["narrative"] = "credit"
    bank["txn_type"] = "CREDIT"
    bank["used_in_recon"] = True
    bank = ensure_schema(bank, "bank_txns")
    bank["row_seq"] = range(len(bank))

    bills = pd.DataFrame([
        ("INV-1", "V1", 1000.0, DAY),                  # matches T1
        ("INV-Z", "V2", 0.0, DAY),                     # nil payable, in window
        ("INV-3", "V3", 300.0, DAY),                   # unpaid -> BILL_ONLY
        ("INV-OLD", "V4", 0.0, pd.Timestamp("2026-05-02")),  # nil, pre-fix row
    ], columns=["bill_number", "vendor_code", "net_payable_amount",
                "payment_advice_date"])
    bills["bill_status"] = "PAYMENT MADE"
    bills["submission_date"] = bills["payment_advice_date"]
    bills["submission_ref"] = "S-" + bills["bill_number"]
    bills["sheet"] = "synth"
    bills["data_row"] = range(len(bills))
    bills = ensure_schema(bills, "bills")
    bills["row_seq"] = range(len(bills))

    with SessionLocal() as s:
        ingest_gold_frames(
            s, cust,
            {"bank_txns": bank, "bills": bills,
             "recoveries": ensure_schema(pd.DataFrame(), "recoveries").assign(row_seq=[])},
            {"bank_txns": bz["stmt"], "bills": bz["bills"], "recoveries": bz["bills"]})
        ids = dict(s.execute(select(GoldBill.bill_number, GoldBill.id)
                             .where(GoldBill.customer_id == cust)).all())
        # an OPEN BILL_ONLY row a pre-fix run left behind for a nil bill
        pre = ExceptionLedger(customer_id=cust, exception_type="BILL_ONLY",
                              gold_bill_id=ids["INV-OLD"])
        s.add(pre)
        s.commit()
        pre_id = pre.id

    run1, out1, stats1 = _run(cust, bz["stmt"])
    run2, out2, stats2 = _run(cust, bz["stmt"])

    yield {"cust": cust, "ids": ids, "pre_id": pre_id, "run1": run1,
           "out1": out1, "stats1": stats1, "stats2": stats2}

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


def _bill_only_rows(cust, bill_id):
    with SessionLocal() as s:
        return list(s.execute(select(ExceptionLedger).where(
            ExceptionLedger.customer_id == cust,
            ExceptionLedger.exception_type == "BILL_ONLY",
            ExceptionLedger.gold_bill_id == bill_id)).scalars())


def test_nil_bill_in_window_gets_no_bill_only_row(world):
    assert _bill_only_rows(world["cust"], world["ids"]["INV-Z"]) == []
    # still in the run's frame, labelled — the frame is engine output
    bo = world["out1"]["bill_only"]
    z = bo[bo["bill_number"] == "INV-Z"]
    assert list(z["ExpectedBasis"]) == ["ZERO_NET_NOTHING_DUE"]


def test_unpaid_bill_still_opens_bill_only(world):
    rows = _bill_only_rows(world["cust"], world["ids"]["INV-3"])
    assert [r.status for r in rows] == ["OPEN"]
    assert world["stats1"]["exceptions_opened"] == 1   # INV-3 only


def test_pre_existing_nil_row_resolved_by_next_run(world):
    with SessionLocal() as s:
        row = s.get(ExceptionLedger, world["pre_id"])
        assert row.status == "RESOLVED" and row.resolved_by == "RUN"
        assert row.resolved_by_run_id == world["run1"]
        assert row.resolved_by_match_id is None and row.resolved_at is not None
    assert world["stats1"]["zero_net_closed"] == 1
    # the second run neither reopens nor re-closes anything
    assert world["stats2"]["zero_net_closed"] == 0
    assert world["stats2"]["exceptions_opened"] == 0
    assert [r.status for r in _bill_only_rows(world["cust"],
                                              world["ids"]["INV-OLD"])] == ["RESOLVED"]


def test_overview_and_ar_ignore_nil_bills(world):
    cust, ids = world["cust"], world["ids"]
    nil = {ids["INV-Z"], ids["INV-OLD"]}
    with SessionLocal() as s:
        ov = overview.overview(s, cust)
        ar = overview.ar_view(s, cust)
    assert ov["open_in_scope"]["bill_only"] == 1
    assert ov["open_exceptions"]["BILL_ONLY"] == 1
    assert all(t.get("amount") != 0 for t in ov["top_exceptions"]
               if t["exception_type"] == "BILL_ONLY")
    outstanding = [r for r in ar["rows"] if r["status"] in ("AWAITING", "OVERDUE")]
    assert [r["bill_number"] for r in outstanding] == ["INV-3"]
    assert not any(r["bill_number"] in ("INV-Z", "INV-OLD") for r in ar["rows"])
    assert nil  # both ids resolved from gold
