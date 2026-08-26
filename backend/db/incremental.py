"""
Incremental runs (Phase 6): accumulate + carry forward with a locked
ledger.

Lives in db/ (not recon/) on purpose: the candidate pool is built from
the database, and recon never imports db. The matcher itself is the
UNCHANGED recon matcher — it just receives different frames:

  bank side  = the new statement's credits + credits still OPEN as
               BANK_ONLY in the exception ledger, minus credits already
               consumed by an OPEN/LOCKED match
  bill side  = every bill of the customer not consumed by an OPEN/LOCKED
               match (OPEN keeps a bill claimed until a user rejects it)

New matches land in match_ledger (HIGH confidence auto-LOCKs); exceptions
persist OPEN across runs until a later run matches them (RESOLVED).
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from logging_setup import get_logger, run_id_var
from recon.engine import exception_queue, reconcile
from recon.gold import ensure_schema
from recon.rules import MatchRuleSet

from .audit import record_event
from .base import SessionLocal
from .gold import (BANK_MAP, BILLS_MAP, CRN_MAP, RECOVERIES_MAP, RNOTE_MAP,
                   frame_from_gold)
from .models import (ExceptionLedger, GoldBankTxn, GoldBill, GoldLineageDoc,
                     GoldRecovery, MatchLedger, MatchLedgerBill, Run)

logger = get_logger(__name__)

# In incremental mode the open-bills pool replaces the advice-date window:
# any open advised (or CO7-issued) bill is expected, however old.
WIDE_OPEN_DAYS = 36500


class RunInProgress(Exception):
    """Another incremental run is in flight for this customer."""


class LedgerConflict(Exception):
    """A ledger transition would double-claim a credit or a bill."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def start_run(customer_id: int, params: dict) -> str:
    """Claim the one-running-run-per-customer slot (partial unique index
    on runs(customer_id) WHERE status='running')."""
    run_id = uuid.uuid4().hex
    # bind ambient correlation the moment the id exists; callers invoke
    # this directly (not via run_in_threadpool), so the set propagates to
    # everything else in the same request
    run_id_var.set(run_id)
    with SessionLocal() as session:
        session.add(Run(id=run_id, customer_id=customer_id,
                        status="running", mode="incremental", params=params))
        record_event(session, logger, event_type="run.started",
                     customer_id=customer_id, run_id=run_id,
                     details={"mode": "incremental"})
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            # no run was actually claimed, so nothing durable to anchor
            # an audit row to — a plain log line is the whole story here
            logger.warning("run.start_conflict", extra={
                "event_type": "run.start_conflict",
                "details": {"customer_id": customer_id}})
            raise RunInProgress(
                "an incremental run is already in progress for this customer"
            ) from None
    return run_id


def _consumed(session, customer_id) -> Tuple[set, set]:
    """(bank txn ids, bill ids) consumed by an OPEN or LOCKED match."""
    active = select(MatchLedger.id).where(
        MatchLedger.customer_id == customer_id,
        MatchLedger.status.in_(("OPEN", "LOCKED")))
    txns = set(session.execute(
        select(MatchLedger.gold_bank_txn_id).where(
            MatchLedger.customer_id == customer_id,
            MatchLedger.status.in_(("OPEN", "LOCKED")))).scalars())
    bills = set(session.execute(
        select(MatchLedgerBill.gold_bill_id)
        .where(MatchLedgerBill.match_ledger_id.in_(active),
               MatchLedgerBill.role == "picked")).scalars())
    return txns, bills


def _open_exceptions(session, customer_id, exception_type) -> Dict[str, str]:
    """gold id -> exception_ledger id for OPEN rows of one type."""
    col = (ExceptionLedger.gold_bank_txn_id if exception_type == "BANK_ONLY"
           else ExceptionLedger.gold_bill_id)
    return {gid: eid for eid, gid in session.execute(
        select(ExceptionLedger.id, col)
        .where(ExceptionLedger.customer_id == customer_id,
               ExceptionLedger.exception_type == exception_type,
               ExceptionLedger.status == "OPEN"))
        if gid is not None}


