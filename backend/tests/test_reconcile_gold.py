"""
Snapshot-from-gold equivalence: frames rebuilt from the gold layer must
reproduce the legacy freshly-parsed snapshot's results exactly on the
sample documents (same matched / bank_only / bill_only / review counts
and the same matched credit set).

Runs under a throwaway customer, like test_incremental.py, and cleans up
after itself (DB rows + bronze storage dir).
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

from db import SessionLocal, init_db, reconcile_gold  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, GoldBankTxn,  # noqa: E402
                       GoldBill, GoldLineageDoc, GoldRecovery, SilverRecord)
from db.storage import storage  # noqa: E402
from recon import MatchRuleSet, run_pipeline  # noqa: E402
from recon.sources import get_adapter  # noqa: E402


@pytest.fixture(scope="module")
def world():
    """Throwaway customer with all four sample documents ingested to gold."""
    init_db()
    cfg = sample_config()
    key = f"gtest-{uuid.uuid4().hex[:8]}"

    adapters = {t: get_adapter(t, k) for t, k in (
        ("bank_statement", "hsbc"), ("bill_status", "ireps"),
        ("lineage_rnote", "ireps_rnote"), ("lineage_crn", "ireps_crn"))}
    inputs = {"bank_statement": cfg.statement_pdf,
              "bill_status": cfg.bill_status,
              "lineage_rnote": cfg.rnote, "lineage_crn": cfg.crn}
    params = {"lineage_rnote": {"sheet": 0}, "lineage_crn": {"sheet": 0}}

    gold_frames, bronze_by_type = {}, {}
    with SessionLocal() as s:
        customer = Customer(key=key, name="reconcile-gold equivalence test")
        s.add(customer)
        s.flush()
        for source_type, path in inputs.items():
            bronze_by_type[source_type] = register_file(
                s, customer, source_type, Path(path), Path(path).name).id
        s.commit()
        customer_pk = customer.id

    frame_to_type = {"bank_txns": "bank_statement", "bills": "bill_status",
                     "recoveries": "bill_status",
                     "lineage_rnote": "lineage_rnote",
                     "lineage_crn": "lineage_crn"}
    for source_type, path in inputs.items():
        adapter = adapters[source_type]
        p = params.get(source_type, {})
        gold = adapter.to_gold(adapter.parse(path, p), p)
        gold_frames.update(gold)
    bronze_ids = {frame: bronze_by_type[frame_to_type[frame]]
                  for frame in gold_frames}
    with SessionLocal() as s:
        ingest_gold_frames(s, customer_pk, gold_frames, bronze_ids)
        s.commit()

    yield {"customer_pk": customer_pk,
           "statement_bronze_id": bronze_by_type["bank_statement"],
           "cfg": cfg, "adapters": adapters, "inputs": inputs,
           "params": params}

    with SessionLocal() as s:
        for model in (AuditLog, GoldRecovery, GoldBill, GoldBankTxn,
                      GoldLineageDoc, SilverRecord, BronzeFile):
            s.execute(delete(model).where(model.customer_id == customer_pk))
        s.execute(delete(Customer).where(Customer.id == customer_pk))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def test_snapshot_from_gold_matches_legacy_pipeline(world):
    rules = MatchRuleSet()

    # legacy path: parse fresh, reconcile in memory (DB-free)
    legacy = run_pipeline(world["inputs"], world["adapters"],
                          world["params"], rules)

    # new path: same reconciliation, frames rebuilt from gold
    with SessionLocal() as s:
        out, _bank_ids, _bill_ids = reconcile_gold.run_snapshot(
            s, world["customer_pk"], world["statement_bronze_id"], rules)

    for frame in ("matched", "bank_only", "bill_only", "match_review"):
        assert len(out[frame]) == len(legacy[frame]), (
            f"{frame}: gold={len(out[frame])} legacy={len(legacy[frame])}")

    # same credits matched, not just the same number of them
    legacy_refs = sorted(legacy["matched"]["bank_ref"].astype(str))
    gold_refs = sorted(out["matched"]["bank_ref"].astype(str))
    assert gold_refs == legacy_refs

    # matched amounts agree to the paisa
    assert round(out["matched"]["amount"].sum(), 2) == \
        round(legacy["matched"]["amount"].sum(), 2)
