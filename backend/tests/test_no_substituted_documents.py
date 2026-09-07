"""
Regression: the API never substitutes a document for one you didn't send.

The repo's sample files used to stand in for an empty upload slot on the
seeded `default` customer (GET /api/defaults advertised them, and both
/api/ingest and the legacy /api/runs fell back to them). That made an
ingestion silently different from the files the user actually chose —
demo data landing in a real gold layer. Every ingestion and run is now
exactly the files it was given, for every customer, and an empty form is
an error rather than a four-document demo.

The router is mounted on a bare FastAPI app (not app.main) for the same
reason as test_config_api: no log files during pytest.
"""

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import SessionLocal, init_db  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer,  # noqa: E402
                       MatchRuleSetRow, SourceConfig)


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
    key = f"nosub-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/customers", json={"key": key, "name": "no-sub test"})
    assert r.status_code == 200, r.text
    yield key
    with SessionLocal() as s:
        cust = s.execute(select(Customer)
                         .where(Customer.key == key)).scalar_one()
        for model in (AuditLog, BronzeFile, MatchRuleSetRow, SourceConfig):
            s.execute(delete(model).where(model.customer_id == cust.id))
        s.execute(delete(Customer).where(Customer.id == cust.id))
        s.commit()


def _bronze_count(customer_key: str) -> int:
    with SessionLocal() as s:
        cust = s.execute(select(Customer)
                         .where(Customer.key == customer_key)).scalar_one()
        return s.execute(select(func.count()).select_from(BronzeFile)
                         .where(BronzeFile.customer_id == cust.id)).scalar()


def test_ingest_with_no_files_is_rejected_not_filled_in(client):
    """The `default` customer is the one the sample fallback used to back:
    an empty form must now fail, and register nothing."""
    before = _bronze_count("default")

    r = client.post("/api/ingest", data={"customer_id": "default"})

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == "INVALID_INPUT"
    assert "no files uploaded" in r.json()["detail"]["detail"]
    assert _bronze_count("default") == before, "an empty ingest registered a file"


def test_legacy_run_with_no_files_is_rejected(client):
    """Same for the one-shot /api/runs path — it used to reconcile the
    four sample documents end to end when given nothing at all."""
    r = client.post("/api/runs", data={"customer_id": "default"})

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "INVALID_INPUT"
    assert "statement" in detail["detail"] and "no file uploaded" in detail["detail"]


def test_no_defaults_endpoint(client):
    """Nothing advertises bundled documents any more."""
    assert client.get("/api/defaults?customer_id=default").status_code == 404


def test_a_file_posted_under_an_unknown_slot_is_rejected(client, customer):
    """A typo'd upload field used to be dropped in silence: the ingest
    'succeeded' without the document. It must fail instead — and before
    anything is registered."""
    r = client.post(
        "/api/ingest",
        files={"rnotes": ("rnote.xlsx", b"bytes",
                          "application/vnd.openxmlformats-officedocument"
                          ".spreadsheetml.sheet")},
        data={"customer_id": customer},
    )

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "INVALID_INPUT"
    assert "rnotes" in detail["detail"]
    assert _bronze_count(customer) == 0