def build_pool(session, customer_id: int, statement_bronze_id: int) -> dict:
    """Engine-shaped frames + positional gold-id lists for the matcher."""
    consumed_txns, consumed_bills = _consumed(session, customer_id)
    open_bank_only = _open_exceptions(session, customer_id, "BANK_ONLY")

    txn_rows = list(session.execute(
        select(GoldBankTxn)
        .where(GoldBankTxn.bronze_file_id == statement_bronze_id,
               GoldBankTxn.used_in_recon.is_(True))
        .order_by(GoldBankTxn.row_seq)).scalars())
    seen = {r.id for r in txn_rows}
    carried = [r for r in session.execute(
        select(GoldBankTxn)
        .where(GoldBankTxn.id.in_(list(open_bank_only)))).scalars()
        if r.id not in seen] if open_bank_only else []
    txn_rows = [r for r in txn_rows + carried if r.id not in consumed_txns]
    bank_df, bank_ids = frame_from_gold(txn_rows, BANK_MAP, "bank_txns",
                                        ensure=ensure_schema)
    if "used_in_recon" in bank_df.columns:
        bank_df = bank_df.drop(columns=["used_in_recon"])

    bill_rows = [r for r in session.execute(
        select(GoldBill).where(GoldBill.customer_id == customer_id)
        .order_by(GoldBill.bronze_file_id, GoldBill.row_seq)).scalars()
        if r.id not in consumed_bills]
    bills_df, bill_ids = frame_from_gold(bill_rows, BILLS_MAP, "bills",
                                         ensure=ensure_schema)

    def lineage(doc_type, colmap, frame_name):
        rows = list(session.execute(
            select(GoldLineageDoc)
            .where(GoldLineageDoc.customer_id == customer_id,
                   GoldLineageDoc.doc_type == doc_type)).scalars())
        if not rows:
            return None
        df, _ = frame_from_gold(rows, colmap, frame_name)
        return df

    rnote_df = lineage("RNOTE", RNOTE_MAP, "lineage_rnote")
    crn_df = lineage("CRN", CRN_MAP, "lineage_crn")

    pool_bill_ids = set(bill_ids)
    rec_rows = [r for r in session.execute(
        select(GoldRecovery).where(GoldRecovery.customer_id == customer_id)
        .order_by(GoldRecovery.bronze_file_id, GoldRecovery.row_seq)).scalars()
        if r.gold_bill_id in pool_bill_ids]
    recoveries_df, _ = frame_from_gold(rec_rows, RECOVERIES_MAP, "recoveries",
                                       ensure=ensure_schema)

    return {"bank_df": bank_df, "bank_ids": bank_ids,
            "bills_df": bills_df, "bill_ids": bill_ids,
            "rnote_df": rnote_df, "crn_df": crn_df,
            "recoveries_df": recoveries_df}


def run_matching(session, customer_id: int, statement_bronze_id: int,
                 rules: MatchRuleSet) -> Tuple[dict, List[str], List[str]]:
    """Reconcile the pool through the unchanged engine. Returns
    (out frames dict, bank_ids, bill_ids) — ids positional like the
    matcher's indices."""
    pool = build_pool(session, customer_id, statement_bronze_id)
    out = reconcile(
        pool["bank_df"], pool["bills_df"], pool["rnote_df"], pool["crn_df"],
        window_days=WIDE_OPEN_DAYS,          # pool replaces the window
        co7_lookback_days=WIDE_OPEN_DAYS,
        date_tolerance_days=rules.date_tolerance_days,
        amount_tolerance=rules.amount_tolerance,
        allow_batched=rules.allow_batched,
        max_batch_size=rules.max_batch_size,
        paid_statuses=rules.paid_statuses,
        weights=rules.weights,
        # CRITICAL: this call site bypasses run_pipeline — the golden
        # gate cannot catch a missing field_map here (two-step UI path)
        field_map=rules.field_map,
    )
    out["bank"] = pool["bank_df"]
    out["bank_all"] = pool["bank_df"]
    out["bills"] = pool["bills_df"]
    out["recoveries"] = pool["recoveries_df"]
    out["queue"] = exception_queue(out)
    return out, pool["bank_ids"], pool["bill_ids"]


def _matched_txn_ids(out, bank_ids) -> Dict[int, str]:
    """matched-frame row position -> gold bank txn id.

    The matcher does not carry pool positions into MatchResult, so map
    back via (bank_ref, amount, value_date) with consumption — unique in
    practice (bank refs are per-transaction)."""
    unmatched_pos = set(out["bank_only"].index) if not out["bank_only"].empty else set()
    available: Dict[tuple, list] = {}
    for pos, txn_id in enumerate(bank_ids):
        if pos in unmatched_pos:
            continue
        row = out["bank"].loc[pos]
        key = (row.get("bank_ref"), round(float(row["amount"]), 2),
               pd.Timestamp(row["value_date"]).date()
               if pd.notna(row.get("value_date")) else None)
        available.setdefault(key, []).append(txn_id)

    result = {}
    if out["matched"].empty:
        return result
    for i, r in enumerate(out["matched"].itertuples()):
        key = (r.bank_ref, round(float(r.amount), 2),
               pd.Timestamp(r.value_date).date()
               if pd.notna(r.value_date) else None)
        ids = available.get(key)
        if ids:
            result[i] = ids.pop(0)
    return result


