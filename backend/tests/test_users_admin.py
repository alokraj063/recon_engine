"""User administration: the admin-only /api/users routes and the CSV import."""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from starlette.middleware.sessions import SessionMiddleware

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import auth  # noqa: E402
from app.auth import SESSION_COOKIE, AuthUser, require_user  # noqa: E402
from app.routes import router as api_router  # noqa: E402
from db import SessionLocal, init_db  # noqa: E402
from db.models import AuditLog, User  # noqa: E402

TAG = "UsersAdminTest"


def _app(role):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t", session_cookie=SESSION_COOKIE)
    app.include_router(auth.router)
    app.include_router(api_router)

    async def _me():
        return AuthUser(id=0, email="t@example.com", name="T", role=role)
    app.dependency_overrides[require_user] = _me
    return TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def db():
    init_db()


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with SessionLocal() as s:
        ids = [u.id for u in s.query(User).filter(User.name.like(f"{TAG}%"))]
        if ids:
            s.execute(delete(AuditLog).where(AuditLog.entity_type == "user",
                                             AuditLog.entity_id.in_([str(i) for i in ids])))
            s.execute(delete(User).where(User.id.in_(ids)))
        s.commit()


def _email():
    return f"ua-{uuid.uuid4().hex[:8]}@example.com"


def test_only_admins_reach_user_admin():
    for role in ("analyst", "viewer", None):
        assert _app(role).get("/api/users").status_code == 403
    assert _app("admin").get("/api/users").status_code == 200


def test_create_list_update_and_audit():
    c = _app("admin")
    email = _email()
    r = c.post("/api/users", json={"email": email.upper(), "name": TAG, "role": "analyst"})
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["email"] == email and u["role"] == "analyst"
    assert u["has_password"] is False and u["okta_linked"] is False

    assert c.post("/api/users", json={"email": email, "name": TAG, "role": "viewer"}
                  ).json()["detail"]["error"] == "USER_EXISTS"
    assert c.post("/api/users", json={"email": email + "x", "role": "boss"}
                  ).status_code == 400
    assert c.post("/api/users", json={"email": "nope", "role": "viewer"}).status_code == 400

    r = c.patch(f"/api/users/{u['id']}", json={"role": "viewer", "is_active": False})
    assert r.json()["role"] == "viewer" and r.json()["is_active"] is False
    assert any(x["id"] == u["id"] for x in c.get("/api/users").json())
    assert c.patch("/api/users/999999", json={"role": "viewer"}).status_code == 404

    with SessionLocal() as s:
        rows = s.query(AuditLog).filter(AuditLog.entity_id == str(u["id"])).all()
        types = {x.event_type for x in rows}
        assert {"user.created", "user.role_changed", "user.deactivated"} <= types
        assert "example.com" not in " ".join(str(x.details) for x in rows)


def test_the_last_active_admin_cannot_be_removed():
    c = _app("admin")
    with SessionLocal() as s:
        was_active = [u.id for u in s.query(User).filter(
            User.role == "admin", User.is_active.is_(True))]
        s.query(User).filter(User.id.in_(was_active)).update(
            {"is_active": False}, synchronize_session=False)
        s.commit()
    try:
        r = c.post("/api/users", json={"email": _email(), "name": TAG, "role": "admin"})
        uid = r.json()["id"]
        bad = c.patch(f"/api/users/{uid}", json={"role": "viewer"})
        assert bad.status_code == 409 and bad.json()["detail"]["error"] == "LAST_ADMIN"
        assert c.patch(f"/api/users/{uid}", json={"is_active": False}).status_code == 409
    finally:
        with SessionLocal() as s:
            s.query(User).filter(User.id.in_(was_active)).update(
                {"is_active": True}, synchronize_session=False)
            s.commit()


def _cli(tmp_path, *args):
    env = {"RECON_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(BACKEND / "scripts/create_user.py"), *args],
                          capture_output=True, text=True, cwd=BACKEND, env=env)


def test_csv_import_is_all_or_nothing_and_creates_passwordless_users(tmp_path):
    subprocess.run([sys.executable, "-c", "from db import init_db; init_db()"],
                   cwd=BACKEND, env={"RECON_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
                   check=True, capture_output=True)
    good = tmp_path / "ok.csv"
    good.write_text("email,name,role\na@example.com,A,analyst\nb@example.com,B,Viewer\n")
    r = _cli(tmp_path, "--import-csv", str(good))
    assert r.returncode == 0, r.stderr
    listing = _cli(tmp_path, "--list").stdout
    assert "a@example.com" in listing and "analyst" in listing and "okta" in listing

    bad = tmp_path / "bad.csv"
    bad.write_text("email,name,role\nc@example.com,C,analyst\nd@example.com,D,boss\n")
    r = _cli(tmp_path, "--import-csv", str(bad))
    assert r.returncode != 0 and "line 3" in (r.stderr + r.stdout)
    assert "c@example.com" not in _cli(tmp_path, "--list").stdout
