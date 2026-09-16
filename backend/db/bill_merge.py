"""Merge the duplicate gold bills an earlier ingest rule created.

Until db/ingest._bill_key, a bill whose bill_number is blank ('-', IREPS's
works-contract convention) never matched an existing bill, so every daily
export that re-reported it stored one more copy — and each copy raised its
own BILL_ONLY exception. Ingest no longer does that; this repairs what is
already stored. A group is: same customer, blank bill_number, same CO6.

Per group one copy SURVIVES:
  * the copy picked by a live (non-rejected) match, so no match moves — a
    group with more than one such copy is skipped and reported, never
    guessed;
  * otherwise the earliest-ingested copy, brought up to date with the
    latest copy's mutable fields (what ingest would have done). A copy in
    a LOCKED match is never mutated, exactly like ingest.

Everything pointing at a dropped copy moves to the survivor (match bills,
run match bills, ingest conflicts, file sightings). The dropped copies'
recovery lines are deleted with their sightings — lines ride with the
first copy only, as ingest does now. The dropped copies' exceptions are
DELETED: those bills never existed, and resolving them would inflate the
Resolved figure. The one exception kept is the earliest dropped OPEN row
when the survivor has no exception and no live match, so a real open bill
keeps its first-seen run. Frozen run frames and silver are not touched.

Never commits: the caller owns the transaction (see scripts/
merge_duplicate_bills.py), so a dry run is a rollback.
"""

import logging
from collections import defaultdict
from typing import Dict, List

from sqlalchemy import delete, func, select, update

from logging_setup import get_logger

from .audit import record_event
from .ingest import BILL_MUTABLE, _norm_key
from .models import (ExceptionLedger, GoldBill, GoldFileRow, GoldRecovery,
                     IngestConflict, MatchLedger, MatchLedgerBill, Run,
                     RunMatchBill)

logger = get_logger(__name__)


def duplicate_groups(session, customer_id: int) -> Dict[str, List[GoldBill]]:
    """{normalised CO6: copies, oldest first} for groups of 2+ copies."""
    groups: Dict[str, List[GoldBill]] = defaultdict(list)
    for b in session.execute(
            select(GoldBill).where(GoldBill.customer_id == customer_id)
            .order_by(GoldBill.bronze_file_id, GoldBill.row_seq)).scalars():
        co6 = _norm_key(b.submission_ref)
        if _norm_key(b.bill_number) is None and co6 is not None:
            groups[co6].append(b)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _live_picks(session, bill_ids) -> Dict[str, set]:
    """{gold_bill_id: {match status}} for picks in non-rejected matches."""
    out: Dict[str, set] = defaultdict(set)
    for bid, status in session.execute(
            select(MatchLedgerBill.gold_bill_id, MatchLedger.status)
            .join(MatchLedger, MatchLedger.id == MatchLedgerBill.match_ledger_id)
            .where(MatchLedgerBill.gold_bill_id.in_(bill_ids),
                   MatchLedgerBill.role == "picked",
                   MatchLedger.status != "REJECTED")):
        out[bid].add(status)
    return out


def _repoint_links(session, model, owner_col, drop_id, keep_id) -> int:
    """Move `model` rows from drop_id to keep_id; where the owner already
    links the survivor, drop the duplicate link instead (a picked role
    wins over candidate). Returns rows touched."""
    touched = 0
    for row in session.execute(
            select(model).where(model.gold_bill_id == drop_id)).scalars().all():
        owner = [getattr(model, c) == getattr(row, c) for c in owner_col]
        twin = session.execute(select(model).where(
            model.gold_bill_id == keep_id, *owner)).scalars().first()
        if twin is None:
            row.gold_bill_id = keep_id
        else:
            if row.role == "picked":
                twin.role = "picked"
            session.delete(row)
        touched += 1
    return touched


