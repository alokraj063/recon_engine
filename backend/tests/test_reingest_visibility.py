"""
Regression: an export whose rows ALREADY exist in gold must still be
visible as its own ingestion.

Gold rows are entities, not file rows — the entity upsert keeps ONE row
for a bill, stamped with the bronze file that first inserted it. So a
later export that re-reports 34 known bills inserts nothing, owns nothing,
and used to disappear completely: the Bills tab's ingestion filter did not
list it, filtering by it returned zero rows, and a bank statement whose
credits had all arrived on an earlier one could not even be picked to
reconcile (no gold rows "of its own" -> 404 STATEMENT_NOT_FOUND).

gold.file_rows records what each file REPORTED, whether it inserted the
row, updated it, or matched it unchanged; db/gold.py reported_by_file is
the one place readers ask "what did this upload bring".
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

from db import SessionLocal, incremental, init_db, reconcile_gold  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, GoldBankTxn,  # noqa: E402
                       GoldBill, GoldFileRow, GoldRecovery, SilverRecord)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402


@pytest.fixture()
def world(tmp_path):
    """Throwaway customer with two bill-status files and two statements."""
    init_db()
    key = f"sighting-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        c = Customer(key=key, name="re-ingest visibility test")
        s.add(c)
        s.flush()

        def bronze(name, source_type):
            p = tmp_path / name
            p.write_text(name + uuid.uuid4().hex)   # distinct bytes
            return register_file(s, c, source_type, p, name).id

        ids = {
            "full": bronze("bills_full.txt", "bill_status"),
            "subset": bronze("bills_subset.txt", "bill_status"),
            "stmt1": bronze("stmt1.txt", "bank_statement"),
            "stmt2": bronze("stmt2.txt", "bank_statement"),
        }
        s.commit()
        pk, ckey = c.id, c.key
    yield {"pk": pk, "bronze": ids}
    with SessionLocal() as s:
        for model in (AuditLog, GoldFileRow, GoldRecovery, GoldBill,
                      GoldBankTxn, SilverRecord, BronzeFile):
            s.execute(delete(model).where(model.customer_id == pk))
        s.execute(delete(Customer).where(Customer.id == pk))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / ckey, ignore_errors=True)


def bills(numbers, status="REGISTERED"):
    df = ensure_schema(pd.DataFrame([
        {"bill_number": n, "submission_ref": f"CO6-{n}",
         "net_payable_amount": 1000.0 + i, "bill_status": status,
         "payment_advice_date": pd.Timestamp("2026-03-18")}
        for i, n in enumerate(numbers)]), "bills")
    df["row_seq"] = range(len(df))
    return df


def credits(refs):
    df = ensure_schema(pd.DataFrame([
        {"bank_ref": r, "value_date": pd.Timestamp("2026-03-18"),
         "amount": 1000.0 + i, "used_in_recon": True}
        for i, r in enumerate(refs)]), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def _ingest(pk, frames, bronze_ids):
    with SessionLocal() as s:
        ids, stats = ingest_gold_frames(s, pk, frames, bronze_ids)
        s.commit()
    return ids, stats


def test_a_re_export_of_known_bills_is_still_its_own_ingestion(world):
    pk, bz = world["pk"], world["bronze"]
    _ingest(pk, {"bills": bills(["B1", "B2", "B3", "B4"])},
            {"bills": bz["full"]})

    # the same bills again (one of them progressed) — nothing new to insert
    _ids, stats = _ingest(
        pk, {"bills": bills(["B2", "B3"], status="PAYMENT MADE")},
        {"bills": bz["subset"]})
    assert stats["rows_inserted"] == 0
    assert stats["bills_updated"] == 2
    assert stats["rows_reported"] == 2, "the file still reported two bills"

    with SessionLocal() as s:
        # the whole gold table is still four bills...
        df, _prov, total = reconcile_gold.gold_frame(s, "bills", pk)
        assert total == 4
        # ...and filtering to the re-export shows exactly what it carried
        df, _prov, total = reconcile_gold.gold_frame(
            s, "bills", pk, bronze_file_id=bz["subset"])
        assert total == 2
        assert set(df["bill_number"]) == {"B2", "B3"}
        assert set(df["bill_status"]) == {"PAYMENT MADE"}

        # and it appears in the ingestion filter at all
        files = {f["bronze_file_id"]: f for f in reconcile_gold.gold_files(s, pk)}
        assert files[bz["subset"]]["gold_counts"]["bills"] == 2
        assert files[bz["full"]]["gold_counts"]["bills"] == 4


def test_a_statement_whose_credits_all_deduped_can_still_be_reconciled(world):
    """The same rule on the bank side: an overlapping statement owns no
    gold rows of its own, but is still a statement you can pick and
    reconcile — with all of its credits, not none of them."""
    pk, bz = world["pk"], world["bronze"]
    _ingest(pk, {"bank_txns": credits(["R1", "R2"])},
            {"bank_txns": bz["stmt1"]})
    _ids, stats = _ingest(pk, {"bank_txns": credits(["R1", "R2"])},
                          {"bank_txns": bz["stmt2"]})
    assert stats["rows_inserted"] == 0

    with SessionLocal() as s:
        assert reconcile_gold.get_statement_bronze(s, pk, bz["stmt2"]) is not None
        frames = reconcile_gold.build_snapshot_frames(s, pk, bz["stmt2"])
        assert set(frames["bank_df"]["bank_ref"]) == {"R1", "R2"}
        pool = incremental.build_pool(s, pk, bz["stmt2"])
        assert set(pool["bank_df"]["bank_ref"]) == {"R1", "R2"}


def test_replaying_the_same_file_records_each_sighting_once(world):
    pk, bz = world["pk"], world["bronze"]
    frame = bills(["B1", "B2"])
    _ingest(pk, {"bills": frame}, {"bills": bz["full"]})
    _ingest(pk, {"bills": frame}, {"bills": bz["full"]})

    with SessionLocal() as s:
        seen = list(s.execute(
            select(GoldFileRow.row_seq)
            .where(GoldFileRow.bronze_file_id == bz["full"],
                   GoldFileRow.frame == "bills")).scalars())
    assert sorted(seen) == [0, 1]


def test_a_file_ingested_before_sightings_existed_still_filters(world):
    """Backward compatibility: rows already in gold have no sightings, so
    readers fall back to plain bronze_file_id ownership for that file."""
    pk, bz = world["pk"], world["bronze"]
    _ingest(pk, {"bills": bills(["B1", "B2"])}, {"bills": bz["full"]})
    with SessionLocal() as s:          # forget this file ever reported
        s.execute(delete(GoldFileRow)
                  .where(GoldFileRow.bronze_file_id == bz["full"]))
        s.commit()

    with SessionLocal() as s:
        _df, _prov, total = reconcile_gold.gold_frame(
            s, "bills", pk, bronze_file_id=bz["full"])
        assert total == 2
        files = {f["bronze_file_id"]: f for f in reconcile_gold.gold_files(s, pk)}
        assert files[bz["full"]]["gold_counts"]["bills"] == 2
