"""POST /api/documents: one source document -> managed storage (S3) + bronze.

Runs against moto's in-memory S3. Nothing is parsed, so the uploaded bytes
only need the right extension.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from db import SessionLocal, init_db  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, GoldBankTxn,  # noqa: E402
                       GoldBill, GoldFileRow, GoldLineageDoc, GoldRecovery,
                       IngestConflict, MatchRuleSetRow, SilverRecord,
                       SourceConfig)
from db.storage import S3Backend, storage  # noqa: E402

REGION = "ap-south-1"
BUCKET = "test-recon-documents"


SAMPLES = Path(__file__).resolve().parents[2].parent / "TWO MONTHS DATA" / "RNOTE & CRN files"


def _doc(doc_type, marker):
    """A minimal workbook the doc type's parser accepts; `marker` makes the
    bytes (so the sha256) unique."""
    import io
    import openpyxl
    doc_type = doc_type.lower().replace(" ", "_")
    if doc_type == "bill_status":
        head = ["Bill Number", "CO6 No", "Status", "Net Amt"]
        row = [f"B-{marker}", f"C6-{marker}", "PAYMENT MADE", 1000]
    else:
        head = ["Invoice No.", "CO6 No.", "CO7 No.", "PO No.",
                "CRN No." if doc_type == "crn" else "RNOTE No."]
        row = [f"B-{marker}", f"C6-{marker}", f"C7-{marker}", "PO1", "D1"]
    wb = openpyxl.Workbook()
    wb.active.append(head)
    wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def world(monkeypatch):
    for name, value in {"AWS_ACCESS_KEY_ID": "testing",
                        "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing",
                        "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET,
                         CreateBucketConfiguration={"LocationConstraint": REGION})
        previous = storage.backend
        storage.use(S3Backend(BUCKET, prefix="recon", client=s3))
        init_db()
        from app.routes import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        key = f"docs-{uuid.uuid4().hex[:8]}"
        r = client.post("/api/customers", json={"key": key, "name": "docs test"})
        assert r.status_code == 200, r.text
        try:
            yield {"client": client, "key": key, "s3": s3}
        finally:
            storage.use(previous)
            with SessionLocal() as s:
                pk = s.execute(select(Customer.id).where(Customer.key == key)).scalar_one()
                for model in (GoldFileRow, GoldRecovery, GoldBill, GoldBankTxn,
                              GoldLineageDoc, IngestConflict, SilverRecord,
                              AuditLog, BronzeFile, SourceConfig,
                              MatchRuleSetRow):
                    s.execute(delete(model).where(model.customer_id == pk))
                s.execute(delete(Customer).where(Customer.id == pk))
                s.commit()
            shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def _post(w, doc_type, name="x.xlsx", content=None, **extra):
    data = {"customer_id": w["key"], "doc_type": doc_type, **extra}
    body = content if content is not None else _doc(doc_type, uuid.uuid4().hex[:8])
    return w["client"].post("/api/documents", data=data,
                            files={"file": (name, body)})


@pytest.mark.parametrize("doc_type,slot", [
    ("bill_status", "bill_status"), ("Bill status", "bill_status"),
    ("RNote", "lineage_rnote"), ("CRN", "lineage_crn")])
def test_each_doc_type_lands_in_s3(world, doc_type, slot):
    r = _post(world, doc_type)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["slot"] == slot and j["deduplicated"] is False
    assert j["stored_path"].startswith(
        f"s3://{BUCKET}/recon/bronze/{world['key']}/{j['sha256']}")
    world["s3"].head_object(
        Bucket=BUCKET, Key=j["stored_path"].split(f"s3://{BUCKET}/", 1)[1])
    with SessionLocal() as s:
        row = s.get(BronzeFile, j["bronze_file_id"])
        assert row.source_type == slot and row.original_name == "x.xlsx"


def test_same_bytes_dedup(world):
    same = _doc("crn", "same")
    first = _post(world, "crn", content=same)
    again = _post(world, "crn", content=same)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["deduplicated"] is True
    assert again.json()["bronze_file_id"] == first.json()["bronze_file_id"]
    assert len(world["s3"].list_objects_v2(Bucket=BUCKET)["Contents"]) == 1


def test_same_bytes_other_doc_type_is_refused(world):
    same = _doc("crn", "same")
    assert _post(world, "crn", content=same).status_code == 201
    r = _post(world, "rnote", content=same)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "INVALID_INPUT"


def test_bad_input(world):
    assert _post(world, "invoice").status_code == 400            # doc_type
    assert _post(world, "crn", name="x.exe").status_code == 400  # extension
    assert _post(world, "crn", content=b"").status_code == 400   # empty
    assert _post(world, "crn", customer_id="nope-xyz").status_code == 400
    assert "Contents" not in world["s3"].list_objects_v2(Bucket=BUCKET)


# --- ingest half: store AND load gold -----------------------------------
def _sample(prefix):
    files = sorted(SAMPLES.glob(f"{prefix}*.xlsx")) if SAMPLES.is_dir() else []
    if not files:
        pytest.skip("sample documents not available")
    return files[0]


@pytest.mark.parametrize("doc_type,prefix,frame", [
    ("crn", "CRN IREPS", "lineage"), ("rnote", "RN IREPS", "lineage")])
def test_real_sample_is_stored_and_ingested(world, doc_type, prefix, frame):
    f = _sample(prefix)
    r = _post(world, doc_type, name=f.name, content=f.read_bytes())
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["ingested"] is True
    assert j["stats"]["by_frame"][f"lineage_{doc_type}"]["inserted"] > 0
    g = world["client"].get("/api/gold/lineage", params={
        "customer_id": world["key"], "bronze_file_id": j["bronze_file_id"]})
    assert g.status_code == 200 and g.json()["count"] > 0

    again = _post(world, doc_type, name=f.name, content=f.read_bytes())
    assert again.status_code == 200 and again.json()["deduplicated"] is True
    assert again.json()["stats"]["by_frame"][f"lineage_{doc_type}"]["inserted"] == 0


def test_unparseable_file_is_stored_but_not_ingested(world):
    r = _post(world, "bill_status", content=_doc("crn", "x"))   # no Bill Status table
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "PARSE_FAILED"
    with SessionLocal() as s:
        assert s.execute(select(func.count(BronzeFile.id))).scalar() >= 1
    assert len(world["s3"].list_objects_v2(Bucket=BUCKET)["Contents"]) == 1
