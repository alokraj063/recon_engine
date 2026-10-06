"""
Okta sign-in (OIDC authorization-code + PKCE). An ADD-ON to the password
login in app/auth.py: it ends in exactly the same signed session cookie, so
require_user, the audit actor and every api.ts call are untouched.

Okta proves WHO. The app decides WHAT: a person must already have a `users`
row with a role (admins provision by email); nobody is created on first
sign-in. Without a row, an active flag or a role the user gets no session
and the SPA shows "Please contact Admin for access".

Settings (all env, read at request time; Okta is enabled only when the
first three are set — otherwise the app behaves exactly as before):
  OKTA_ISSUER         e.g. https://wabtec.oktapreview.com/oauth2/default
  OKTA_CLIENT_ID
  OKTA_CLIENT_SECRET  a secret: backend/.env locally, Secrets Manager in AWS
  OKTA_REDIRECT_URI   the exact registered callback URL. Set it explicitly:
                      behind the ALB the app would otherwise build an http://
                      URL Okta rejects.
  OKTA_SCOPES         default "openid profile email"

Matching a person to a row (resolve_okta_user): the stable Okta `sub` first;
only an unbound row is matched by email, and that match binds `sub` — so a
later email change in Okta cannot hand the row to someone else.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from db import SessionLocal
from db.audit import record_event
from db.models import User, utcnow
from logging_setup import get_logger
from passwords import normalize_email

from .auth import ROLES, SESSION_KEY

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)

DEFAULT_SCOPES = "openid profile email"


@dataclass(frozen=True)
class OktaSettings:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str


def okta_settings() -> Optional[OktaSettings]:
    """None when Okta is not configured."""
    issuer = os.environ.get("OKTA_ISSUER", "").strip().rstrip("/")
    client_id = os.environ.get("OKTA_CLIENT_ID", "").strip()
    secret = os.environ.get("OKTA_CLIENT_SECRET", "").strip()
    if not (issuer and client_id and secret):
        return None
    return OktaSettings(
        issuer=issuer, client_id=client_id, client_secret=secret,
        redirect_uri=os.environ.get("OKTA_REDIRECT_URI", "").strip(),
        scopes=os.environ.get("OKTA_SCOPES", "").strip() or DEFAULT_SCOPES)


_client = None
_client_key = None


def get_client():
    """The Authlib client, built on first use and rebuilt if the settings
    change. A function so tests can replace it and so importing this module
    never needs the environment."""
    global _client, _client_key
    cfg = okta_settings()
    if cfg is None:
        return None
    key = (cfg.issuer, cfg.client_id, cfg.client_secret, cfg.scopes)
    if _client is None or _client_key != key:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        oauth.register(
            name="okta",
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            server_metadata_url=f"{cfg.issuer}/.well-known/openid-configuration",
            client_kwargs={"scope": cfg.scopes,
                           "code_challenge_method": "S256"},
        )
        _client, _client_key = oauth.okta, key
    return _client


# --- who is this Okta identity ------------------------------------------

def resolve_okta_user(session, sub: Optional[str], email: Optional[str],
                      email_verified=None):
    """-> (row, None) on success or (row_or_None, REASON) on refusal.
    Reasons are codes for the audit row and the SPA; never an address."""
    if not sub:
        return None, "BAD_TOKEN"
    row = session.execute(
        select(User).where(User.okta_sub == sub)).scalar_one_or_none()
    if row is None:
        if not email:
            return None, "NO_EMAIL_CLAIM"
        if email_verified is False:
            return None, "EMAIL_UNVERIFIED"
        row = session.execute(select(User).where(
            User.email == normalize_email(email))).scalar_one_or_none()
        if row is None:
            return None, "NO_SUCH_USER"
        if row.okta_sub and row.okta_sub != sub:
            # the row already belongs to a different Okta identity
            return row, "SUB_MISMATCH"
        row.okta_sub = sub
    if not row.is_active:
        return row, "INACTIVE"
    if row.role not in ROLES:
        return row, "NO_ROLE"
    return row, None


# --- routes ----------------------------------------------------------------

@router.get("/providers")
def providers() -> dict:
    """What the login screen may offer. Public."""
    return {"password": True, "okta": okta_settings() is not None}


def _denied(code: str) -> RedirectResponse:
    # the SPA reads ?auth_error= and says what to do; no session is issued
    return RedirectResponse(url=f"/?auth_error={code}", status_code=302)


@router.get("/okta/login")
async def okta_login(request: Request):
    client = get_client()
    if client is None:
        return _denied("OKTA_NOT_CONFIGURED")
    cfg = okta_settings()
    redirect_uri = cfg.redirect_uri or str(request.url_for("okta_callback"))
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/okta/callback", name="okta_callback")
async def okta_callback(request: Request):
    client = get_client()
    if client is None:
        return _denied("OKTA_NOT_CONFIGURED")

    reason: Optional[str] = None
    claims: dict = {}
    try:
        # checks state, exchanges the code (PKCE) and validates the ID
        # token's signature, issuer, audience, expiry and nonce
        token = await client.authorize_access_token(request)
        claims = dict(token.get("userinfo") or {})
    except Exception as exc:                                 # noqa: BLE001
        # the class name only: messages can echo parts of the token
        reason = "BAD_TOKEN"
        logger.warning("auth.okta_token_rejected", extra={
            "event_type": "auth.okta_token_rejected",
            "details": {"error_type": type(exc).__name__}})

    with SessionLocal() as session:
        row = None
        if reason is None:
            row, reason = resolve_okta_user(
                session, claims.get("sub"), claims.get("email"),
                claims.get("email_verified"))

        if reason is not None:
            record_event(session, logger, event_type="auth.okta_login_failed",
                         level=logging.WARNING, entity_type="user",
                         entity_id=row.id if row is not None else None,
                         details={"reason": reason})
            session.commit()
            request.session.clear()
            return _denied("NO_ACCESS" if reason in ("NO_SUCH_USER", "NO_ROLE")
                           else "INACTIVE" if reason == "INACTIVE" else "FAILED")

        # clear BEFORE issuing, like the password login: nothing from a
        # pre-existing cookie is carried into the new session
        request.session.clear()
        request.session[SESSION_KEY] = row.id
        row.last_login_at = utcnow()
        record_event(session, logger, event_type="auth.okta_login_succeeded",
                     entity_type="user", entity_id=row.id, actor_user_id=row.id,
                     details={"via": "okta"})
        session.commit()
    return RedirectResponse(url="/", status_code=302)
