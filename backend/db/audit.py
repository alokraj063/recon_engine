"""
The single place a log line and its audit_log row get written together,
so they can never drift apart. record_event() never commits — it only
session.add()s, riding whatever transaction the caller is already about
to commit. If that transaction rolls back, the audit row vanishes along
with the domain write it describes, which is the correct behavior: an
action that didn't actually happen shouldn't have a durable trail either.
"""

import logging
from typing import Optional

from .models import AuditLog


def record_event(session, logger: logging.Logger, *, event_type: str,
                 level: int = logging.INFO, customer_id: Optional[int] = None,
                 run_id: Optional[str] = None, entity_type: Optional[str] = None,
                 entity_id=None, details: Optional[dict] = None) -> None:
    session.add(AuditLog(
        customer_id=customer_id, run_id=run_id, event_type=event_type,
        severity=logging.getLevelName(level), entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details))
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
