"""
Bearer-token authentication with a locally generated RSA key standing in
for Okta's: real signatures, real expiry/audience/issuer checks, no network.
"""

import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from starlette.middleware.sessions import SessionMiddleware

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import auth, okta  # noqa: E402
from app.auth import SESSION_COOKIE, require_user  # noqa: E402
from app.routes import router as api_router  # noqa: E402
from db import SessionLocal, init_db  # noqa: E402
from db.models import User  # noqa: E402

ISSUER = "https://idp.example/oauth2/default"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


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
    return TestClient(app)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("OKTA_ISSUER", ISSUER)
    monkeypatch.setenv("OKTA_CLIENT_ID", "cid")
    monkeypatch.setenv("OKTA_CLIENT_SECRET", "secret")
    monkeypatch.delenv("OKTA_REQUIRED_SCOPE", raising=False)
    monkeypatch.setattr(okta, "_jwks_client", lambda uri: FakeJwks())


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with SessionLocal() as s:
        s.execute(delete(User).where(User.name == "Bearer Test"))
        s.commit()


def _user(role="analyst", active=True, sub=None):
    email = f"bearer-{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as s:
        row = User(email=email, name="Bearer Test", password_hash=None,
                   role=role, is_active=active, okta_sub=sub)
        s.add(row)
        s.commit()
        return email, row.id


def _token(key=KEY, **over):
    claims = {"iss": ISSUER, "aud": "api://default", "exp": int(time.time()) + 600,
              "uid": "00u-" + uuid.uuid4().hex[:6], "sub": "x@example.com",
              "scp": ["openid"]}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _get(client, token, path="/api/auth/me", method="get"):
    return getattr(client, method)(path, headers={"Authorization": f"Bearer {token}"})


def test_valid_token_for_a_known_user(client):
    email, uid = _user("analyst", sub="00u-known")
    r = _get(client, _token(uid="00u-known"))
    assert r.status_code == 200 and r.json()["id"] == uid and r.json()["role"] == "analyst"
    assert _get(client, _token(uid="00u-known"), "/api/customers").status_code == 200


def test_first_use_binds_the_okta_id_by_email(client):
    email, uid = _user("viewer")
    r = _get(client, _token(sub=email))                  # sub = login name
    assert r.status_code == 200 and r.json()["id"] == uid
    with SessionLocal() as s:
        assert s.get(User, uid).okta_sub is not None


@pytest.mark.parametrize("make", [
    lambda: _token(exp=int(time.time()) - 10),           # expired
    lambda: _token(aud="api://other"),                   # wrong audience
    lambda: _token(iss="https://evil.example/oauth2/default"),
    lambda: _token(key=OTHER_KEY),                       # bad signature
    lambda: "not.a.jwt",
])
def test_bad_tokens_are_401(client, make):
    r = _get(client, make())
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "example.com" not in r.text


def test_machine_token_without_uid_is_refused(client):
    claims = {"iss": ISSUER, "aud": "api://default", "sub": "0oa-client",
              "exp": int(time.time()) + 600}
    token = jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})
    assert _get(client, token).status_code == 401


def test_unknown_inactive_and_roleless_users(client):
    assert _get(client, _token(sub="ghost@example.com")).status_code == 403
    _e, _ = _user("analyst", active=False, sub="00u-off")
    assert _get(client, _token(uid="00u-off")).status_code == 401
    _e, _ = _user(None, sub="00u-norole")
    r = _get(client, _token(uid="00u-norole"))
    assert r.status_code == 403 and r.json()["detail"]["error"] == "NO_ACCESS"


def test_viewer_token_is_read_only(client):
    _user("viewer", sub="00u-view")
    t = _token(uid="00u-view")
    assert _get(client, t, "/api/customers").status_code == 200
    r = client.post("/api/customers", json={"key": "x", "name": "x"},
                    headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 403


def test_required_scope(client, monkeypatch):
    _user("analyst", sub="00u-scope")
    monkeypatch.setenv("OKTA_REQUIRED_SCOPE", "recon.api")
    assert _get(client, _token(uid="00u-scope")).status_code == 403
    assert _get(client, _token(uid="00u-scope", scp=["recon.api"])).status_code == 200


def test_bearer_is_ignored_when_okta_is_not_configured(client, monkeypatch):
    _user("analyst", sub="00u-off2")
    monkeypatch.delenv("OKTA_CLIENT_SECRET")
    assert _get(client, _token(uid="00u-off2")).status_code == 401
