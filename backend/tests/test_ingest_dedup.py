"""
Bank-txn entity dedup across byte-different files (regression).

The (bank_ref, value_date, amount) natural key must dedup transactions
that arrive again in a DIFFERENT file (e.g. a re-exported statement with
tweaked bytes, which file-level sha256 dedup cannot catch). This was
silently dead code: the DB side of the key is a datetime.date
('2026-03-18') while the frame side is a Timestamp
('2026-03-18 00:00:00'), so raw str() normalisation never matched.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete, func, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import SessionLocal, init_db  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, GoldBankTxn,  # noqa: E402
                       GoldFileRow, SilverRecord)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402


def _txn_frame() -> pd.DataFrame:
    """Two credits, dates as Timestamps — exactly what adapters emit."""
    df = pd.DataFrame({
        "bank_ref": ["REF-001", "REF-002"],
        "txn_type": ["TFR+", "TFR+"],
        "narrative": ["NEFT FROM X", "NEFT FROM Y"],
        "value_date": [pd.Timestamp("2026-03-18"), pd.Timestamp("2026-03-18")],
        "amount": [1000.0, 2500.5],
        "used_in_recon": [True, True],
    })
    df = ensure_schema(df, "bank_txns")
    df["row_seq"] = range(len(df))
    return df


@pytest.fixture()
def customer(tmp_path):
    init_db()
    key = f"dtest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        c = Customer(key=key, name="bank txn dedup test")
        s.add(c)
        s.flush()

        def bronze(name):
            p = tmp_path / name
            p.write_text(name + uuid.uuid4().hex)   # byte-different files
            return register_file(s, c, "bank_statement", p, name).id

        ids = {"file_a": bronze("stmt_a.txt"), "file_b": bronze("stmt_b.txt")}
        s.commit()
        pk, ckey = c.id, c.key
    yield {"pk": pk, "bronze": ids}
    with SessionLocal() as s:
        for model in (AuditLog, GoldFileRow, GoldBankTxn, SilverRecord,
                      BronzeFile):
            s.execute(delete(model).where(model.customer_id == pk))
        s.execute(delete(Customer).where(Customer.id == pk))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / ckey, ignore_errors=True)


def test_same_txns_in_different_files_dedup(customer):
    pk, bz = customer["pk"], customer["bronze"]

    with SessionLocal() as s:
        _ids, stats1 = ingest_gold_frames(
            s, pk, {"bank_txns": _txn_frame()}, {"bank_txns": bz["file_a"]})
        s.commit()
    assert stats1["rows_inserted"] == 2 and stats1["rows_reused"] == 0

    # the SAME transactions arriving in a byte-different second file:
    # entity key must reuse the existing gold rows, not insert duplicates
    with SessionLocal() as s:
        ids2, stats2 = ingest_gold_frames(
            s, pk, {"bank_txns": _txn_frame()}, {"bank_txns": bz["file_b"]})
        s.commit()
    assert stats2["rows_inserted"] == 0, "duplicate txns inserted"
    assert stats2["rows_reused"] == 2
    # and the returned ids map still resolves every incoming row
    assert len(ids2["bank_txns"]) == 2

    with SessionLocal() as s:
        total = s.execute(select(func.count()).select_from(GoldBankTxn)
                          .where(GoldBankTxn.customer_id == pk)).scalar()
    assert total == 2


def test_new_txn_in_second_file_still_inserts(customer):
    """Dedup must not over-merge: a genuinely new transaction in the
    second file (different amount) inserts alongside the reused ones."""
    pk, bz = customer["pk"], customer["bronze"]

    with SessionLocal() as s:
        ingest_gold_frames(
            s, pk, {"bank_txns": _txn_frame()}, {"bank_txns": bz["file_a"]})
        s.commit()

    second = _txn_frame()
    second.loc[1, "bank_ref"] = "REF-003"      # new txn, same date
    second.loc[1, "amount"] = 999.99
    with SessionLocal() as s:
        _ids, stats = ingest_gold_frames(
            s, pk, {"bank_txns": second}, {"bank_txns": bz["file_b"]})
        s.commit()
    assert stats["rows_reused"] == 1
    assert stats["rows_inserted"] == 1

    with SessionLocal() as s:
        total = s.execute(select(func.count()).select_from(GoldBankTxn)
                          .where(GoldBankTxn.customer_id == pk)).scalar()
    assert total == 3