def finalize_ledger(session, customer_id: int, run_id: str, out,
                    bank_ids: List[str], bill_ids: List[str]
                    ) -> Tuple[dict, List[dict], Dict[str, str]]:
    """Write match_ledger / exception_ledger updates. Returns
    (ledger stats, run_match_bills links, {match_id: ledger row id})."""
    now = utcnow()
    stats = {"matches_created": 0, "auto_locked": 0,
             "exceptions_opened": 0, "exceptions_resolved": 0}
    links: List[dict] = []
    ledger_ids: Dict[str, str] = {}

    txn_by_match_row = _matched_txn_ids(out, bank_ids)
    matched_bill_ids, matched_txn_ids = set(), set()

    if not out["matched"].empty:
        for i, r in enumerate(out["matched"].itertuples()):
            gold_txn = txn_by_match_row.get(i)
            if gold_txn is None:
                continue
            matched_txn_ids.add(gold_txn)
            status = "LOCKED" if r.confidence == "HIGH" else "OPEN"
            ledger = MatchLedger(
                customer_id=customer_id, run_id=run_id, match_id=r.match_id,
                gold_bank_txn_id=gold_txn, confidence=r.confidence,
                status=status,
                locked_at=now if status == "LOCKED" else None,
                locked_by="AUTO_HIGH" if status == "LOCKED" else None,
            )
            session.add(ledger)
            session.flush()
            ledger_ids[r.match_id] = ledger.id
            picked = {int(x) for x in r.bill_indices}
            for pos in picked:
                matched_bill_ids.add(bill_ids[pos])
                session.add(MatchLedgerBill(match_ledger_id=ledger.id,
                                            gold_bill_id=bill_ids[pos],
                                            role="picked"))
                links.append({"match_id": r.match_id,
                              "gold_bill_id": bill_ids[pos], "role": "picked"})
            for pos in {int(x) for x in r.candidate_indices} - picked:
                session.add(MatchLedgerBill(match_ledger_id=ledger.id,
                                            gold_bill_id=bill_ids[pos],
                                            role="candidate"))
                links.append({"match_id": r.match_id,
                              "gold_bill_id": bill_ids[pos],
                              "role": "candidate"})
            stats["matches_created"] += 1
            if status == "LOCKED":
                stats["auto_locked"] += 1

    # resolve OPEN exceptions that this run's matches settled
    for exc_type, matched_ids in (("BANK_ONLY", matched_txn_ids),
                                  ("BILL_ONLY", matched_bill_ids)):
        for gid, eid in _open_exceptions(session, customer_id, exc_type).items():
            if gid in matched_ids:
                row = session.get(ExceptionLedger, eid)
                row.status = "RESOLVED"
                row.resolved_by_run_id = run_id
                row.resolved_at = now
                stats["exceptions_resolved"] += 1

    # append new OPEN exceptions (dedup against still-open rows)
    open_bank = _open_exceptions(session, customer_id, "BANK_ONLY")
    if not out["bank_only"].empty:
        for pos in out["bank_only"].index:
            gid = bank_ids[int(pos)]
            if gid not in open_bank:
                session.add(ExceptionLedger(
                    customer_id=customer_id, exception_type="BANK_ONLY",
                    gold_bank_txn_id=gid, first_seen_run_id=run_id))
                stats["exceptions_opened"] += 1
    open_bill = _open_exceptions(session, customer_id, "BILL_ONLY")
    if not out["bill_only"].empty:
        for pos in out["bill_only"].index:
            gid = bill_ids[int(pos)]
            if gid not in open_bill:
                session.add(ExceptionLedger(
                    customer_id=customer_id, exception_type="BILL_ONLY",
                    gold_bill_id=gid, first_seen_run_id=run_id))
                stats["exceptions_opened"] += 1

    record_event(session, logger, event_type="ledger.finalized",
                 customer_id=customer_id, run_id=run_id, details=stats)
    return stats, links, ledger_ids


