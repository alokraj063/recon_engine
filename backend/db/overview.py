"""
Command Center overview: cheap aggregate counts for one customer, all
via SQL — no gold frames are loaded. Read-only (no audit event).
"""

from sqlalchemy import func, select

from .models import (AuditLog, BronzeFile, ExceptionLedger, GoldBankTxn,
                     GoldBill, GoldLineageDoc, GoldRecovery, MatchLedger, Run)


def audit_events(session, customer_pk: int, limit: int = 500) -> list:
    """Newest-first audit_log rows for one customer — the Audit trail
    view's feed. details are safe to serve verbatim: the logging taxonomy
    keeps them to counts/ids/field NAMES, never row content."""
    rows = session.execute(
        select(AuditLog)
        .where(AuditLog.customer_id == customer_pk)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)).scalars()
    return [{
        "id": r.id,
        "event_type": r.event_type,
        "severity": r.severity,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "run_id": r.run_id,
        "details": r.details,
        "created_at": r.created_at.isoformat(),
    } for r in rows]


def _count(session, model, *where) -> int:
    return session.execute(
        select(func.count()).select_from(model).where(*where)).scalar() or 0


def overview(session, customer_pk: int) -> dict:
    gold = {
        "bank_txns": _count(session, GoldBankTxn,
                            GoldBankTxn.customer_id == customer_pk),
        "credits": _count(session, GoldBankTxn,
                          GoldBankTxn.customer_id == customer_pk,
                          GoldBankTxn.used_in_recon.is_(True)),
        "bills": _count(session, GoldBill, GoldBill.customer_id == customer_pk),
        "recoveries": _count(session, GoldRecovery,
                             GoldRecovery.customer_id == customer_pk),
        "lineage_docs": _count(session, GoldLineageDoc,
                               GoldLineageDoc.customer_id == customer_pk),
    }

    matches = {status: 0 for status in ("OPEN", "LOCKED", "REJECTED")}
    for status, n in session.execute(
            select(MatchLedger.status, func.count())
            .where(MatchLedger.customer_id == customer_pk)
            .group_by(MatchLedger.status)):
        matches[status] = n
    locked_by = {"AUTO_HIGH": 0, "USER": 0}
    for by, n in session.execute(
            select(MatchLedger.locked_by, func.count())
            .where(MatchLedger.customer_id == customer_pk,
                   MatchLedger.status == "LOCKED")
            .group_by(MatchLedger.locked_by)):
        if by in locked_by:
            locked_by[by] = n

    open_exc = {"BANK_ONLY": 0, "BILL_ONLY": 0}
    for etype, n in session.execute(
            select(ExceptionLedger.exception_type, func.count())
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN")
            .group_by(ExceptionLedger.exception_type)):
        open_exc[etype] = n
    resolved = _count(session, ExceptionLedger,
                      ExceptionLedger.customer_id == customer_pk,
                      ExceptionLedger.status == "RESOLVED")

    bank_open_value = session.execute(
        select(func.coalesce(func.sum(GoldBankTxn.amount), 0.0))
        .select_from(ExceptionLedger)
        .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.status == "OPEN",
               ExceptionLedger.exception_type == "BANK_ONLY")).scalar() or 0.0
    bill_open_value = session.execute(
        select(func.coalesce(func.sum(GoldBill.net_payable_amount), 0.0))
        .select_from(ExceptionLedger)
        .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.status == "OPEN",
               ExceptionLedger.exception_type == "BILL_ONLY")).scalar() or 0.0

    matched_credits = session.execute(
        select(func.count(func.distinct(MatchLedger.gold_bank_txn_id)))
        .where(MatchLedger.customer_id == customer_pk,
               MatchLedger.status != "REJECTED")).scalar() or 0
    match_rate = (matched_credits / gold["credits"]) if gold["credits"] else None

    # top open exceptions by absolute value (both sides in one list)
    top: list = []
    for e, t in session.execute(
            select(ExceptionLedger, GoldBankTxn)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BANK_ONLY")):
        top.append({"id": e.id, "exception_type": "BANK_ONLY",
                    "ref": t.bank_ref, "zone": t.zone_guess,
                    "amount": t.amount, "date": str(t.value_date or "")})
    for e, b in session.execute(
            select(ExceptionLedger, GoldBill)
            .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BILL_ONLY")):
        top.append({"id": e.id, "exception_type": "BILL_ONLY",
                    "ref": b.bill_number, "zone": b.zone,
                    "amount": b.net_payable_amount,
                    "date": str(b.payment_advice_date or b.payment_order_date or "")})
    top.sort(key=lambda x: abs(x["amount"] or 0), reverse=True)

    last_run_row = session.execute(
        select(Run).where(Run.customer_id == customer_pk,
                          Run.status == "succeeded")
        .order_by(Run.created_at.desc()).limit(1)).scalar_one_or_none()
    last_run = None
    if last_run_row is not None:
        counts = ((last_run_row.payload or {}).get("meta") or {}).get("counts")
        last_run = {"run_id": last_run_row.id, "mode": last_run_row.mode,
                    "created_at": last_run_row.created_at.isoformat(),
                    "counts": counts}

    last_file = session.execute(
        select(BronzeFile).where(BronzeFile.customer_id == customer_pk)
        .order_by(BronzeFile.uploaded_at.desc()).limit(1)).scalar_one_or_none()
    last_ingestion = ({"at": last_file.uploaded_at.isoformat(),
                       "original_name": last_file.original_name,
                       "source_type": last_file.source_type}
                      if last_file is not None else None)

    return {
        "gold": gold,
        "matches": matches,
        "locked_by": locked_by,
        "open_exceptions": open_exc,
        "resolved_exceptions": resolved,
        "open_value": {"bank_only": bank_open_value,
                       "bill_only": bill_open_value,
                       "total": bank_open_value + bill_open_value},
        "matched_credits": matched_credits,
        "match_rate": match_rate,
        "top_exceptions": top[:8],
        "last_run": last_run,
        "last_ingestion": last_ingestion,
    }
