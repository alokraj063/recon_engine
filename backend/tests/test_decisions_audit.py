"""
WHO decided, WHY, and WHAT a change replaced:

  * every audit event raised by a signed-in request carries its user
    (audit_log.actor_user_id, served as `actor` by GET /api/audit)
  * match and non-IREPS decisions take an optional note, stored on the
    ledger row with the deciding user and echoed by GET /api/ledger
  * a Source change from Bank Transactions logs what the credit read as
    BEFORE — the engine's own reading when no analyst had decided (it
    used to log `was: null`)
  * a matching-config save logs each changed field old -> new, and a
    save that changed nothing logs nothing
  * an AWAITING_STATUS credit names the in-flight bill(s) behind it
"""

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import AuthUser, require_user  # noqa: E402
from db import SessionLocal  # noqa: E402
from db.audit import set_actor  # noqa: E402
from db.models import GoldBankTxn, MatchLedger, User  # noqa: E402
from tests.test_multi_statement import bills, world  # noqa: E402,F401
from tests.test_unrecognised_receipts import _ingest, _reconcile, credits  # noqa: E402


@pytest.fixture
def analyst(world):  # noqa: F811
    """A real users row, signed in on the world's client."""
    with SessionLocal() as s:
        u = User(email=f"analyst-{uuid.uuid4().hex[:8]}@example.com",
                 name="Asha Analyst", password_hash="x", is_active=True)
        s.add(u)
        s.commit()
        uid = u.id
    user = AuthUser(id=uid, email="a@example.com", name="Asha Analyst")

    async def _signed_in():
        set_actor(uid)
        return user
    app = world["client"].app
    saved = app.dependency_overrides.get(require_user)
    app.dependency_overrides[require_user] = _signed_in
    yield uid
    app.dependency_overrides[require_user] = saved
    with SessionLocal() as s:
        s.execute(delete(User).where(User.id == uid))
        s.commit()


def _audit(client, key, event_type=None):
    r = client.get("/api/audit", params={"customer_id": key})
    assert r.status_code == 200, r.text
    rows = r.json()
    return [e for e in rows if event_type is None or e["event_type"] == event_type]


def _match(client, key):
    r = client.get("/api/ledger", params={"customer_id": key})
    assert r.status_code == 200, r.text
    (m,) = r.json()["matches"]
    return m


def test_match_decisions_record_user_and_note(world, analyst):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bills": bills(["B1"])}, {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([("C1", 5000.0, "NR")])}, {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])
    m = _match(client, key)
    assert m["status"] == "LOCKED" and m["locked_by"] == "AUTO_HIGH"
    assert m["decided_by"] is None                 # the system locked it

    r = client.post(f"/api/matches/{m['id']}/unlock",
                    json={"note": "  zone looks wrong  "})
    assert r.status_code == 200, r.text
    m = _match(client, key)
    assert m["status"] == "OPEN"
    assert m["decided_by"] == "Asha Analyst"
    assert m["decision_note"] == "zone looks wrong"
    assert m["decided_at"]

    r = client.post(f"/api/matches/{m['id']}/accept", json={"note": "checked PO"})
    assert r.status_code == 200, r.text
    m = _match(client, key)
    assert (m["status"], m["decision_note"]) == ("LOCKED", "checked PO")

    # a note with no other body still works; blank normalizes to None
    client.post(f"/api/matches/{m['id']}/unlock")
    r = client.post(f"/api/matches/{m['id']}/reject", json={"note": "   "})
    assert r.status_code == 200, r.text
    assert _match(client, key)["decision_note"] is None
    # an over-long note is refused, not truncated
    assert client.post(f"/api/matches/{m['id']}/reopen",
                       json={"note": "x" * 501}).status_code == 400

    ev = _audit(client, key, "ledger.match_accepted")[0]
    assert ev["actor"] == "Asha Analyst" and ev["actor_user_id"] == analyst
    assert ev["details"]["note"] == "checked PO"
    assert ev["entity_label"] == f"M-{m['seq']}"
    # the run the user started is theirs too
    assert _audit(client, key, "run.succeeded")[0]["actor"] == "Asha Analyst"