def accept_match(match_ledger_id: str,
                 gold_bill_id: Optional[str] = None) -> Optional[dict]:
    """OPEN -> LOCKED by user. Returns the new state or None if unknown.

    gold_bill_id lets the analyst OVERRIDE an ambiguous pick: it must be
    one of the match's recorded bills (picked or candidate); accepting a
    candidate swaps the roles so the chosen bill becomes the settled one.
    Raises ValueError if the bill does not belong to this match.
    """
    with SessionLocal() as session:
        row = session.get(MatchLedger, match_ledger_id)
        if row is None:
            return None
        if row.status == "OPEN":
            overrode = False
            if gold_bill_id is not None:
                links = list(session.execute(
                    select(MatchLedgerBill)
                    .where(MatchLedgerBill.match_ledger_id == row.id)).scalars())
                chosen = next((l for l in links
                               if l.gold_bill_id == gold_bill_id), None)
                if chosen is None:
                    raise ValueError(
                        f"bill {gold_bill_id} is not part of match {row.match_id}")
                if chosen.role != "picked":
                    for l in links:
                        if l.role == "picked":
                            l.role = "candidate"
                    chosen.role = "picked"
                    overrode = True
            row.status = "LOCKED"
            row.locked_by = "USER"
            row.locked_at = utcnow()
            record_event(session, logger, event_type="ledger.match_accepted",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "user_overrode_pick": overrode})
            session.commit()
        return {"id": row.id, "status": row.status, "locked_by": row.locked_by}


def unlock_match(match_ledger_id: str) -> Optional[dict]:
    """LOCKED -> OPEN: reopen the decision (works for USER and AUTO_HIGH
    locks). Links and roles are preserved; pool math is unchanged (OPEN
    matches consume their credit/bills exactly like LOCKED ones), so this
    only returns the match to the review workload."""
    with SessionLocal() as session:
        row = session.get(MatchLedger, match_ledger_id)
        if row is None:
            return None
        if row.status == "LOCKED":
            was = row.locked_by
            row.status = "OPEN"
            row.locked_by = None
            row.locked_at = None
            record_event(session, logger, event_type="ledger.match_unlocked",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "was_locked_by": was})
            session.commit()
        return {"id": row.id, "status": row.status, "locked_by": row.locked_by}


def reopen_match(match_ledger_id: str) -> Optional[dict]:
    """REJECTED -> OPEN: undo a rejection. Unlike unlock_match this DOES
    change pool math — REJECTED is the only status that releases anything —
    so the credit and the picked bills are re-claimed, and the BANK_ONLY
    exception the rejection opened is closed again. Refuses with
    LedgerConflict if a later run already claimed either side.

    The MatchLedgerBill links and their picked/candidate roles were never
    touched by reject, so the pick structure restores itself.
    """
    with SessionLocal() as session:
        row = session.get(MatchLedger, match_ledger_id)
        if row is None:
            return None
        if row.status == "REJECTED":
            picked = set(session.execute(
                select(MatchLedgerBill.gold_bill_id)
                .where(MatchLedgerBill.match_ledger_id == row.id,
                       MatchLedgerBill.role == "picked")).scalars())
            # this row is REJECTED, so it contributes nothing to _consumed
            taken_txns, taken_bills = _consumed(session, row.customer_id)
            clash = picked & taken_bills
            if row.gold_bank_txn_id in taken_txns:
                raise LedgerConflict(
                    f"credit {row.gold_bank_txn_id} has since been claimed by "
                    f"another match; reopening {row.match_id} would "
                    "double-claim it")
            if clash:
                raise LedgerConflict(
                    f"bill(s) {', '.join(sorted(clash))} have since been "
                    f"claimed by another match; reopening {row.match_id} "
                    "would double-claim them")

            row.status = "OPEN"
            row.locked_by = None
            row.locked_at = None
            # the credit is consumed again, so an OPEN BANK_ONLY row for it
            # would be a lie (mirrors finalize_ledger's resolve block)
            eid = _open_exceptions(session, row.customer_id, "BANK_ONLY").get(
                row.gold_bank_txn_id)
            if eid is not None:
                exc = session.get(ExceptionLedger, eid)
                exc.status = "RESOLVED"
                exc.resolved_by_run_id = row.run_id
                exc.resolved_at = utcnow()
            record_event(session, logger, event_type="ledger.match_reopened",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "exception_resolved": eid is not None})
            session.commit()
        return {"id": row.id, "status": row.status, "locked_by": row.locked_by}


