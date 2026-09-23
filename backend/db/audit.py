"""
The single place a log line and its audit_log row get written together,
so they can never drift apart. record_event() never commits — it only
session.add()s, riding whatever transaction the caller is already about
to commit. If that transaction rolls back, the audit row vanishes along
with the domain write it describes, which is the correct behavior: an
action that didn't actually happen shouldn't have a durable trail either.
"""

import logging
from contextvars import ContextVar
from typing import Optional

from .models import AuditLog

# WHO is acting: the signed-in user's id for the current request, set by
# app/auth.py require_user (an ASYNC dependency, so the value lands in the
# request's own context and flows into the route — and, via anyio's
# context copy, into a sync route's worker thread). None = the system
# itself: startup seeding, the CLI, tests that never sign in. Ambient on
# purpose: every record_event call site and every ledger decision gets
# the actor without each of ~20 call chains threading a user parameter.
current_actor: ContextVar[Optional[int]] = ContextVar("current_actor", default=None)


def set_actor(user_id: Optional[int]) -> None:
    current_actor.set(user_id)


def actor_id() -> Optional[int]:
    return current_actor.get()


_UNSET = object()


def record_event(session, logger: logging.Logger, *, event_type: str,
                 level: int = logging.INFO, customer_id: Optional[int] = None,
                 run_id: Optional[str] = None, entity_type: Optional[str] = None,
                 entity_id=None, details: Optional[dict] = None,
                 actor_user_id=_UNSET) -> None:
    """actor_user_id defaults to the ambient current_actor; pass it only
    where the actor is known before the request context holds it (login)."""
    actor = actor_id() if actor_user_id is _UNSET else actor_user_id
    session.add(AuditLog(
        customer_id=customer_id, run_id=run_id, event_type=event_type,
        severity=logging.getLevelName(level), entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details, actor_user_id=actor))
    # explicit ids win over the ambient ContextVars (ContextFilter only
    # fills attrs the record doesn't already carry) — the ambient context
    # can lag, e.g. a run_id generated inside this very call chain
    extra = {"event_type": event_type, "entity_type": entity_type,
             "entity_id": entity_id, "details": details}
    if customer_id is not None:
        extra["customer_id"] = customer_id
    if run_id is not None:
        extra["run_id"] = run_id
    logger.log(level, event_type, extra=extra)
