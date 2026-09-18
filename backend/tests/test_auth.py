"""
The login gate, exercised for real: real cookies, real bcrypt, no
dependency override.

This module clears the override tests/conftest.py installs, which is the
whole point of it existing — every other API test runs as a signed-in
user, so only this file can tell an ungated router from a gated one.

The app is assembled here rather than imported from app.main, following
the same rule as the other API tests: importing app.main runs
configure_logging() and starts writing log files under pytest. It is the
REAL router though, with its real router-level dependency — nothing
about the gate is re-implemented here.
"""

import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from starlette.middleware.sessions import SessionMiddleware

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import auth  # noqa: E402
from app.auth import SESSION_COOKIE, require_user  # noqa: E402
from app.routes import router as api_router  # noqa: E402
from db import SessionLocal, init_db  # noqa: E402
from db.models import AuditLog, User  # noqa: E402
from passwords import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def client():
    init_db()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret",
                       session_cookie=SESSION_COOKIE)
    app.include_router(auth.router)
    app.include_router(api_router)
    # conftest signs every other test in automatically; this file must see
    # the gate as a stranger would
    app.dependency_overrides.pop(require_user, None)
    return TestClient(app)


@pytest.fixture()
def user():
    """A throwaway login, deleted afterwards along with its audit rows."""
    email = f"auth-{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as s:
        row = User(email=email, name="Auth Test",
                   password_hash=hash_password(PASSWORD))
        s.add(row)
        s.commit()
        user_id = row.id
    auth._clear_failures(email)
    yield email, user_id
    with SessionLocal() as s:
        s.execute(delete(AuditLog).where(AuditLog.entity_type == "user",
                                         AuditLog.entity_id == str(user_id)))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()
    auth._clear_failures(email)


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login",
                       json={"email": email, "password": password})


# --- the gate ----------------------------------------------------------

def test_api_route_refuses_a_stranger(client):
    r = client.get("/api/customers")
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "NOT_AUTHENTICATED"


def test_every_api_route_carries_the_gate():
    """Structural, not behavioural: the gate is one router-level
    dependency, so a route added later inherits it. This fails loudly if
    someone mounts a second, ungated router."""
    names = [d.dependency for d in api_router.dependencies]
    assert require_user in names


def test_me_is_401_before_sign_in(client):
    assert client.get("/api/auth/me").status_code == 401


def test_sign_in_then_call_an_api_route(client, user):
    email, user_id = user
    r = login(client, email)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": user_id, "email": email, "name": "Auth Test"}

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email

    # the cookie now opens the rest of the API
    assert client.get("/api/customers").status_code == 200
    client.post("/api/auth/logout")


def test_login_stamps_last_login(client, user):
    email, user_id = user
    assert login(client, email).status_code == 200
    with SessionLocal() as s:
        assert s.get(User, user_id).last_login_at is not None
    client.post("/api/auth/logout")


def test_logout_closes_the_door(client, user):
    email, _ = user
    assert login(client, email).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/customers").status_code == 401


# --- rejections --------------------------------------------------------

def test_wrong_password_is_refused(client, user):
    email, _ = user
    r = login(client, email, "not-the-password")
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "INVALID_CREDENTIALS"
    assert client.get("/api/customers").status_code == 401


def test_unknown_email_and_wrong_password_are_indistinguishable(client, user):
    email, _ = user
    unknown = login(client, f"nobody-{uuid.uuid4().hex[:6]}@example.com", "x" * 12)
    wrong = login(client, email, "not-the-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_email_is_normalised(client, user):
    email, _ = user
    assert login(client, f"  {email.upper()}  ").status_code == 200
    client.post("/api/auth/logout")


def test_deactivating_revokes_a_live_session(client, user):
    """The cookie is stateless and cannot be recalled — require_user
    reloading the row on every request IS the revocation path."""
    email, user_id = user
    assert login(client, email).status_code == 200
    assert client.get("/api/customers").status_code == 200

    with SessionLocal() as s:
        s.get(User, user_id).is_active = False
        s.commit()

    assert client.get("/api/customers").status_code == 401
    assert login(client, email).status_code == 401  # and cannot sign back in


def test_repeated_failures_lock_the_account(client, user, monkeypatch):
    email, _ = user
    monkeypatch.setattr(auth, "LOCKOUT_AFTER", 3)
    for _ in range(3):
        assert login(client, email, "wrong").status_code == 401
    locked = login(client, email)          # the RIGHT password, still refused
    assert locked.status_code == 429
    assert locked.json()["detail"]["error"] == "TOO_MANY_ATTEMPTS"


def test_a_failed_login_is_audited_without_the_email(client, user):
    email, user_id = user
    login(client, email, "wrong")
    with SessionLocal() as s:
        rows = s.execute(
            select(AuditLog).where(AuditLog.entity_type == "user",
                                   AuditLog.entity_id == str(user_id))
        ).scalars().all()
    events = [r.event_type for r in rows]
    assert "auth.login_failed" in events
    # ids and reason codes only — no address anywhere in the trail
    for row in rows:
        assert email not in str(row.details or {})
