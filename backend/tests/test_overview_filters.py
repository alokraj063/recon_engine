"""
Item 2.1: /api/overview date + operating-unit filters. A synthetic
customer with bills in two units and two months; the unfiltered payload
must not change, and each filter must move the counts it is documented
to move (db/overview.py:overview).
"""

import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
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
    paid_statuses=frozenset({"APPROVED"}),
    field_map=FieldMapping(
        exact_signals=(ExactSignal("customer_ref", "vendor_code", 3),),
        bill_date_fallback=None, fallback_due_statuses=()),
)

MARCH, APRIL = pd.Timestamp("2026-03-18"), pd.Timestamp("2026-04-09")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    init_db()
    tmp = tmp_path_factory.mktemp("ovf")
    key = f"ptest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        customer = Customer(key=key, name="overview filters test")
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

    bank = pd.DataFrame([
        ("T1", "V1", 1000.0, MARCH),   # matches INV-1 (Friction, March)
        ("T2", "V2", 700.0, APRIL),    # matches INV-2 (Hosur, April)
        ("T3", "V9", 500.0, MARCH),    # no bill -> BANK_ONLY (unassigned)
    ], columns=["bank_ref", "customer_ref", "amount", "value_date"])
    bank["narrative"] = "credit"
    bank["txn_type"] = "CREDIT"
    bank["used_in_recon"] = True
    bank = ensure_schema(bank, "bank_txns")
    bank["row_seq"] = range(len(bank))

    bills = pd.DataFrame([
        ("INV-1", "V1", 1000.0, "Friction", MARCH),
        ("INV-2", "V2", 700.0, "Hosur", APRIL),
        ("INV-3", "V3", 300.0, "Friction", APRIL),   # no credit -> BILL_ONLY
        ("INV-4", "V4", 200.0, None, MARCH),         # no unit  -> BILL_ONLY
    ], columns=["bill_number", "vendor_code", "net_payable_amount",
                "operating_unit", "payment_advice_date"])
    bills["bill_status"] = "APPROVED"
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
        s.commit()
    run_id = incremental.start_run(cust, {})
    with SessionLocal() as s:
        out, bank_pos, bill_pos = incremental.run_matching(s, cust, bz["stmt"], RULES)
        stats, _, _ = incremental.finalize_ledger(s, cust, run_id, out, bank_pos, bill_pos)
        run = s.get(Run, run_id)
        run.status = "succeeded"
        # what persist_success would have stored: the AR view reads the
        # per-run credit count from here
        run.payload = {"meta": {"counts": {"bank_credits": 3}}}
        s.commit()
    assert stats["matches_created"] == 2 and stats["exceptions_opened"] == 3

    yield {"cust": cust, "key": key}

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


def _ov(cust, **kw):
    with SessionLocal() as s:
        return overview.overview(s, cust, **kw)


def test_unfiltered_payload_unchanged(world):
    plain = _ov(world["cust"])
    explicit = _ov(world["cust"], date_from=None, date_to=None, units=None)
    assert plain == explicit
    assert "filters_applied" not in plain
    assert plain["gold"]["credits"] == 3 and plain["gold"]["bills"] == 4
    # T3 carries a customer_ref (the first exact signal) -> SIGNAL_BILL_NOT_FOUND,
    # so nothing is unrecognised here and the rate denominator is all 3 credits
    assert plain["open_exceptions"] == {"BANK_ONLY": 1, "BILL_ONLY": 2, "UNRECOGNISED": 0}
    assert plain["unrecognised_credits"] == 0 and plain["recognised_credits"] == 3
    assert plain["matched_credits"] == 2


def test_date_window_moves_the_counts(world):
    march = _ov(world["cust"], date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    assert march["gold"]["credits"] == 2            # T1, T3
    assert march["gold"]["bills"] == 2              # INV-1, INV-4
    assert march["open_exceptions"] == {"BANK_ONLY": 1, "BILL_ONLY": 1, "UNRECOGNISED": 0}   # T3, INV-4
    assert march["matched_credits"] == 1 and march["match_rate"] == 0.5
    assert march["filters_applied"]["from"] == "2026-03-01"
    assert march["filters_applied"]["bank_only_unassigned"] == 1
    # match status counts follow the match's created_at (today), not the
    # credit's value date: nothing was created in March 2026
    assert march["matches"] == {"OPEN": 0, "LOCKED": 0, "REJECTED": 0}
    april = _ov(world["cust"], date_from=date(2026, 4, 1), date_to=date(2026, 4, 30))
    assert april["gold"]["credits"] == 1 and april["gold"]["bills"] == 2
    assert april["open_exceptions"] == {"BANK_ONLY": 0, "BILL_ONLY": 1, "UNRECOGNISED": 0}   # INV-3
    assert [e["ref"] for e in april["top_exceptions"]] == ["INV-3"]


def test_operating_unit_filter_and_unassigned_bucket(world):
    friction = _ov(world["cust"], units=["Friction"])
    assert friction["gold"]["bills"] == 2                        # INV-1, INV-3
    assert friction["gold"]["credits"] == 1                      # T1 settles INV-1
    assert friction["open_exceptions"] == {"BANK_ONLY": 0, "BILL_ONLY": 1, "UNRECOGNISED": 0}   # INV-3
    assert friction["matched_credits"] == 1 and friction["match_rate"] == 1.0
    assert friction["matches"]["LOCKED"] == 1                    # T1<->INV-1 only
    assert friction["filters_applied"]["unassigned_included"] is False
    assert friction["filters_applied"]["bank_only_unassigned"] == 1   # T3 hidden, reported

    with_unassigned = _ov(world["cust"], units=["Friction", overview.UNASSIGNED_UNIT])
    assert with_unassigned["gold"]["credits"] == 2               # + T3 (unmatched)
    assert with_unassigned["open_exceptions"] == {"BANK_ONLY": 1, "BILL_ONLY": 2, "UNRECOGNISED": 0}  # + T3, INV-4
    assert with_unassigned["gold"]["bills"] == 3                 # + INV-4 (no unit)

    only_unassigned = _ov(world["cust"], units=[overview.UNASSIGNED_UNIT])
    assert only_unassigned["gold"]["credits"] == 1 and only_unassigned["gold"]["bills"] == 1
    assert only_unassigned["matches"] == {"OPEN": 0, "LOCKED": 0, "REJECTED": 0}


def test_routes(world):
    from app.main import app
    client = TestClient(app)
    key = world["key"]
    r = client.get(f"/api/customers/{key}/operating-units")
    assert r.status_code == 200
    assert [u["unit"] for u in r.json()["units"]] == ["Friction", "Hosur"]
    assert r.json()["unassigned"] == "UNASSIGNED"
    r = client.get(f"/api/overview?customer_id={key}&from=2026-03-01&to=2026-03-31"
                   "&operating_unit=Friction&operating_unit=UNASSIGNED")
    assert r.status_code == 200
    assert r.json()["filters_applied"]["operating_units"] == ["Friction", "UNASSIGNED"]
    assert r.json()["gold"]["credits"] == 2
    assert client.get(f"/api/overview?customer_id={key}&from=nope").status_code == 400
    assert client.get(f"/api/overview?customer_id={key}&from=2026-05-01&to=2026-04-01").status_code == 400
    ar = client.get(f"/api/ar?customer_id={key}").json()
    assert len(ar["runs"]) == 1 and ar["runs"][0]["credits"] == 3