def reject_match(match_ledger_id: str) -> Optional[dict]:
    """OPEN -> REJECTED: releases the bills, and puts the credit back in
    the pool as an OPEN BANK_ONLY exception."""
    with SessionLocal() as session:
        row = session.get(MatchLedger, match_ledger_id)
        if row is None:
            return None
        if row.status == "OPEN":
            row.status = "REJECTED"
            if row.gold_bank_txn_id not in _open_exceptions(
                    session, row.customer_id, "BANK_ONLY"):
                session.add(ExceptionLedger(
                    customer_id=row.customer_id, exception_type="BANK_ONLY",
                    gold_bank_txn_id=row.gold_bank_txn_id,
                    first_seen_run_id=row.run_id))
            record_event(session, logger, event_type="ledger.match_rejected",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence})
            session.commit()
        return {"id": row.id, "status": row.status}


def _txn_info(t: Optional[GoldBankTxn]) -> Optional[dict]:
    if t is None:
        return None
    return {"bank_ref": t.bank_ref, "amount": t.amount,
            "value_date": t.value_date.isoformat() if t.value_date else None,
            "zone": t.zone_guess,
            "narrative": (t.narrative or "")[:120]}


def _bill_info(b: Optional[GoldBill]) -> Optional[dict]:
    if b is None:
        return None
    return {"bill_number": b.bill_number,
            "net_payable_amount": b.net_payable_amount,
            "zone": b.zone, "bill_status": b.bill_status}


def ledger_view(customer_id: int) -> dict:
    with SessionLocal() as session:
        match_rows = list(session.execute(
            select(MatchLedger)
            .where(MatchLedger.customer_id == customer_id)
            .order_by(MatchLedger.created_at.desc())).scalars())
        link_rows = list(session.execute(
            select(MatchLedgerBill)
            .where(MatchLedgerBill.match_ledger_id.in_(
                [m.id for m in match_rows]))).scalars()) if match_rows else []
        exc_rows = list(session.execute(
            select(ExceptionLedger)
            .where(ExceptionLedger.customer_id == customer_id)
            .order_by(ExceptionLedger.status)).scalars())

        # bulk-load the human-readable side of every referenced gold row
        txn_ids = ({m.gold_bank_txn_id for m in match_rows}
                   | {e.gold_bank_txn_id for e in exc_rows
                      if e.gold_bank_txn_id})
        bill_ids = ({l.gold_bill_id for l in link_rows}
                    | {e.gold_bill_id for e in exc_rows if e.gold_bill_id})
        txns = {t.id: t for t in session.execute(
            select(GoldBankTxn).where(GoldBankTxn.id.in_(txn_ids))
        ).scalars()} if txn_ids else {}
        bills = {b.id: b for b in session.execute(
            select(GoldBill).where(GoldBill.id.in_(bill_ids))
        ).scalars()} if bill_ids else {}
        links_by_match: Dict[str, list] = {}
        for l in link_rows:
            links_by_match.setdefault(l.match_ledger_id, []).append(l)

        matches = [{
            "id": m.id, "run_id": m.run_id, "match_id": m.match_id,
            "confidence": m.confidence, "status": m.status,
            "locked_by": m.locked_by,
            "created_at": m.created_at.isoformat(),
            "locked_at": m.locked_at.isoformat() if m.locked_at else None,
            "txn": _txn_info(txns.get(m.gold_bank_txn_id)),
            "bills": [{"gold_bill_id": l.gold_bill_id, "role": l.role,
                       **(_bill_info(bills.get(l.gold_bill_id))
                          or {"bill_number": None, "net_payable_amount": None,
                              "zone": None, "bill_status": None})}
                      for l in links_by_match.get(m.id, [])],
        } for m in match_rows]
        exceptions = [{
            "id": e.id, "exception_type": e.exception_type,
            "status": e.status,
            "gold_bank_txn_id": e.gold_bank_txn_id,
            "gold_bill_id": e.gold_bill_id,
            "first_seen_run_id": e.first_seen_run_id,
            "resolved_by_run_id": e.resolved_by_run_id,
            "txn": _txn_info(txns.get(e.gold_bank_txn_id))
            if e.gold_bank_txn_id else None,
            "bill": _bill_info(bills.get(e.gold_bill_id))
            if e.gold_bill_id else None,
        } for e in exc_rows]
    return {"matches": matches, "exceptions": exceptions}
