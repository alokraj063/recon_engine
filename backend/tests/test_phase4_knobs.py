"""
Phase-4 hardening knobs: batching slack and amount precision become
per-customer config (defaults byte-identical to the legacy constants —
the golden gate proves that end-to-end); the signal-coverage guard warns
on an all-NA exact-signal column; the bill entity key is configurable.
"""

import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.engine import reconcile  # noqa: E402
from recon.pipeline import check_signal_coverage  # noqa: E402
from recon.rules import FieldMapping  # noqa: E402


def _bank(**over):
    row = {"bank_ref": "R1", "narrative": "X", "amount": 100.30,
           "value_date": datetime(2026, 3, 18), "zone_guess": "NR"}
    row.update(over)
    return pd.DataFrame([row])


def _bills(rows):
    base = {"contract_no": "C", "zone": "NR", "bill_status": "PAYMENT MADE",
            "payment_advice_date": pd.Timestamp("2026-03-18"),
            "submission_ref": "S", "submission_date": pd.Timestamp("2026-03-10"),
            "payment_order_ref": "P",
            "payment_order_date": pd.Timestamp("2026-03-15"),
            "gross_amount": 0.0, "approved_amount": 0.0,
            "deduction_amount": 0.0, "vendor_code": "V", "org_unit": "AU",
            "recoveries": {}, "recovery_count": 0, "return_reason": None,
            "sheet": "s"}
    return pd.DataFrame([{**base, **r, "data_row": i}
                         for i, r in enumerate(rows)])


def test_batch_amount_slack_is_configurable():
    # historical precondition: only a credit that HAD an amount candidate
    # but lost the assignment reaches the batch pass — so R0 and R1 share
    # amount 100.0, R0 claims the single 100.0 bill, R1 falls through to
    # batching over the two ~50 bills (sum 100.30, gap 0.30)
    bank = pd.concat([_bank(bank_ref="R0", amount=100.0),
                      _bank(bank_ref="R1", amount=100.0)],
                     ignore_index=True)
    bills = _bills([{"bill_number": "B0", "net_payable_amount": 100.0},
                    {"bill_number": "B1", "net_payable_amount": 50.30},
                    {"bill_number": "B2", "net_payable_amount": 50.0}])
    # gap 0.30 <= default 0.5 slack -> BATCHED
    out = reconcile(bank, bills)
    assert sorted(out["matched"]["confidence"]) == ["BATCHED", "HIGH"]
    # tighter slack refuses the same batch
    out2 = reconcile(bank, bills, batch_amount_slack=0.05)
    assert list(out2["matched"]["confidence"]) == ["HIGH"]
    assert list(out2["bank_only"]["bank_ref"]) == ["R1"]


def test_amount_decimals_are_configurable():
    bills = _bills([{"bill_number": "B1", "net_payable_amount": 100.001}])
    # default 2dp: 100.004 and 100.001 both round to 100.0 -> match
    out = reconcile(_bank(amount=100.004), bills)
    assert len(out["matched"]) == 1
    # 3dp: 100.004 != 100.001 -> no amount candidate
    out3 = reconcile(_bank(amount=100.004), bills, amount_decimals=3)
    assert out3["matched"].empty


def test_signal_coverage_guard():
    bills = _bills([{"bill_number": "B1", "net_payable_amount": 100.0}])
    ok = check_signal_coverage(_bank(), bills, FieldMapping())
    assert ok is None
    # bank side never populated the mapped signal column
    cov = check_signal_coverage(_bank(zone_guess=None), bills, FieldMapping())
    assert cov == {"passed": False,
                   "problems": [{"side": "bank", "field": "zone_guess",
                                 "signal": "zone"}]}
    # empty frames stay quiet; no signals configured stays quiet
    assert check_signal_coverage(_bank().iloc[0:0], bills,
                                 FieldMapping()) is None
    assert check_signal_coverage(_bank(zone_guess=None), bills,
                                 FieldMapping(exact_signals=())) is None


def test_configurable_bill_entity_key(tmp_path):
    """An ERP without a CO6-like ref merges daily exports on bill_number
    alone once entity_key says so; the default two-part key would insert
    a duplicate."""
    from db import SessionLocal, init_db
    from db.bronze import register_file
    from db.ingest import ingest_gold_frames
    from db.models import (AuditLog, BronzeFile, Customer, GoldBill,
                           SilverRecord)
    from db.storage import storage
    from recon.gold import ensure_schema

    init_db()
    key = f"ekey-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        customer = Customer(key=key, name="entity-key test")
        s.add(customer)
        s.flush()

        def bronze(name):
            p = tmp_path / name
            p.write_text(name + uuid.uuid4().hex)
            return register_file(s, customer, "bill_status", p, name).id
        b1, b2 = bronze("day1.csv"), bronze("day2.csv")
        s.commit()
        cust = customer.id

    def frame(sub_ref, net):
        df = pd.DataFrame([{"bill_number": "INV-1",
                            "submission_ref": sub_ref,
                            "net_payable_amount": net}])
        df = ensure_schema(df, "bills")
        df["row_seq"] = range(len(df))
        return df

    try:
        with SessionLocal() as s:
            ingest_gold_frames(s, cust, {"bills": frame("S1", 10.0)},
                               {"bills": b1},
                               entity_keys={"bills": ["bill_number"]})
            s.commit()
        with SessionLocal() as s:
            _, stats = ingest_gold_frames(
                s, cust, {"bills": frame("S2", 20.0)}, {"bills": b2},
                entity_keys={"bills": ["bill_number"]})
            s.commit()
        # merged on bill_number alone: an UPDATE, not a second row
        assert stats["rows_inserted"] == 0
        assert stats["bills_updated"] == 1
        with SessionLocal() as s:
            n = len(list(s.execute(
                select(GoldBill.id).where(GoldBill.customer_id == cust)
            ).scalars()))
        assert n == 1
    finally:
        with SessionLocal() as s:
            for model in (AuditLog, GoldBill, SilverRecord, BronzeFile):
                s.execute(delete(model).where(model.customer_id == cust))
            s.execute(delete(Customer).where(Customer.id == cust))
            s.commit()
        shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)
