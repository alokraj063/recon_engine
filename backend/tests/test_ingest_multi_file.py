"""
POST /api/ingest accepts SEVERAL files per slot in one submission (the
multipart field repeated). Each file is its own bronze/silver/gold pass,
in the order attached, inside one ingestion.completed event; stats are
summed. Fixtures + the parseable-workbook helper come from the sibling
slot-mismatch test.
"""

import io
import sys
from pathlib import Path

import openpyxl
from sqlalchemy import select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import SessionLocal  # noqa: E402
from db.models import Customer, GoldBill  # noqa: E402
from recon.parsers.bill_status import HEADER_TO_FIELD  # noqa: E402
from tests.test_ingest_slot_mismatch import client, customer  # noqa: E402,F401


def _bill_status_xlsx(bill_no: str, net: int) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "bill"
    ws.append(list(HEADER_TO_FIELD))
    ws.append(["C1", "01/01/2026", "01/01/2026", bill_no, "NR", "VENDOR", "V1",
               f"CO6-{bill_no}", "01/01/2026", "PAYMENT MADE", net, net, 0, net,
               f"CO7-{bill_no}", "01/01/2026", "01/01/2026", "UNIT", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _gold_bill_numbers(key: str) -> set:
    with SessionLocal() as s:
        cust = s.execute(select(Customer).where(Customer.key == key)).scalar_one()
        return set(s.execute(
            select(GoldBill.bill_number).where(GoldBill.customer_id == cust.id)
        ).scalars())


def test_two_files_in_one_slot_are_one_ingestion(client, customer):  # noqa: F811
    a, b = _bill_status_xlsx("B-A", 1000), _bill_status_xlsx("B-B", 2000)
    r = client.post("/api/ingest", data={"customer_id": customer}, files=[
        ("bills", ("unit-a.xlsx", a)),
        ("bills", ("unit-b.xlsx", b)),
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["field"] for f in body["files"]] == ["bills", "bills"]
    assert [f["original_name"] for f in body["files"]] == ["unit-a.xlsx", "unit-b.xlsx"]
    assert {f["outcome"] for f in body["files"]} == {"registered"}
    assert body["stats"]["by_frame"]["bills"]["reported"] == 2
    assert body["stats"]["by_frame"]["bills"]["inserted"] == 2
    assert body["stats"]["rows_inserted"] >= 2
    assert body["selfcheck"] is None and body["selfchecks"] == []
    assert _gold_bill_numbers(customer) == {"B-A", "B-B"}

    ings = client.get("/api/ingestions", params={"customer_id": customer}).json()
    assert len(ings) == 1, "one submission = one ingestion row"
    assert len(ings[0]["files"]) == 2


def test_same_bytes_twice_in_one_submission_dedups_the_second(client, customer):  # noqa: F811
    a = _bill_status_xlsx("B-DUP", 500)
    r = client.post("/api/ingest", data={"customer_id": customer}, files=[
        ("bills", ("first.xlsx", a)),
        ("bills", ("again.xlsx", a)),
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["outcome"] for f in body["files"]] == ["registered", "deduped"]
    assert body["files"][0]["bronze_file_id"] == body["files"][1]["bronze_file_id"]
    assert body["stats"]["files_reused"] == 1
    assert body["stats"]["by_frame"]["bills"]["inserted"] == 1
    assert _gold_bill_numbers(customer) == {"B-DUP"}


def test_single_file_shape_is_unchanged(client, customer):  # noqa: F811
    r = client.post("/api/ingest", data={"customer_id": customer},
                    files={"bills": ("one.xlsx", _bill_status_xlsx("B-1", 10))})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["files"]) == 1 and body["files"][0]["outcome"] == "registered"
    assert set(body) == {"customer", "files", "stats", "selfcheck", "selfchecks"}
