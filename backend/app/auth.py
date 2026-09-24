"""
The login gate: a signed session cookie plus a `users` table.

WHY A COOKIE AND NOT A BEARER TOKEN. In production one process serves
both the SPA (app/frontend.py mounts the Vite build at '/') and the API
under '/api'; in dev, Vite proxies '/api' to the backend. Either way the
browser is making a SAME-ORIGIN request, and fetch() attaches cookies to
those by itself — so not one of the ~30 call sites in frontend/src/api.ts
had to learn about a token, and the credential is httponly, unreadable by
any script on the page.

The cookie is stateless: Starlette's SessionMiddleware signs its contents
with SESSION_SECRET (itsdangerous), so there is no session table and a
restart or a second container does not log anyone out. The cost is that a
cookie cannot be withdrawn before it expires, so require_user() reloads
the user row on every request and refuses an is_active=false account.
That reload IS the revocation path — keep it.

SCOPE: this authenticates, it does not authorize. Every signed-in user
sees every customer, exactly as before; `customer_id` remains a request
field (a known deferral in CLAUDE.md). Binding a user to a tenant is the
follow-up that turns this into real isolation.

Settings, all optional in dev:
  SESSION_SECRET   signing key. Missing -> a random one per process, with
                   a WARNING: logins then die at every restart, and two
                   containers never agree. Set it in production.
  SESSION_MAX_AGE  cookie lifetime in seconds (default 8h).
  COOKIE_SECURE    truthy -> the cookie is HTTPS-only. Set it behind the
                   ALB; leave it off for plain-http localhost.
"""

import logging
import os
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select

from db import SessionLocal
from db.audit import record_event, set_actor
from db.models import User, utcnow
from logging_setup import get_logger
from passwords import hash_password, normalize_email, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)

SESSION_KEY = "user_id"
SESSION_COOKIE = "recon_session"
DEFAULT_MAX_AGE = 8 * 60 * 60

# Failed-login throttle. Per email, in this process only — which is exact
# under the single-worker uvicorn this app already assumes elsewhere, and
# degrades to "per container" if that ever changes. It protects an
# ACCOUNT from being guessed at; it is not a general-purpose rate limiter
# and does not pretend to stop a spray across many addresses.
LOCKOUT_AFTER = 8
LOCKOUT_SECONDS = 300
_failures: dict = {}
_failures_lock = Lock()

# Compared against when the email is unknown, so a miss costs the same
# bcrypt work as a hit and the response time cannot be used to enumerate
# who has an account. Built on first use — hashing at import would add
# ~100ms to every process start, including alembic's.
_dummy_hash: Optional[str] = None


# --- settings ----------------------------------------------------------

def session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if secret:
        return secret
    logger.warning("auth.ephemeral_session_secret", extra={
        "event_type": "auth.ephemeral_session_secret",
        "details": {"reason": "SESSION_SECRET not set",
                    "effect": "sessions end at every restart and are not "
                              "shared between processes"}})
    return secrets.token_urlsafe(32)


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "").strip().lower() \
        in ("1", "true", "yes", "on")


