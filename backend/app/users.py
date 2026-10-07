"""
User administration (admin only): who may sign in and with which role.

Mounted INSIDE app/routes.py's gated /api router, so it inherits
require_user; the router-level require_role("admin") narrows it further.
Accounts made here have no password — they sign in with Okta (an admin who
also wants a password login sets one with scripts/create_user.py).

Audit rows carry user ids and role NAMES only — never an address (an email
is PII; see the taxonomy in CLAUDE.md).
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from db import SessionLocal
from db.audit import record_event
from db.models import User
from logging_setup import get_logger
from passwords import normalize_email

from .auth import ROLES, AuthUser, require_role

router = APIRouter(prefix="/users", tags=["users"],
                   dependencies=[Depends(require_role("admin"))])
logger = get_logger(__name__)


class UserRow(BaseModel):
    id: int
    email: str
    name: str
    role: Optional[str]
    is_active: bool
    has_password: bool
    okta_linked: bool
    last_login_at: Optional[datetime]
    created_at: datetime


class CreateBody(BaseModel):
    email: str
    name: Optional[str] = None
    role: str


class UpdateBody(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


def _row(u: User) -> UserRow:
    return UserRow(id=u.id, email=u.email, name=u.name, role=u.role,
                   is_active=u.is_active, has_password=u.password_hash is not None,
                   okta_linked=u.okta_sub is not None,
                   last_login_at=u.last_login_at, created_at=u.created_at)


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "detail": detail})


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise _err(400, "INVALID_INPUT", f"role must be one of {', '.join(ROLES)}")


def _active_admins(session) -> int:
    return session.execute(select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True))).scalar_one()


@router.get("", response_model=list[UserRow])
def list_users() -> list[UserRow]:
    with SessionLocal() as session:
        rows = session.execute(select(User).order_by(User.email)).scalars().all()
        return [_row(u) for u in rows]


@router.post("", response_model=UserRow, status_code=201)
def create_user(body: CreateBody) -> UserRow:
    email = normalize_email(body.email)
    if "@" not in email or len(email) > 320:
        raise _err(400, "INVALID_INPUT", "A valid email address is required.")
    _check_role(body.role)
    name = (body.name or "").strip() or email
    with SessionLocal() as session:
        if session.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise _err(409, "USER_EXISTS", "A user with that email already exists.")
        row = User(email=email, name=name[:200], password_hash=None, role=body.role)
        session.add(row)
        session.flush()
        record_event(session, logger, event_type="user.created", entity_type="user",
                     entity_id=row.id,
                     details={"user_id": row.id, "role": row.role, "via": "api"})
        session.commit()
        return _row(row)


@router.patch("/{user_id}", response_model=UserRow)
def update_user(user_id: int, body: UpdateBody,
                me: AuthUser = Depends(require_role("admin"))) -> UserRow:
    with SessionLocal() as session:
        row = session.get(User, user_id)
        if row is None:
            raise _err(404, "USER_NOT_FOUND", "No such user.")
        if body.role is not None:
            _check_role(body.role)

        new_role = row.role if body.role is None else body.role
        new_active = row.is_active if body.is_active is None else body.is_active
        # the last active admin can neither be demoted nor switched off:
        # nobody could then administer anyone
        was_admin = row.role == "admin" and row.is_active
        will_be_admin = new_role == "admin" and new_active
        if was_admin and not will_be_admin and _active_admins(session) <= 1:
            raise _err(409, "LAST_ADMIN", "At least one active admin must remain.")

        changed = []
        if body.name is not None and body.name.strip() and body.name.strip() != row.name:
            row.name = body.name.strip()[:200]
            changed.append("name")
        if new_role != row.role:
            was = row.role
            row.role = new_role
            record_event(session, logger, event_type="user.role_changed",
                         entity_type="user", entity_id=row.id,
                         details={"user_id": row.id, "from": was, "to": new_role})
        if new_active != row.is_active:
            row.is_active = new_active
            record_event(session, logger,
                         event_type="user.activated" if new_active else "user.deactivated",
                         entity_type="user", entity_id=row.id,
                         details={"user_id": row.id, "via": "api"})
        if changed:
            record_event(session, logger, event_type="user.updated",
                         entity_type="user", entity_id=row.id,
                         details={"user_id": row.id, "changed_fields": changed,
                                  "via": "api"})
        session.commit()
        return _row(row)
