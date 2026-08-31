"""
Configuration API surface (plan phases 1+3+4): copy overrides round-trip
sparsely through PUT/GET config, invalid copy is rejected loudly, and
lineage slots can be added / removed / re-adaptered through PUT /sources.

The router is mounted on a bare FastAPI app on purpose — importing
app.main would run configure_logging() at import time and create log
files under pytest (the "no log files during pytest" strategy); init_db()
is called directly instead of the lifespan.
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
from db.models import (AuditLog, Customer, MatchRuleSetRow,  # noqa: E402
                       SourceConfig)
from recon.engine import BILL_ACTIONS  # noqa: E402


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
    key = f"capi-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/customers", json={"key": key, "name": "cfg test"})
    assert r.status_code == 200, r.text
    yield key
    with SessionLocal() as s:
        cust = s.execute(select(Customer)
                         .where(Customer.key == key)).scalar_one()
        for model in (AuditLog, MatchRuleSetRow, SourceConfig):
            s.execute(delete(model).where(model.customer_id == cust.id))
        s.execute(delete(Customer).where(Customer.id == cust.id))
        s.commit()


def test_copy_overrides_roundtrip_sparse(client, customer):
    cfg = client.get(f"/api/customers/{customer}/config").json()
    rules = cfg["rules"]
    # effective copy starts as the defaults, overrides empty
    assert rules["copy_overrides"] == {}
    assert rules["copy_effective"]["expected_basis"] == BILL_ACTIONS

    # the UI round-trips the EFFECTIVE text with one edit — only the
    # edited entry may be stored (sparse), defaults must not freeze in
    body = dict(rules)
    body.pop("copy_effective")
    body["copy_overrides"] = {
        "expected_basis": {**BILL_ACTIONS, "ADVICE_DATE": "chase them"}}
    r = client.put(f"/api/customers/{customer}/config", json=body)
    assert r.status_code == 200, r.text
    got = r.json()["rules"]
    assert got["copy_overrides"] == {"expected_basis":
                                     {"ADVICE_DATE": "chase them"}}
    assert got["copy_effective"]["expected_basis"]["ADVICE_DATE"] == \
        "chase them"
    assert got["copy_effective"]["expected_basis"]["ZERO_NET_NOTHING_DUE"] \
        == BILL_ACTIONS["ZERO_NET_NOTHING_DUE"]

    # unknown section / unknown code / empty text are rejected
    for bad in ({"nope": {"X": "y"}},
                {"gap_type": {"NOT_A_CODE": "y"}},
                {"review": {"LOW": "  "}}):
        body["copy_overrides"] = bad
        assert client.put(f"/api/customers/{customer}/config",
                          json=body).status_code == 400


def test_new_scalar_knobs_roundtrip(client, customer):
    cfg = client.get(f"/api/customers/{customer}/config").json()["rules"]
    assert (cfg["batch_amount_slack"], cfg["amount_decimals"],
            cfg["ar_overdue_days"]) == (0.5, 2, 30)
    body = dict(cfg)
    body.pop("copy_effective")
    body.update(batch_amount_slack=1.0, amount_decimals=0,
                ar_overdue_days=45)
    got = client.put(f"/api/customers/{customer}/config",
                     json=body).json()["rules"]
    assert (got["batch_amount_slack"], got["amount_decimals"],
            got["ar_overdue_days"]) == (1.0, 0, 45)


def test_lineage_slots_add_remove(client, customer):
    # add a third lineage slot using an existing lineage adapter
    r = client.put(f"/api/customers/{customer}/sources",
                   json={"sources": {"lineage_grn2": "ireps_rnote"}})
    assert r.status_code == 200, r.text
    assert r.json()["sources"]["lineage_grn2"] == "ireps_rnote"

    # remove it again (null); singleton slots refuse removal
    r = client.put(f"/api/customers/{customer}/sources",
                   json={"sources": {"lineage_grn2": None}})
    assert r.status_code == 200
    assert "lineage_grn2" not in r.json()["sources"]
    assert client.put(f"/api/customers/{customer}/sources",
                      json={"sources": {"bank_statement": None}}
                      ).status_code == 400

    # role mismatch and bad slot names are rejected
    assert client.put(f"/api/customers/{customer}/sources",
                      json={"sources": {"lineage_x": "hsbc"}}
                      ).status_code == 400
    assert client.put(f"/api/customers/{customer}/sources",
                      json={"sources": {"weird_slot": "ireps_rnote"}}
                      ).status_code == 400

    # entity_key params validate against gold columns
    ok = client.put(f"/api/customers/{customer}/sources",
                    json={"sources": {},
                          "params": {"bill_status":
                                     {"entity_key": ["bill_number"]}}})
    assert ok.status_code == 200, ok.text
    bad = client.put(f"/api/customers/{customer}/sources",
                     json={"sources": {},
                           "params": {"bill_status":
                                      {"entity_key": ["not_a_column"]}}})
    assert bad.status_code == 400