def session_max_age() -> int:
    raw = os.environ.get("SESSION_MAX_AGE", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_AGE


# --- the signed-in user ------------------------------------------------

@dataclass(frozen=True)
class AuthUser:
    """What a route may know about the caller. A plain value, detached
    from any session, so it can outlive the one require_user() opened."""
    id: int
    email: str
    name: str


def _unauthenticated() -> HTTPException:
    # same shape every route in this app uses: {detail: {error, detail}}
    return HTTPException(status_code=401,
                         detail={"error": "NOT_AUTHENTICATED",
                                 "detail": "Sign in to continue."})


def _load_active_user(user_id: int) -> Optional[AuthUser]:
    with SessionLocal() as session:
        row = session.get(User, user_id)
        if row is None or not row.is_active:
            return None
        return AuthUser(id=row.id, email=row.email, name=row.name)


async def require_user(request: Request) -> AuthUser:
    """The whole gate. Attached ONCE, to the /api router in app/routes.py,
    so every route it carries is protected together and a route added
    later is protected by default rather than by remembering to.

    Public by construction, because they are not on that router:
    /api/health (registered on `app` in main.py, so the ALB health check
    needs no credential) and /api/auth/* (this module's own router).

    ASYNC on purpose: it also names the actor for the audit trail
    (db/audit.set_actor). A sync dependency runs in a worker thread whose
    context changes never reach the route; an async one runs in the
    request's own task, so the actor flows into the route handler — and
    from there, copied by anyio, into a sync route's worker thread. The
    user lookup itself is blocking SQLAlchemy, so it goes to the threadpool.
    """
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        raise _unauthenticated()
    user = await run_in_threadpool(_load_active_user, user_id)
    if user is None:
        # deleted or deactivated since the cookie was issued
        request.session.clear()
        raise _unauthenticated()
    set_actor(user.id)
    return user


# --- throttle ----------------------------------------------------------

def _locked_for(email: str) -> int:
    """Seconds remaining on this account's lockout, 0 if it is open."""
    with _failures_lock:
        count, first_at = _failures.get(email, (0, 0.0))
        if count < LOCKOUT_AFTER:
            return 0
        remaining = int(first_at + LOCKOUT_SECONDS - time.time())
        if remaining <= 0:
            _failures.pop(email, None)
            return 0
        return remaining


def _record_failure(email: str) -> None:
    now = time.time()
    with _failures_lock:
        # drop entries whose window has passed, so this never grows
        for key, (_, started) in list(_failures.items()):
            if started + LOCKOUT_SECONDS < now:
                _failures.pop(key, None)
        count, first_at = _failures.get(email, (0, now))
        if first_at + LOCKOUT_SECONDS < now:
            count, first_at = 0, now
        _failures[email] = (count + 1, first_at)


def _clear_failures(email: str) -> None:
    with _failures_lock:
        _failures.pop(email, None)


def _equal_cost_miss() -> None:
    """Spend the same time on an unknown email as on a real one."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(24))
    verify_password("not-the-password", _dummy_hash)


# --- routes ------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request) -> UserOut:
    """Sign in. One message for every reject reason — an unknown address,
    a wrong password and a deactivated account are indistinguishable from
    outside, so this endpoint never tells a stranger who has an account.
    """
    email = normalize_email(body.email)
    locked = _locked_for(email)
    if locked:
        raise HTTPException(status_code=429, detail={
            "error": "TOO_MANY_ATTEMPTS",
            "detail": f"Too many failed attempts. Try again in "
                      f"{max(1, locked // 60)} minute(s)."})

    with SessionLocal() as session:
        row = session.execute(
            select(User).where(User.email == email)).scalar_one_or_none()
        if row is None:
            _equal_cost_miss()
            reason = "NO_SUCH_USER"
        elif not row.is_active:
            reason = "INACTIVE"
        elif not verify_password(body.password, row.password_hash):
            reason = "BAD_PASSWORD"
        else:
            reason = None

        if reason is not None:
            _record_failure(email)
            # no email, ever: audit details and log lines carry ids and
            # field names only (see the taxonomy in CLAUDE.md)
            record_event(session, logger, event_type="auth.login_failed",
                         level=logging.WARNING, entity_type="user",
                         entity_id=row.id if row is not None else None,
                         details={"reason": reason})
            session.commit()
            raise HTTPException(status_code=401, detail={
                "error": "INVALID_CREDENTIALS",
                "detail": "Email or password is not correct."})

        # clear BEFORE issuing: a pre-existing cookie must not be adopted
        # and carried into the new session (session fixation)
        request.session.clear()
        request.session[SESSION_KEY] = row.id
        row.last_login_at = utcnow()
        _clear_failures(email)
        record_event(session, logger, event_type="auth.login_succeeded",
                     entity_type="user", entity_id=row.id, actor_user_id=row.id)
        session.commit()
        return UserOut(id=row.id, email=row.email, name=row.name)


@router.post("/logout")
def logout(request: Request) -> dict:
    """Always succeeds, signed in or not — a logout that can fail leaves
    the user stuck on a page they cannot leave."""
    user_id = request.session.get(SESSION_KEY)
    request.session.clear()
    if user_id:
        with SessionLocal() as session:
            record_event(session, logger, event_type="auth.logout",
                         entity_type="user", entity_id=user_id,
                         actor_user_id=user_id)
            session.commit()
    return {"status": "signed_out"}


@router.get("/me", response_model=UserOut)
def me(user: AuthUser = Depends(require_user)) -> UserOut:
    """Who am I — the call the SPA's gate makes on load. 401 here is the
    normal, expected answer for someone who has not signed in yet."""
    return UserOut(id=user.id, email=user.email, name=user.name)
