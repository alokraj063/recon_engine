"""
Command Center overview + AR reconciliation + audit feed: cheap read-only
aggregates for one customer, all via SQL — no gold frames are loaded.
Read-only (no audit events).
"""

from datetime import date

from sqlalchemy import func, select

from .models import (AuditLog, BronzeFile, ExceptionLedger, GoldBankTxn,
                     GoldBill, GoldLineageDoc, GoldRecovery, MatchLedger,
                     MatchLedgerBill, Run)


def _audit_entity_context(session, rows) -> dict:
    """Read-time display enrichment for audit entities, batched per type
    (never N+1). Storage stays minimal per the logging taxonomy; joining
    the human-facing identifiers (M-{seq}, filenames — ids, no amounts)
    for DISPLAY is the same contract /api/ledger already serves."""
    by_type: dict = {}
    for r in rows:
        if r.entity_type and r.entity_id is not None:
            by_type.setdefault(r.entity_type, set()).add(str(r.entity_id))
    out: dict = {}

    match_ids = by_type.get("match_ledger", set())
    if match_ids:
        matches = session.execute(
            select(MatchLedger).where(MatchLedger.id.in_(match_ids))).scalars()
        picked = dict(session.execute(
            select(MatchLedgerBill.match_ledger_id, GoldBill.bill_number)
            .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
            .where(MatchLedgerBill.match_ledger_id.in_(match_ids),
                   MatchLedgerBill.role == "picked")).all())
        for m in matches:
            label = f"M-{m.seq}" if m.seq is not None else m.id[:8]
            out[("match_ledger", m.id)] = {
                "label": label,
                "context": {"match": label, "status": m.status,
                            "confidence": m.confidence,
                            "bill_number": picked.get(m.id)},
            }

    file_ids = by_type.get("bronze_file", set())
    if file_ids:
        ids = [int(i) for i in file_ids if str(i).isdigit()]
        for b in session.execute(
                select(BronzeFile).where(BronzeFile.id.in_(ids))).scalars():
            out[("bronze_file", str(b.id))] = {
                "label": b.original_name,
                "context": {"file": b.original_name,
                            "source_type": b.source_type},
            }

    for rid in by_type.get("match_rule_set", set()):
        out[("match_rule_set", rid)] = {
            "label": f"rule set #{rid}", "context": None,
        }
    return out


def audit_events(session, customer_pk: int, limit: int = 500) -> list:
    """Newest-first audit_log rows for one customer — the Audit trail
    view's feed. details are safe to serve verbatim: the logging taxonomy
    keeps them to counts/ids/field NAMES, never row content. Each event
    additionally carries a display `entity_label`/`context` resolved at
    read time (see _audit_entity_context)."""
    rows = list(session.execute(
        select(AuditLog)
        .where(AuditLog.customer_id == customer_pk)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)).scalars())
    enrich = _audit_entity_context(session, rows)
    out = []
    for r in rows:
        e = enrich.get((r.entity_type, str(r.entity_id))) if r.entity_type else None
        out.append({
            "id": r.id,
            "event_type": r.event_type,
            "severity": r.severity,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "entity_label": e["label"] if e else None,
            "context": e["context"] if e else None,
            "run_id": r.run_id,
            "details": r.details,
            "created_at": r.created_at.isoformat(),
        })
    return out


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


# --- AR reconciliation ---------------------------------------------------

OVERDUE_DAYS = 30

_AR_STATUS_ORDER = {"OVERDUE": 0, "AWAITING": 1, "IN_REVIEW": 2, "SETTLED": 3}


def _due_date(bill: GoldBill):
    """Economic due date: the day the money was advised to the bank,
    falling back to the payment order, then the CO6 submission."""
    return (bill.payment_advice_date or bill.payment_order_date
            or bill.submission_date)


