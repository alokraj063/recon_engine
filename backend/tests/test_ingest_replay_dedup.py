"""
Regression: replaying the SAME bronze file (identical bytes re-ingested,
e.g. a user re-uploading a file they already ingested) must be a safe
no-op even when some of its rows have a blank/placeholder natural key.

Bug: bill_number '-' (IREPS's works-contract convention), a blank
bank_ref, or a blank lineage doc_no all normalise to no usable entity
key, so entity-key matching correctly never merges them ACROSS two
different files — but that same rule meant a second ingest of the exact
same file tried to insert those rows again, hitting the
(bronze_file_id, row_seq) UNIQUE constraint with a raw IntegrityError.
`existing_file_rows` in db/ingest.py closes that gap: a blank-keyed row
already sitting at this row_seq for THIS bronze file is reused instead.
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
                       GoldBill, GoldFileRow, GoldLineageDoc, GoldRecovery,
                       SilverRecord)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402


@pytest.fixture()
def customer(tmp_path):
    init_db()
    key = f"replaytest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        c = Customer(key=key, name="ingest replay dedup test")
        s.add(c)
        s.flush()

        def bronze(name, source_type):
            p = tmp_path / name
            p.write_text(name + uuid.uuid4().hex)
            return register_file(s, c, source_type, p, name).id

        ids = {
            "bills": bronze("bills.txt", "bill_status"),
            "bank": bronze("bank.txt", "bank_statement"),
            "rnote": bronze("rnote.txt", "lineage_rnote"),
        }
        s.commit()
        pk, ckey = c.id, c.key
    yield {"pk": pk, "bronze": ids}
    with SessionLocal() as s:
        for model in (AuditLog, GoldFileRow, GoldRecovery, GoldBill,
                      GoldBankTxn,
                     GoldLineageDoc, SilverRecord, BronzeFile):
            s.execute(delete(model).where(model.customer_id == pk))
        s.execute(delete(Customer).where(Customer.id == pk))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / ckey, ignore_errors=True)


def _bills_frame(n=3, bill_number="-", keyed_prefix=None):
    """n bills sharing the blank/placeholder bill_number IREPS uses for
    works-contract bills — none of them can be entity-matched by key.
    `keyed_prefix` prepends one REAL-keyed bill (matchable by
    bill_number/submission_ref), so a file mixes rows that merge into an
    existing bill with rows that don't — the shape a real IREPS export
    actually has, and the one that exposes the replay bug (only rows
    NOT entity-matched end up "owned" by this bronze file, so the
    whole-file row-count shortcut can't shortcut a replay)."""
    rows = []
    if keyed_prefix:
        rows.append({"bill_number": keyed_prefix, "submission_ref": "CO6-K",
                    "net_payable_amount": 500.0, "bill_status": "PAYMENT MADE"})
    rows += [{"bill_number": bill_number, "contract_no": f"WC{i}",
             "submission_ref": f"CO6-{i}", "net_payable_amount": 1000.0 + i,
             "bill_status": "REGISTERED"} for i in range(n)]
    df = ensure_schema(pd.DataFrame(rows), "bills")
    df["row_seq"] = range(len(df))
    return df


def _bank_frame(n=2, keyed_ref=None):
    rows = []
    if keyed_ref:
        rows.append({"bank_ref": keyed_ref, "value_date": pd.Timestamp("2026-03-18"),
                    "amount": 500.0, "used_in_recon": True})
    rows += [{"bank_ref": None, "value_date": pd.Timestamp("2026-03-18"),
             "amount": 1000.0 + i, "used_in_recon": True} for i in range(n)]
    df = ensure_schema(pd.DataFrame(rows), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def _lineage_frame(n=2, doc_type="RNOTE", keyed_doc_no=None):
    rows = []
    if keyed_doc_no:
        rows.append({"doc_type": doc_type, "doc_no": keyed_doc_no,
                    "invoice_no": "INV-K"})
    rows += [{"doc_type": doc_type, "doc_no": None,
             "invoice_no": f"INV-{i}"} for i in range(n)]
    df = ensure_schema(pd.DataFrame(rows), "lineage_docs")
    df["row_seq"] = range(len(df))
    return df


def test_replayed_bills_file_with_blank_keys_is_idempotent(customer, tmp_path):
    """The exact production shape: a file whose ROW 0 entity-matches an
    already-existing bill from an EARLIER file (so it gets merged, not
    owned by this bronze file) while its other rows carry the blank
    bill_number '-' and DO get freshly owned by this bronze file. That
    split is what breaks the whole-file row-count shortcut
    (`len(owned rows) != len(parsed rows)`) and is exactly what exposed
    the original IntegrityError on a replay."""
    pk, bz = customer["pk"], customer["bronze"]

    # an earlier file plants the bill this file's row 0 will merge into
    with SessionLocal() as s:
        c = s.get(Customer, pk)
        p = tmp_path / "earlier_bills.txt"
        p.write_text("earlier-file-bytes")
        earlier_bronze = register_file(s, c, "bill_status", p, "earlier_bills.txt").id
        s.commit()
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, {"bills": _bills_frame(n=0, keyed_prefix="B-KEYED")},
                           {"bills": earlier_bronze})
        s.commit()

    frame = _bills_frame(n=3, keyed_prefix="B-KEYED")   # 1 merges + 3 blank-key
    with SessionLocal() as s:
        _ids, stats1 = ingest_gold_frames(
            s, pk, {"bills": frame}, {"bills": bz["bills"]})
        s.commit()
    assert stats1["rows_inserted"] == 3        # only the blank-key rows
    assert stats1["rows_reused"] == 1           # the keyed row merged in
    assert stats1["conflicts"] == 0

    # replaying the identical file/frame must NOT raise IntegrityError
    with SessionLocal() as s:
        ids2, stats2 = ingest_gold_frames(
            s, pk, {"bills": frame}, {"bills": bz["bills"]})
        s.commit()
    assert stats2["rows_inserted"] == 0, "blank-key bills reinserted on replay"
    assert stats2["rows_reused"] == 4
    assert len(ids2["bills"]) == 4

    with SessionLocal() as s:
        total = s.execute(select(func.count()).select_from(GoldBill)
                          .where(GoldBill.customer_id == pk)).scalar()
    assert total == 4, "replay must not duplicate rows"


def test_blank_key_bill_still_inserts_fresh_in_a_different_file(customer, tmp_path):
    """The fix must not over-merge: the SAME blank key in a genuinely
    DIFFERENT bronze file is still a distinct, newly-inserted bill."""
    pk, bz = customer["pk"], customer["bronze"]

    with SessionLocal() as s:
        c = s.get(Customer, pk)
        p = tmp_path / "second_bills.txt"
        p.write_text("second-file-bytes")
        other_bronze = register_file(s, c, "bill_status", p, "second_bills.txt").id
        s.commit()

    with SessionLocal() as s:
        ingest_gold_frames(s, pk, {"bills": _bills_frame()}, {"bills": bz["bills"]})
        s.commit()
    with SessionLocal() as s:
        _ids, stats = ingest_gold_frames(
            s, pk, {"bills": _bills_frame()}, {"bills": other_bronze})
        s.commit()
    assert stats["rows_inserted"] == 3, "a different file's blank-key bills must still insert"

    with SessionLocal() as s:
        total = s.execute(select(func.count()).select_from(GoldBill)
                          .where(GoldBill.customer_id == pk)).scalar()
    assert total == 6


def test_replayed_bank_file_with_blank_bank_ref_is_idempotent(customer, tmp_path):
    """Same mixed-ownership shape as the bills test: one row merges into
    an already-existing txn from an earlier file, the blank-bank_ref
    rows are freshly owned by this file — the split that defeats the
    whole-file row-count shortcut."""
    pk, bz = customer["pk"], customer["bronze"]

    with SessionLocal() as s:
        c = s.get(Customer, pk)
        p = tmp_path / "earlier_bank.txt"
        p.write_text("earlier-bank-bytes")
        earlier_bronze = register_file(s, c, "bank_statement", p, "earlier_bank.txt").id
        s.commit()
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, {"bank_txns": _bank_frame(n=0, keyed_ref="REF-K")},
                           {"bank_txns": earlier_bronze})
        s.commit()

    frame = _bank_frame(n=2, keyed_ref="REF-K")
    with SessionLocal() as s:
        _ids, stats1 = ingest_gold_frames(
            s, pk, {"bank_txns": frame}, {"bank_txns": bz["bank"]})
        s.commit()
    assert stats1["rows_inserted"] == 2 and stats1["rows_reused"] == 1

    with SessionLocal() as s:
        ids2, stats2 = ingest_gold_frames(
            s, pk, {"bank_txns": frame}, {"bank_txns": bz["bank"]})
        s.commit()
    assert stats2["rows_inserted"] == 0, "blank-ref txns reinserted on replay"
    assert stats2["rows_reused"] == 3
    assert len(ids2["bank_txns"]) == 3


