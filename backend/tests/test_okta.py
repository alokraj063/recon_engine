"""
Okta sign-in, with the identity provider faked: the Authlib client is
replaced, so what is exercised is OUR part — resolving the identity to a
row, issuing (or refusing) the session, and the audit trail.
"""

import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete
from starlette.middleware.sessions import SessionMiddleware

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import auth, okta  # noqa: E402
from app.auth import SESSION_COOKIE, require_user  # noqa: E402
from app.routes import router as api_router  # noqa: E402
from db import SessionLocal, init_db  # noqa: E402
from db.models import AuditLog, User  # noqa: E402


class FakeClient:
    """Stands in for Authlib's Okta client."""
    claims: dict = {}
    fail = False

    async def authorize_redirect(self, request, redirect_uri):
        return RedirectResponse("https://idp.example/authorize?ru=" + redirect_uri)

    async def authorize_access_token(self, request):
        if FakeClient.fail:
            raise ValueError("bad state")
        return {"userinfo": dict(FakeClient.claims)}


@pytest.fixture(scope="module")
def client():
    init_db()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret",
                       session_cookie=SESSION_COOKIE)
    app.include_router(auth.router)
    app.include_router(okta.router)
    app.include_router(api_router)
    app.dependency_overrides.pop(require_user, None)
    return TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("OKTA_ISSUER", "https://idp.example/oauth2/default")
    monkeypatch.setenv("OKTA_CLIENT_ID", "cid")
    monkeypatch.setenv("OKTA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OKTA_REDIRECT_URI", "http://localhost:5173/api/auth/okta/callback")
    monkeypatch.setattr(okta, "get_client", lambda: FakeClient())
    FakeClient.fail = False
    yield


def _user(role="analyst", active=True, sub=None):
    email = f"okta-{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as s:
        row = User(email=email, name="Okta Test", password_hash=None,
                   role=role, is_active=active, okta_sub=sub)
        s.add(row)
        s.commit()
        return email, row.id


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with SessionLocal() as s:
        ids = [u.id for u in s.query(User).filter(User.name == "Okta Test")]
        if ids:
            s.execute(delete(AuditLog).where(AuditLog.entity_type == "user",
                                             AuditLog.entity_id.in_([str(i) for i in ids])))
            s.execute(delete(User).where(User.id.in_(ids)))
            s.commit()


def _callback(client, **claims):
    FakeClient.claims = claims
    return client.get("/api/auth/okta/callback")


def test_providers_reflect_configuration(client, monkeypatch):
    assert client.get("/api/auth/providers").json() == {"password": True, "okta": True}
    monkeypatch.delenv("OKTA_CLIENT_SECRET")
    assert client.get("/api/auth/providers").json() == {"password": True, "okta": False}


def test_login_redirects_to_okta(client):
    r = client.get("/api/auth/okta/login")
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("https://idp.example/authorize")


def test_not_configured_login_goes_back_with_a_code(client, monkeypatch):
    monkeypatch.setattr(okta, "get_client", lambda: None)
    r = client.get("/api/auth/okta/login")
    assert r.headers["location"] == "/?auth_error=OKTA_NOT_CONFIGURED"


def test_first_sign_in_binds_sub_and_opens_the_session(client):
    email, uid = _user("analyst")
    r = _callback(client, sub="00u-abc", email=email.upper())
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "analyst"
    with SessionLocal() as s:
        assert s.get(User, uid).okta_sub == "00u-abc"
        assert s.get(User, uid).last_login_at is not None
    client.post("/api/auth/logout")


def test_later_sign_in_matches_on_sub_even_if_the_email_changed(client):
    _email, uid = _user("viewer", sub="00u-keep")
    r = _callback(client, sub="00u-keep", email="someone.else@example.com")
    assert r.headers["location"] == "/"
    assert client.get("/api/auth/me").json()["id"] == uid
    client.post("/api/auth/logout")


@pytest.mark.parametrize("make,claims_email,code", [
    (None, "nobody@example.com", "NO_ACCESS"),                  # no row
    (dict(role=None), None, "NO_ACCESS"),                       # row, no role
    (dict(active=False), None, "INACTIVE"),
])
def test_refusals_issue_no_session(client, make, claims_email, code):
    email = claims_email
    if make is not None:
        email, _ = _user(**make)
    r = _callback(client, sub="00u-x" + uuid.uuid4().hex[:6], email=email)
    assert r.headers["location"] == f"/?auth_error={code}"
    assert client.get("/api/auth/me").status_code == 401


def test_a_row_bound_to_another_identity_is_not_taken_over(client):
    email, uid = _user("admin", sub="00u-owner")
    r = _callback(client, sub="00u-intruder", email=email)
    assert r.headers["location"] == "/?auth_error=FAILED"
    with SessionLocal() as s:
        assert s.get(User, uid).okta_sub == "00u-owner"


def test_unverified_email_cannot_bind(client):
    email, _ = _user("admin")
    r = _callback(client, sub="00u-un", email=email, email_verified=False)
    assert r.headers["location"] == "/?auth_error=FAILED"


def test_token_failure_is_a_refusal(client):
    FakeClient.fail = True
    r = client.get("/api/auth/okta/callback")
    assert r.headers["location"] == "/?auth_error=FAILED"
    assert client.get("/api/auth/me").status_code == 401


def test_audit_rows_carry_codes_never_the_address(client):
    email, uid = _user("analyst")
    _callback(client, sub="00u-aud", email=email)
    client.post("/api/auth/logout")
    _callback(client, sub="00u-zzz", email="ghost@example.com")
    with SessionLocal() as s:
        rows = s.query(AuditLog).filter(
            AuditLog.event_type.in_(["auth.okta_login_succeeded",
                                     "auth.okta_login_failed"])).all()
        blob = " ".join(str(r.details) for r in rows)
    assert "example.com" not in blob
    assert any(r.event_type == "auth.okta_login_succeeded" and r.entity_id == str(uid)
               for r in rows)
    assert any(r.event_type == "auth.okta_login_failed"
               and r.details.get("reason") == "NO_SUCH_USER" for r in rows)