def ar_view(session, customer_pk: int) -> dict:
    """Bill-centric receivables working set: every bill that is settled
    by the ledger, under review, or still owed (open BILL_ONLY) — plus
    KPIs and an aging analysis. Historic bills outside the recon working
    set are deliberately excluded; every row here is actionable."""
    today = date.today()
    rows: list = []

    # --- settled / in-review: picked bills of non-REJECTED matches ------
    ledger_rows = list(session.execute(
        select(MatchLedger, MatchLedgerBill, GoldBill, GoldBankTxn)
        .join(MatchLedgerBill, MatchLedgerBill.match_ledger_id == MatchLedger.id)
        .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
        .join(GoldBankTxn, GoldBankTxn.id == MatchLedger.gold_bank_txn_id)
        .where(MatchLedger.customer_id == customer_pk,
               MatchLedger.status != "REJECTED",
               MatchLedgerBill.role == "picked")))
    picked_per_match: dict = {}
    for m, _l, _b, _t in ledger_rows:
        picked_per_match[m.id] = picked_per_match.get(m.id, 0) + 1
    received_txns: dict = {}
    for m, _link, bill, txn in ledger_rows:
        received_txns[txn.id] = txn
        net = bill.net_payable_amount
        # a batched credit covers several bills — per-bill variance would
        # mislead, so it is only computed for 1:1 matches
        variance = (round((txn.amount or 0) - (net or 0), 2)
                    if picked_per_match[m.id] == 1 and txn.amount is not None
                    and net is not None else None)
        due = _due_date(bill)
        rows.append({
            "bill_number": bill.bill_number,
            "zone": bill.zone,
            "org_unit": bill.org_unit,
            "bill_status": bill.bill_status,
            "gross_amount": bill.gross_amount,
            "net_payable_amount": net,
            "due_date": due.isoformat() if due else None,
            "age_days": None,
            "status": "SETTLED" if m.status == "LOCKED" else "IN_REVIEW",
            "pay": {"bank_ref": txn.bank_ref, "amount": txn.amount,
                    "value_date": (txn.value_date.isoformat()
                                   if txn.value_date else None)},
            "variance": variance,
            "match_ledger_id": m.id,
            "match_seq": m.seq,
            "exception_id": None,
        })

    # --- outstanding: open BILL_ONLY exceptions -------------------------
    for exc, bill in session.execute(
            select(ExceptionLedger, GoldBill)
            .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BILL_ONLY")):
        due = _due_date(bill)
        age = (today - due).days if due else None
        rows.append({
            "bill_number": bill.bill_number,
            "zone": bill.zone,
            "org_unit": bill.org_unit,
            "bill_status": bill.bill_status,
            "gross_amount": bill.gross_amount,
            "net_payable_amount": bill.net_payable_amount,
            "due_date": due.isoformat() if due else None,
            "age_days": age,
            "status": ("OVERDUE" if age is not None and age > OVERDUE_DAYS
                       else "AWAITING"),
            "pay": None,
            "variance": (-bill.net_payable_amount
                         if bill.net_payable_amount is not None else None),
            "match_ledger_id": None,
            "match_seq": None,
            "exception_id": exc.id,
        })

    rows.sort(key=lambda r: (_AR_STATUS_ORDER[r["status"]],
                             -(abs(r["net_payable_amount"] or 0))))

    # --- KPIs + aging ---------------------------------------------------
    open_rows = [r for r in rows if r["status"] in ("AWAITING", "OVERDUE")]
    overdue_rows = [r for r in rows if r["status"] == "OVERDUE"]
    received_value = sum(t.amount or 0 for t in received_txns.values())
    mtd_value = sum(
        t.amount or 0 for t in received_txns.values()
        if t.value_date and t.value_date.year == today.year
        and t.value_date.month == today.month)
    credits = _count(session, GoldBankTxn,
                     GoldBankTxn.customer_id == customer_pk,
                     GoldBankTxn.used_in_recon.is_(True))
    match_rate = (len(received_txns) / credits) if credits else None

    buckets = [("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90),
               ("90+", 91, None)]
    aging = []
    for label, lo, hi in buckets:
        hit = [r for r in open_rows
               if r["age_days"] is not None and r["age_days"] >= lo
               and (hi is None or r["age_days"] <= hi)]
        aging.append({"bucket": label, "count": len(hit),
                      "value": sum(r["net_payable_amount"] or 0 for r in hit)})
    undated = [r for r in open_rows if r["age_days"] is None]
    aging.append({"bucket": "undated", "count": len(undated),
                  "value": sum(r["net_payable_amount"] or 0 for r in undated)})

    return {
        "as_of": today.isoformat(),
        "kpis": {
            "outstanding": {"count": len(open_rows),
                            "value": sum(r["net_payable_amount"] or 0
                                         for r in open_rows)},
            "received": {"count": len(received_txns), "value": received_value,
                         "mtd_value": mtd_value},
            "match_rate": match_rate,
            "overdue": {"count": len(overdue_rows),
                        "value": sum(r["net_payable_amount"] or 0
                                     for r in overdue_rows)},
        },
        "aging": aging,
        "rows": rows,
    }