def test_replayed_lineage_file_with_blank_doc_no_is_idempotent(customer, tmp_path):
    pk, bz = customer["pk"], customer["bronze"]

    with SessionLocal() as s:
        c = s.get(Customer, pk)
        p = tmp_path / "earlier_rnote.txt"
        p.write_text("earlier-rnote-bytes")
        earlier_bronze = register_file(s, c, "lineage_rnote", p, "earlier_rnote.txt").id
        s.commit()
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, {"lineage_rnote": _lineage_frame(n=0, keyed_doc_no="RN-K")},
                           {"lineage_rnote": earlier_bronze})
        s.commit()

    frame = _lineage_frame(n=2, keyed_doc_no="RN-K")
    with SessionLocal() as s:
        _ids, stats1 = ingest_gold_frames(
            s, pk, {"lineage_rnote": frame}, {"lineage_rnote": bz["rnote"]})
        s.commit()
    assert stats1["rows_inserted"] == 2 and stats1["rows_reused"] == 1

    with SessionLocal() as s:
        ids2, stats2 = ingest_gold_frames(
            s, pk, {"lineage_rnote": frame}, {"lineage_rnote": bz["rnote"]})
        s.commit()
    assert stats2["rows_inserted"] == 0, "blank-doc_no docs reinserted on replay"
    assert stats2["rows_reused"] == 3
    assert len(ids2["lineage_rnote"]) == 3
