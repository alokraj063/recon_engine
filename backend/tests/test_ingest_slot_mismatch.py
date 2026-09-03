"""
Bronze dedup is content-addressed (customer, sha256) with no notion of
"slot" — by design, so the same bytes uploaded twice into the SAME slot
never duplicate. But that also means if a file's bytes were ever
registered under one slot, silently reusing that row for a DIFFERENT slot
would serve its now-wrong source_type/original_name forever, with no
error. _register_inputs (app/routes.py) must catch this and fail loud
instead — this is a regression test for exactly that gap.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import SessionLocal, init_db  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, GoldBankTxn,  # noqa: E402
                       GoldBill, GoldLineageDoc, GoldRecovery, SilverRecord,
                       SourceConfig)
from db.storage import storage  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_db()
    from fastapi import FastAPI

    from app.routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def customer(client):
    key = f"slotmix-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/customers", json={"key": key, "name": "slot mismatch test"})
    assert r.status_code == 200, r.text
    yield key
    with SessionLocal() as s:
        cust = s.execute(select(Customer).where(Customer.key == key)).scalar_one()
        # gold/silver/bronze rows first: SQLite doesn't enforce FKs across
        # the ATTACHed schema files, so leaving them behind after deleting
        # Customer would orphan them under a numeric id SQLite is free to
        # reuse for the NEXT customer created anywhere in the test run —
        # silently polluting an unrelated, later test's customer-scoped
        # queries (this bit a sibling test the first time this was missed)
        for model in (GoldRecovery, GoldBill, GoldBankTxn, GoldLineageDoc,
                      SilverRecord, BronzeFile, AuditLog, SourceConfig,
                      Customer):
            where = (model.id == cust.id) if model is Customer else (model.customer_id == cust.id)
            s.execute(delete(model).where(where))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def _minimal_bill_status_xlsx() -> bytes:
    """A genuinely parseable one-row Bill Status workbook, so a same-slot
    re-upload can be proven to succeed cleanly end to end, not just fail
    for an unrelated reason."""
    import io

    import openpyxl

    from recon.parsers.bill_status import HEADER_TO_FIELD

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "bill"
    headers = list(HEADER_TO_FIELD)
    ws.append(headers)
    ws.append(["C1", "01/01/2026", "01/01/2026", "B1", "NR", "VENDOR", "V1",
              "CO6-1", "01/01/2026", "PAYMENT MADE", 1000, 1000, 0, 1000,
              "CO7-1", "01/01/2026", "01/01/2026", "UNIT", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_slot_mismatch_fails_loud_but_a_genuine_repeat_still_dedups(client, customer):
    """One customer, one flow: a file's bytes attached to the wrong slot
    must fail loudly and immediately (never silently mislabeled) — while
    a genuine re-upload into the SAME slot it originally used must still
    dedup cleanly, exactly as before this guard existed."""
    mismatched = b"identical bytes, wrong slot the second time"

    r1 = client.post(
        "/api/ingest",
        files={"bills": ("bills.xlsx", mismatched,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"customer_id": customer, "slots": "bills"},
    )
    # the content is garbage, so parsing fails — but the bronze row for
    # "bills" is committed BEFORE parsing runs, so it persists regardless
    assert r1.status_code in (400, 422), r1.text

    r2 = client.post(
        "/api/ingest",
        files={"crn": ("crn.xlsx", mismatched,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"customer_id": customer, "slots": "crn"},
    )
    assert r2.status_code == 400, r2.text
    body = r2.json()["detail"]
    assert body["error"] == "INVALID_INPUT"
    assert "already" in body["detail"] and "bill_status" in body["detail"]

    # a genuine repeat, same slot both times, must still dedup cleanly
    real_bills = _minimal_bill_status_xlsx()
    r3 = client.post(
        "/api/ingest",
        files={"bills": ("real_bills.xlsx", real_bills,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"customer_id": customer, "slots": "bills"},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["files"][0]["outcome"] == "registered"

    r4 = client.post(
        "/api/ingest",
        files={"bills": ("real_bills_reexported.xlsx", real_bills,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"customer_id": customer, "slots": "bills"},
    )
    assert r4.status_code == 200, r4.text
    assert r4.json()["files"][0]["outcome"] == "deduped"