def test_source_change_logs_the_auto_reading_it_replaced(world, analyst):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bank_txns": credits([("C4", 7000.0, None), ("C5", 7100.0, "NR")])},
            {"bank_txns": bz["a"]})
    with SessionLocal() as s:
        ids = dict(s.execute(select(GoldBankTxn.bank_ref, GoldBankTxn.id)
                             .where(GoldBankTxn.customer_id == pk)).all())

    def put(ref, source):
        r = client.put(f"/api/bank/{ids[ref]}/source",
                       json={"customer_id": key, "source": source})
        assert r.status_code == 200, r.text
        return _audit(client, key, "ledger.credit_source_set")[0]["details"]

    # no zone -> the page showed NON_IREPS (auto); with a zone -> IREPS
    assert put("C4", "IREPS") == {"was": "NON_IREPS", "was_auto": True,
                                  "source": "IREPS", "via": "bank"}
    assert put("C5", "NON_IREPS")["was"] == "IREPS"
    # once decided, "was" is the decision itself
    d = put("C4", "NON_IREPS")
    assert (d["was"], d["was_auto"]) == ("IREPS", False)
    ev = _audit(client, key, "ledger.credit_source_set")[0]
    assert ev["entity_label"] == "Credit · C4" and ev["actor"] == "Asha Analyst"


def test_non_ireps_decision_note_and_user(world, analyst):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bank_txns": credits([("C4", 7000.0, None)])}, {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])
    exc = client.get("/api/ledger", params={"customer_id": key}).json()["exceptions"][0]
    r = client.post(f"/api/exceptions/{exc['id']}/non-ireps/approve",
                    json={"note": "bank interest"})
    assert r.status_code == 200, r.text
    exc = client.get("/api/ledger", params={"customer_id": key}).json()["exceptions"][0]
    assert exc["resolved_by"] == "USER_NON_IREPS"
    assert exc["resolved_by_user"] == "Asha Analyst"
    assert exc["source_decided_by"] == "Asha Analyst"
    assert exc["source_note"] == "bank interest"
    d = _audit(client, key, "ledger.non_ireps_approved")[0]["details"]
    assert d == {"was": "NON_IREPS", "was_auto": True, "source": "NON_IREPS",
                 "note": "bank interest"}


def test_config_save_logs_changed_fields_only(world, analyst):  # noqa: F811
    client, key = world["client"], world["key"]
    cfg = client.get(f"/api/customers/{key}/config").json()["rules"]
    body = {k: v for k, v in cfg.items() if k != "copy_effective"}

    def put(**changes):
        r = client.put(f"/api/customers/{key}/config", json={**body, **changes})
        assert r.status_code == 200, r.text
        return _audit(client, key, "config.rules_updated")

    events = put(amount_tolerance=2.5,
                 weights={**body["weights"], "zone": body["weights"]["zone"] + 1})
    assert len(events) == 1
    ev = events[0]
    changes = {c["field"]: (c["from"], c["to"]) for c in ev["details"]["changes"]}
    assert changes["amount_tolerance"] == (body["amount_tolerance"], 2.5)
    assert changes["weights.zone"] == (body["weights"]["zone"], body["weights"]["zone"] + 1)
    assert len(changes) == 2
    assert ev["actor"] == "Asha Analyst" and ev["entity_label"] == "Matching config"

    # saving the same values again is not a change
    assert len(put(amount_tolerance=2.5,
                   weights={**body["weights"], "zone": body["weights"]["zone"] + 1})) == 1

    r = client.put(f"/api/customers/{key}/sources",
                   json={"sources": {"lineage_grn": "ireps_rnote"}})
    assert r.status_code == 200, r.text
    ev = _audit(client, key, "config.sources_updated")[0]
    assert {"field": "lineage_grn.adapter", "from": None, "to": "ireps_rnote"} \
        in ev["details"]["changes"]
    assert ev["entity_label"] == "Source setup"


def test_awaiting_status_names_the_in_flight_bill(world, analyst):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    b = bills(["P1", "P2"])
    b["bill_status"] = ["PASSED", "CO7 DONE"]
    b["net_payable_amount"] = [7000.0, 7000.0]
    _ingest(pk, {"bills": b}, {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([("C7", 7000.0, "NR")])}, {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])
    (exc,) = [e for e in client.get("/api/ledger", params={"customer_id": key})
              .json()["exceptions"] if e["exception_type"] == "BANK_ONLY"]
    assert exc["gap_detail"] == "AWAITING_STATUS"
    assert sorted((g["bill_number"], g["bill_status"]) for g in exc["gap_bills"]) \
        == [("P1", "PASSED"), ("P2", "CO7 DONE")]
    # a MatchLedger row was never created for it
    with SessionLocal() as s:
        assert not s.execute(select(MatchLedger.id)
                             .where(MatchLedger.customer_id == pk)).first()