def merge_duplicate_bills(session, customer_id: int) -> dict:
    """Merge one customer's duplicate bill groups. Returns a report dict
    (counts + per-group detail); writes nothing it can't roll back."""
    running = session.execute(
        select(func.count()).select_from(Run)
        .where(Run.customer_id == customer_id, Run.status == "running")).scalar()
    if running:
        raise RuntimeError("a reconciliation is running for this customer — retry when it ends")

    groups = duplicate_groups(session, customer_id)
    all_ids = [b.id for copies in groups.values() for b in copies]
    picks = _live_picks(session, all_ids) if all_ids else {}
    stats = defaultdict(int)
    merged: Dict[str, List[str]] = {}
    detail, skipped = [], []

    for co6, copies in groups.items():
        picked = [b for b in copies if b.id in picks]
        if len(picked) > 1:
            skipped.append({"submission_ref": co6, "copies": len(copies),
                            "reason": "more than one copy is in a live match"})
            continue
        keep = picked[0] if picked else copies[0]
        drop = [b for b in copies if b.id != keep.id]
        drop_ids = [b.id for b in drop]
        locked = "LOCKED" in picks.get(keep.id, set())

        # 1. survivor carries the latest export's mutable values
        latest = copies[-1]
        if latest.id != keep.id and not locked:
            for col in BILL_MUTABLE:
                setattr(keep, col, getattr(latest, col))
            stats["survivors_updated"] += 1

        # 2. exceptions: the dropped copies' rows go, except one OPEN row
        #    carried over for a real open bill that has none of its own
        keep_has_exc = session.execute(
            select(func.count()).select_from(ExceptionLedger)
            .where(ExceptionLedger.gold_bill_id == keep.id)).scalar()
        # oldest first by the run that first saw it (NULL first-seen last)
        dropped_exc = session.execute(
            select(ExceptionLedger)
            .outerjoin(Run, Run.id == ExceptionLedger.first_seen_run_id)
            .where(ExceptionLedger.gold_bill_id.in_(drop_ids))
            .order_by(Run.created_at.is_(None), Run.created_at)).scalars().all()
        carried = None
        if not keep_has_exc and keep.id not in picks:
            carried = next((e for e in dropped_exc if e.status == "OPEN"), None)
            if carried is not None:
                carried.gold_bill_id = keep.id
                stats["exceptions_carried"] += 1
        for e in dropped_exc:
            if e is not carried:
                session.delete(e)
                stats["exceptions_deleted"] += 1

        # 3. links move to the survivor
        for d in drop_ids:
            stats["match_bills_repointed"] += _repoint_links(
                session, MatchLedgerBill, ("match_ledger_id",), d, keep.id)
            stats["run_match_bills_repointed"] += _repoint_links(
                session, RunMatchBill, ("run_id", "match_id"), d, keep.id)
        stats["ingest_conflicts_repointed"] += session.execute(
            update(IngestConflict).where(IngestConflict.gold_bill_id.in_(drop_ids))
            .values(gold_bill_id=keep.id)).rowcount
        stats["bill_sightings_repointed"] += session.execute(
            update(GoldFileRow).where(GoldFileRow.frame == "bills",
                                      GoldFileRow.gold_row_id.in_(drop_ids))
            .values(gold_row_id=keep.id)).rowcount

        # 4. recovery lines ride with the first copy only
        rec_ids = list(session.execute(
            select(GoldRecovery.id).where(GoldRecovery.gold_bill_id.in_(drop_ids))).scalars())
        if rec_ids:
            session.execute(delete(GoldFileRow).where(
                GoldFileRow.frame == "recoveries", GoldFileRow.gold_row_id.in_(rec_ids)))
            session.execute(delete(GoldRecovery).where(GoldRecovery.id.in_(rec_ids)))
            stats["recoveries_deleted"] += len(rec_ids)

        # 5. the copies themselves
        session.flush()
        session.execute(delete(GoldBill).where(GoldBill.id.in_(drop_ids)))
        stats["bills_deleted"] += len(drop_ids)
        stats["groups_merged"] += 1
        merged[keep.id] = drop_ids
        detail.append({"submission_ref": co6, "copies": len(copies),
                       "kept": "match" if picked else "earliest",
                       "locked": locked})

    report = {**stats, "groups_found": len(groups), "groups_skipped": len(skipped)}
    if stats["groups_merged"]:
        # ids + counts only (no amounts), per the logging rules
        record_event(session, logger, event_type="gold.bills_merged",
                     level=logging.WARNING if skipped else logging.INFO,
                     customer_id=customer_id, entity_type="gold_bill",
                     details={**report, "merged": merged})
    session.flush()
    return {**report, "detail": detail, "skipped": skipped}
