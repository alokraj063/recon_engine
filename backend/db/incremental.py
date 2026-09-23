"""
Incremental runs (Phase 6): accumulate + carry forward with a locked
ledger.

Lives in db/ (not recon/) on purpose: the candidate pool is built from
the database, and recon never imports db. The matcher itself is the
UNCHANGED recon matcher — it just receives different frames:

  bank side  = the new statement's credits + credits still OPEN as
               BANK_ONLY in the exception ledger, minus credits already
               consumed by an OPEN/LOCKED match and minus approved
               non-IREPS receipts; the non-IREPS ones left (no match
               signal, not marked IREPS by an analyst) ride along but are
               never offered to the matcher (reconcile(unmatchable=...))
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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from logging_setup import get_logger, run_id_var
from recon.engine import exception_queue, reconcile
from recon.gold import ensure_schema
from recon.matching.scoring import norm_text
from recon.rules import FieldMapping, MatchRuleSet

from .audit import actor_id, record_event
from .base import SessionLocal
from .gold import (BANK_MAP, BILLS_MAP, RECOVERIES_MAP, frame_from_gold,
                   lineage_frame, reported_by_file)
from .reconcile_gold import statement_ids
from .models import (CreditSource, ExceptionLedger, GoldBankTxn, GoldBill,
                     GoldRecovery, MatchLedger, MatchLedgerBill, Run, User)

logger = get_logger(__name__)

# In incremental mode the open-bills pool replaces the advice-date window:
# any open advised (or CO7-issued) bill is expected, however old.
WIDE_OPEN_DAYS = 36500


class RunInProgress(Exception):
    """Another incremental run is in flight for this customer."""


class LedgerConflict(Exception):
    """A ledger transition would double-claim a credit or a bill."""


class LedgerConsumed(Exception):
    """A manual match names a credit or bill already claimed by a
    non-REJECTED match (409 ALREADY_CONSUMED, naming the blocking M-{seq})."""



# Confidence code of a match an analyst created by hand (item 3.2).
# FROZEN like every other confidence label: it lives in ledger rows, API
# payloads and the frontend's stamp CSS. Not a REVIEW confidence — a
# manual match is born LOCKED by USER, never awaiting review.
MANUAL_CONFIDENCE = "MANUAL"

# An analyst's decision on a credit's source (db/models.CreditSource) and
# the resolved_by an approved non-IREPS receipt carries. FROZEN: stored.
IREPS = "IREPS"
NON_IREPS = "NON_IREPS"
RESOLVED_NON_IREPS = "USER_NON_IREPS"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# the optional note an analyst leaves with a decision (accept / reject /
# unlock / reopen, a non-IREPS approve / reject, a manual match)
NOTE_MAX = 500


def clean_note(note: Optional[str]) -> Optional[str]:
    """Blank -> None; over NOTE_MAX -> ValueError (400 INVALID_INPUT)."""
    if note is None:
        return None
    note = note.strip() or None
    if note and len(note) > NOTE_MAX:
        raise ValueError(f"note is longer than {NOTE_MAX} characters")
    return note


def _stamp_decision(row: MatchLedger, note: Optional[str],
                    when: Optional[datetime] = None) -> None:
    """Record WHO made the latest decision on a match (the ambient signed-
    in user, db/audit.current_actor), when, and the note they left — a
    new decision replaces the previous note; the audit trail keeps each."""
    row.decided_by_user_id = actor_id()
    row.decided_at = when or utcnow()
    row.decision_note = note


def _clear_resolution(exc: ExceptionLedger) -> None:
    exc.status = "OPEN"
    exc.resolved_by = None
    exc.resolved_by_run_id = None
    exc.resolved_by_match_id = None
    exc.resolved_by_user_id = None
    exc.resolved_at = None


def _resolve_by_user(exc: ExceptionLedger, resolved_by: str, when: datetime,
                     match_id: Optional[str] = None) -> None:
    exc.status = "RESOLVED"
    exc.resolved_by = resolved_by
    exc.resolved_by_run_id = None
    exc.resolved_by_match_id = match_id
    exc.resolved_by_user_id = actor_id()
    exc.resolved_at = when


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
        try:
            # the runs row must reach the DB before the audit row that
            # references it: there is no relationship() to order the flush,
            # and Postgres (unlike SQLite) enforces the FK. Flushing here
            # also keeps the IntegrityError below meaning ONLY the partial
            # unique index, not an unrelated FK failure misread as a conflict
            session.flush()
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
        record_event(session, logger, event_type="run.started",
                     customer_id=customer_id, run_id=run_id,
                     details={"mode": "incremental"})
        session.commit()
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


def _picked_bill_ids(session, match_ledger_id: str) -> set:
    return set(session.execute(
        select(MatchLedgerBill.gold_bill_id)
        .where(MatchLedgerBill.match_ledger_id == match_ledger_id,
               MatchLedgerBill.role == "picked")).scalars())


def _resolve_exceptions_for_match(session, row: MatchLedger, resolved_by: str,
                                  now: Optional[datetime] = None) -> int:
    """A match that now claims its credit and picked bills makes any OPEN
    BANK_ONLY / BILL_ONLY row for them a lie — close them, recording HOW
    (USER_ACCEPT | USER_MANUAL | USER_REOPEN) and by WHICH match. Run-time
    resolution (finalize_ledger) stamps RUN + resolved_by_run_id instead.
    Returns how many rows it closed."""
    now = now or utcnow()
    n = 0
    targets = (("BANK_ONLY", {row.gold_bank_txn_id}),
               ("BILL_ONLY", _picked_bill_ids(session, row.id)))
    for exc_type, gids in targets:
        open_rows = _open_exceptions(session, row.customer_id, exc_type)
        for gid in gids:
            eid = open_rows.get(gid)
            if eid is None:
                continue
            _resolve_by_user(session.get(ExceptionLedger, eid),
                             resolved_by, now, row.id)
            n += 1
    return n


def _reopen_exceptions_for_match(session, row: MatchLedger,
                                 bills: bool) -> int:
    """The opposite: a match that no longer claims its sides puts them
    back as OPEN exceptions (deduped against still-open rows). The credit
    always; the picked bills only when asked — a run-created match's
    bills will be re-reported by the next run anyway, a MANUAL match may
    have no run coming. first_seen_run_id is the match's run (a manual
    match has none: the exception's original first-seen row is reused if
    still present, else NULL)."""
    n = 0

    def first_seen(col, gid):
        if row.run_id is not None:
            return row.run_id
        # a MANUAL match has no run: inherit the run that first reported
        # this credit/bill as an exception, if any row ever did
        return session.execute(
            select(ExceptionLedger.first_seen_run_id)
            .where(ExceptionLedger.customer_id == row.customer_id,
                   col == gid, ExceptionLedger.first_seen_run_id.isnot(None))
            .order_by(ExceptionLedger.resolved_at.desc())
            .limit(1)).scalar()

    with session.no_autoflush:
        if row.gold_bank_txn_id not in _open_exceptions(
                session, row.customer_id, "BANK_ONLY"):
            session.add(ExceptionLedger(
                customer_id=row.customer_id, exception_type="BANK_ONLY",
                gold_bank_txn_id=row.gold_bank_txn_id,
                first_seen_run_id=first_seen(ExceptionLedger.gold_bank_txn_id,
                                             row.gold_bank_txn_id)))
            n += 1
        if bills:
            open_bill = _open_exceptions(session, row.customer_id, "BILL_ONLY")
            for gid in _picked_bill_ids(session, row.id):
                if gid not in open_bill:
                    session.add(ExceptionLedger(
                        customer_id=row.customer_id, exception_type="BILL_ONLY",
                        gold_bill_id=gid,
                        first_seen_run_id=first_seen(ExceptionLedger.gold_bill_id, gid)))
                    n += 1
    return n


def _decisions(session, customer_id) -> Dict[str, str]:
    """gold bank txn id -> IREPS | NON_IREPS, for credits an analyst
    classified (the Analyst queue's Non-IREPS receipts tab)."""
    return dict(session.execute(
        select(CreditSource.gold_bank_txn_id, CreditSource.source)
        .where(CreditSource.customer_id == customer_id)).all())


def unmatchable_positions(bank_df, bank_ids, decided, rules: MatchRuleSet) -> set:
    """Pool positions of the non-IREPS receipts: no value in the FIRST
    exact signal's bank field — the very test the matcher uses to tag an
    unmatched credit UNRECOGNISED_RECEIPT — unless an analyst marked the
    credit IREPS. With no exact signal configured nothing can be told
    apart, so nothing is ruled out."""
    signals = (rules.field_map or FieldMapping()).exact_signals
    if not signals or bank_df.empty:
        return set()
    col = signals[0].bank_field
    if col not in bank_df.columns:
        return set()
    return {pos for pos in bank_df.index
            if not norm_text(bank_df.at[pos, col])
            and decided.get(bank_ids[int(pos)]) != IREPS}


def build_pool(session, customer_id: int, statement_bronze_ids) -> dict:
    """Engine-shaped frames + positional gold-id lists for the matcher.
    `statement_bronze_ids`: one id or several — the new credits are the
    union of the statements' credits (each gold row once)."""
    consumed_txns, consumed_bills = _consumed(session, customer_id)
    decided = _decisions(session, customer_id)
    open_bank_only = _open_exceptions(session, customer_id, "BANK_ONLY")

    txn_rows = list(session.execute(
        select(GoldBankTxn)
        .where(reported_by_file(session, GoldBankTxn,
                                statement_ids(statement_bronze_ids),
                                customer_id),
               GoldBankTxn.used_in_recon.is_(True))
        # the statement's credits can span files: one it re-reports is
        # owned by the earlier statement it first arrived on
        .order_by(GoldBankTxn.bronze_file_id, GoldBankTxn.row_seq)).scalars())
    seen = {r.id for r in txn_rows}
    carried = [r for r in session.execute(
        select(GoldBankTxn)
        .where(GoldBankTxn.id.in_(list(open_bank_only)))).scalars()
        if r.id not in seen] if open_bank_only else []
    # an approved non-IREPS receipt is settled as a classification: it
    # never re-enters a pool (nor re-opens as an exception)
    txn_rows = [r for r in txn_rows + carried if r.id not in consumed_txns
                and decided.get(r.id) != NON_IREPS]
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

    lineage_df = lineage_frame(session, customer_id)

    pool_bill_ids = set(bill_ids)
    rec_rows = [r for r in session.execute(
        select(GoldRecovery).where(GoldRecovery.customer_id == customer_id)
        .order_by(GoldRecovery.bronze_file_id, GoldRecovery.row_seq)).scalars()
        if r.gold_bill_id in pool_bill_ids]
    recoveries_df, _ = frame_from_gold(rec_rows, RECOVERIES_MAP, "recoveries",
                                       ensure=ensure_schema)

    return {"bank_df": bank_df, "bank_ids": bank_ids,
            "bills_df": bills_df, "bill_ids": bill_ids,
            "lineage_df": lineage_df, "recoveries_df": recoveries_df,
            "decided": decided}


def coverage_start(session, customer_id: int, pool_bank_df=None):
    """The first day the ledger has bank data for: the earliest value date
    among credits a run has handled (a ledger match or exception) and this
    run's pool. A statement ingested but never reconciled does not count —
    its credits were never offered, so its days are not covered yet.
    None when there is no credit at all."""
    seen = select(GoldBankTxn.value_date).where(
        GoldBankTxn.customer_id == customer_id,
        GoldBankTxn.id.in_(
            select(MatchLedger.gold_bank_txn_id)
            .where(MatchLedger.customer_id == customer_id)
            .union(select(ExceptionLedger.gold_bank_txn_id)
                   .where(ExceptionLedger.customer_id == customer_id,
                          ExceptionLedger.gold_bank_txn_id.isnot(None)))))
    dates = [pd.Timestamp(d) for d in session.execute(
        select(func.min(seen.subquery().c.value_date))).scalars() if d]
    if pool_bank_df is not None and not pool_bank_df.empty \
            and "value_date" in pool_bank_df.columns:
        m = pd.to_datetime(pool_bank_df["value_date"]).min()
        if pd.notna(m):
            dates.append(m)
    return min(dates).normalize() if dates else None


def run_matching(session, customer_id: int, statement_bronze_ids,
                 rules: MatchRuleSet) -> Tuple[dict, List[str], List[str]]:
    """Reconcile the pool through the unchanged engine. Returns
    (out frames dict, bank_ids, bill_ids) — ids positional like the
    matcher's indices."""
    pool = build_pool(session, customer_id, statement_bronze_ids)
    # non-IREPS receipts are kept apart: never scored against a bill, they
    # land in bank_only as UNRECOGNISED_RECEIPT and in the Non-IREPS tab
    unmatchable = unmatchable_positions(pool["bank_df"], pool["bank_ids"],
                                        pool["decided"], rules)
    # matching stays wide open (any open bill, however old); only which
    # bills count as EXPECTED is floored at the bank data's first day
    expected_from = coverage_start(session, customer_id, pool["bank_df"])
    out = reconcile(
        pool["bank_df"], pool["bills_df"], pool["lineage_df"],
        window_days=WIDE_OPEN_DAYS,          # pool replaces the window
        co7_lookback_days=WIDE_OPEN_DAYS,
        date_tolerance_days=rules.date_tolerance_days,
        amount_tolerance=rules.amount_tolerance,
        allow_batched=rules.allow_batched,
        max_batch_size=rules.max_batch_size,
        paid_statuses=rules.paid_statuses,
        weights=rules.weights,
        # CRITICAL: this call site bypasses run_pipeline — the golden
        # gate cannot catch a missing rule knob here (two-step UI path);
        # mirror any new knob in db/reconcile_gold.py too
        field_map=rules.field_map,
        copy_overrides=rules.copy_overrides,
        batch_amount_slack=rules.batch_amount_slack,
        amount_decimals=rules.amount_decimals,
        unmatchable=unmatchable,
        expected_from=expected_from,
    )
    out["expected_from"] = expected_from
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

    # durable per-customer match number ("M-{seq}" in the UI): unlike
    # match_id it never restarts. max+1 is safe under the documented
    # single-worker assumption (same one run-locking relies on).
    next_seq = (session.execute(
        select(func.max(MatchLedger.seq))
        .where(MatchLedger.customer_id == customer_id)).scalar() or 0) + 1

    if not out["matched"].empty:
        for i, r in enumerate(out["matched"].itertuples()):
            gold_txn = txn_by_match_row.get(i)
            if gold_txn is None:
                continue
            matched_txn_ids.add(gold_txn)
            status = "LOCKED" if r.confidence == "HIGH" else "OPEN"
            ledger = MatchLedger(
                customer_id=customer_id, run_id=run_id, match_id=r.match_id,
                seq=next_seq,
                gold_bank_txn_id=gold_txn, confidence=r.confidence,
                status=status,
                locked_at=now if status == "LOCKED" else None,
                locked_by="AUTO_HIGH" if status == "LOCKED" else None,
            )
            next_seq += 1
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
            # in the matcher's order (closest-dated first), not set order,
            # so the ledger lists candidates the way the review cards do
            for pos in [int(x) for x in r.candidate_indices]:
                if pos in picked:
                    continue
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
                row.resolved_by = "RUN"
                row.resolved_by_run_id = run_id
                row.resolved_by_match_id = None
                row.resolved_by_user_id = None
                row.resolved_at = now
                stats["exceptions_resolved"] += 1

    # append new OPEN exceptions (dedup against still-open rows)
    open_bank = _open_exceptions(session, customer_id, "BANK_ONLY")
    if not out["bank_only"].empty:
        gaps = (out["bank_only"]["gap_type"]
                if "gap_type" in out["bank_only"].columns else None)
        for pos in out["bank_only"].index:
            gid = bank_ids[int(pos)]
            gap = (str(gaps.loc[pos]) if gaps is not None
                   and pd.notna(gaps.loc[pos]) else None)
            if gid not in open_bank:
                session.add(ExceptionLedger(
                    customer_id=customer_id, exception_type="BANK_ONLY",
                    gold_bank_txn_id=gid, first_seen_run_id=run_id,
                    gap_type=gap))
                stats["exceptions_opened"] += 1
            elif gap:
                # a carried credit re-reported by this run: keep its gap
                # code current (a zone backfilled since its first sighting
                # turns an unrecognised receipt into a signal-not-found)
                row = session.get(ExceptionLedger, open_bank[gid])
                if row is not None and row.gap_type != gap:
                    row.gap_type = gap
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
                 gold_bill_id: Optional[str] = None,
                 note: Optional[str] = None) -> Optional[dict]:
    """OPEN -> LOCKED by user. Returns the new state or None if unknown.

    gold_bill_id lets the analyst OVERRIDE an ambiguous pick: it must be
    one of the match's recorded bills (picked or candidate); accepting a
    candidate swaps the roles so the chosen bill becomes the settled one.
    Raises ValueError if the bill does not belong to this match.
    """
    note = clean_note(note)
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
            _stamp_decision(row, note, row.locked_at)
            # the credit and the settling bill(s) are now claimed for good:
            # any OPEN exception still carrying them is closed, by this
            # accept (see item 3.1 — the Exception queue must agree with
            # the Matches panel without waiting for the next run)
            resolved = _resolve_exceptions_for_match(session, row, "USER_ACCEPT",
                                                     row.locked_at)
            record_event(session, logger, event_type="ledger.match_accepted",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "user_overrode_pick": overrode,
                                 "exceptions_resolved": resolved,
                                 "note": note})
            session.commit()
        return {"id": row.id, "status": row.status, "locked_by": row.locked_by}


def unlock_match(match_ledger_id: str,
                 note: Optional[str] = None) -> Optional[dict]:
    """LOCKED -> OPEN: reopen the decision (works for USER and AUTO_HIGH
    locks). Links and roles are preserved; pool math is unchanged (OPEN
    matches consume their credit/bills exactly like LOCKED ones), so this
    only returns the match to the review workload."""
    note = clean_note(note)
    with SessionLocal() as session:
        row = session.get(MatchLedger, match_ledger_id)
        if row is None:
            return None
        if row.status == "LOCKED":
            was = row.locked_by
            row.status = "OPEN"
            row.locked_by = None
            row.locked_at = None
            _stamp_decision(row, note)
            record_event(session, logger, event_type="ledger.match_unlocked",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "was_locked_by": was, "note": note})
            session.commit()
        return {"id": row.id, "status": row.status, "locked_by": row.locked_by}


def reopen_match(match_ledger_id: str,
                 note: Optional[str] = None) -> Optional[dict]:
    """REJECTED -> OPEN: undo a rejection. Unlike unlock_match this DOES
    change pool math — REJECTED is the only status that releases anything —
    so the credit and the picked bills are re-claimed, and the BANK_ONLY
    exception the rejection opened is closed again. Refuses with
    LedgerConflict if a later run already claimed either side.

    The MatchLedgerBill links and their picked/candidate roles were never
    touched by reject, so the pick structure restores itself.
    """
    note = clean_note(note)
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
            _stamp_decision(row, note)
            # the credit and bills are consumed again, so OPEN exception
            # rows for them would be a lie (mirrors finalize_ledger's
            # resolve block; the reject re-opened them)
            resolved = _resolve_exceptions_for_match(session, row, "USER_REOPEN")
            record_event(session, logger, event_type="ledger.match_reopened",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "exceptions_resolved": resolved,
                                 "note": note})
            session.commit()
        return {"id": row.id, "status": row.status, "locked_by": row.locked_by}


def reject_match(match_ledger_id: str,
                 note: Optional[str] = None) -> Optional[dict]:
    """OPEN -> REJECTED: releases the bills, and puts the credit back in
    the pool as an OPEN BANK_ONLY exception."""
    note = clean_note(note)
    with SessionLocal() as session:
        row = session.get(MatchLedger, match_ledger_id)
        if row is None:
            return None
        if row.status == "OPEN":
            row.status = "REJECTED"
            _stamp_decision(row, note)
            # a run-created match's bills come back via the next run's
            # pool; a MANUAL match has no run coming, so its bills are
            # re-opened as BILL_ONLY exceptions here
            reopened = _reopen_exceptions_for_match(
                session, row, bills=(row.confidence == MANUAL_CONFIDENCE))
            record_event(session, logger, event_type="ledger.match_rejected",
                        customer_id=row.customer_id, run_id=row.run_id,
                        entity_type="match_ledger", entity_id=row.id,
                        details={"confidence": row.confidence,
                                 "exceptions_reopened": reopened,
                                 "note": note})
            session.commit()
        return {"id": row.id, "status": row.status}


MANUAL_NOTE_MAX = NOTE_MAX


def create_manual_match(customer_id: int, gold_bank_txn_id: str,
                        gold_bill_ids: List[str],
                        note: Optional[str] = None) -> dict:
    """An analyst pairs an open bank credit with one or more open bills
    by hand (item 3.2). A ledger row created by a USER, not a run:
    run_id NULL, match_id "manual", confidence MANUAL, born LOCKED by
    USER with the next durable seq. No amount tolerance is enforced —
    that is the point — but the variance (credit − Σ net payable) is
    returned so the UI can show it and the workbook can flag it.

    Both sides must be free: the credit not held by any OPEN/LOCKED match
    and none of the bills picked by one, else LedgerConsumed naming the
    blocking match. The credit's OPEN BANK_ONLY row and each bill's OPEN
    BILL_ONLY row are RESOLVED (USER_MANUAL) exactly like an accept.
    Later runs exclude all of it through _consumed with no engine change.
    """
    bill_ids = list(dict.fromkeys(gold_bill_ids))   # dedupe, keep order
    if not bill_ids:
        raise ValueError("a manual match needs at least one bill")
    note = clean_note(note)
    with SessionLocal() as session:
        txn = session.get(GoldBankTxn, gold_bank_txn_id)
        if txn is None or txn.customer_id != customer_id:
            raise ValueError(f"no bank transaction {gold_bank_txn_id} for this customer")
        bills = {b.id: b for b in session.execute(
            select(GoldBill).where(GoldBill.id.in_(bill_ids),
                                   GoldBill.customer_id == customer_id)).scalars()}
        missing = [b for b in bill_ids if b not in bills]
        if missing:
            raise ValueError(f"no bill(s) {', '.join(missing)} for this customer")

        # who already holds either side?
        active = list(session.execute(
            select(MatchLedger).where(
                MatchLedger.customer_id == customer_id,
                MatchLedger.status.in_(("OPEN", "LOCKED")))).scalars())
        holder_by_txn = {m.gold_bank_txn_id: m for m in active}
        if gold_bank_txn_id in holder_by_txn:
            m = holder_by_txn[gold_bank_txn_id]
            raise LedgerConsumed(
                f"credit {txn.bank_ref} is already held by match M-{m.seq}")
        if active:
            held = list(session.execute(
                select(MatchLedgerBill.match_ledger_id, MatchLedgerBill.gold_bill_id)
                .where(MatchLedgerBill.match_ledger_id.in_([m.id for m in active]),
                       MatchLedgerBill.role == "picked",
                       MatchLedgerBill.gold_bill_id.in_(bill_ids))))
            if held:
                seq = {m.id: m.seq for m in active}
                names = ", ".join(
                    f"{bills[b].bill_number or b} (M-{seq[mid]})" for mid, b in held)
                raise LedgerConsumed(f"bill(s) already picked by a match: {names}")

        now = utcnow()
        next_seq = (session.execute(
            select(func.max(MatchLedger.seq))
            .where(MatchLedger.customer_id == customer_id)).scalar() or 0) + 1
        row = MatchLedger(
            customer_id=customer_id, run_id=None, match_id="manual",
            seq=next_seq, gold_bank_txn_id=gold_bank_txn_id,
            confidence=MANUAL_CONFIDENCE, status="LOCKED",
            locked_at=now, locked_by="USER", note=note, created_at=now,
            decided_by_user_id=actor_id(), decided_at=now)
        session.add(row)
        session.flush()
        for b in bill_ids:
            session.add(MatchLedgerBill(match_ledger_id=row.id,
                                        gold_bill_id=b, role="picked"))
        session.flush()
        resolved = _resolve_exceptions_for_match(session, row, "USER_MANUAL", now)
        total = sum((bills[b].net_payable_amount or 0.0) for b in bill_ids)
        variance = round((txn.amount or 0.0) - total, 2)
        record_event(session, logger, event_type="ledger.match_created_manual",
                     customer_id=customer_id, run_id=None,
                     entity_type="match_ledger", entity_id=row.id,
                     details={"bill_count": len(bill_ids), "variance": variance,
                              "exceptions_resolved": resolved,
                              "has_note": bool(note)})
        session.commit()
        return {"id": row.id, "seq": row.seq, "status": row.status,
                "locked_by": row.locked_by, "confidence": row.confidence,
                "variance": variance, "exceptions_resolved": resolved}


class ExceptionNotFound(Exception):
    """No BANK_ONLY exception with that id."""


def _engine_source(exc: Optional[ExceptionLedger], txn: Optional[GoldBankTxn],
                   live_match: bool) -> str:
    """The credit's source as the pages show it with NO analyst decision —
    the reading db/overview.credit_sources serves (restated here because
    overview imports this module): the engine's stored gap code on a
    credit the ledger has seen, IREPS for a credit a live match holds,
    else the zone rule."""
    zoned = txn is not None and bool((txn.zone_guess or "").strip())
    if exc is not None:
        if exc.gap_type is not None:
            return NON_IREPS if exc.gap_type == "UNRECOGNISED_RECEIPT" else IREPS
        return IREPS if zoned else NON_IREPS
    if live_match:
        return IREPS
    return IREPS if zoned else NON_IREPS


def _source_was(row: Optional[CreditSource], exc, txn, live_match: bool) -> dict:
    """{"was": IREPS|NON_IREPS, "was_auto": bool} — what the credit read as
    BEFORE this change. With no decision recorded that is the engine's
    reading (was_auto true); before 2026-09-23 it was logged as null."""
    if row is not None:
        return {"was": row.source, "was_auto": False}
    return {"was": _engine_source(exc, txn, live_match), "was_auto": True}


def decide_credit_source(exception_id: str, decision: str,
                         note: Optional[str] = None) -> dict:
    """The Non-IREPS receipts tab's decisions on a BANK_ONLY exception:

      approve  it IS a non-IREPS receipt: CreditSource NON_IREPS, the
               exception RESOLVED as USER_NON_IREPS; the credit never
               re-enters a matching pool. Still counted as "Other
               receipts", never as a resolved exception.
      reject   it is IREPS money after all: CreditSource IREPS; the
               exception stays OPEN, now in the IREPS queue, and later
               runs offer the credit to the matcher.
      undo     drop the decision; an approved row re-opens.

    approve / reject need an OPEN row, undo a decided one — anything else
    raises LedgerConflict (409); an unknown id ExceptionNotFound (404)."""
    if decision not in ("approve", "reject", "undo"):
        raise ValueError(f"unknown decision {decision!r}")
    note = clean_note(note)
    with SessionLocal() as session:
        exc = session.get(ExceptionLedger, exception_id)
        if exc is None or exc.exception_type != "BANK_ONLY":
            raise ExceptionNotFound(exception_id)
        txn_id = exc.gold_bank_txn_id
        row = session.execute(
            select(CreditSource).where(
                CreditSource.customer_id == exc.customer_id,
                CreditSource.gold_bank_txn_id == txn_id)).scalar_one_or_none()
        now = utcnow()
        was = _source_was(row, exc, session.get(GoldBankTxn, txn_id), False)
        if decision == "undo":
            if row is None:
                raise LedgerConflict("no decision recorded for this credit")
            session.delete(row)
            if exc.status == "RESOLVED" and exc.resolved_by == RESOLVED_NON_IREPS:
                _clear_resolution(exc)
            event, details = "ledger.credit_source_undone", {**was, "note": note}
        else:
            if exc.status != "OPEN":
                raise LedgerConflict("only an OPEN exception can be decided")
            source = NON_IREPS if decision == "approve" else IREPS
            if row is None:
                row = CreditSource(customer_id=exc.customer_id,
                                   gold_bank_txn_id=txn_id)
                session.add(row)
            row.source = source
            row.decided_at = now
            row.decided_by_user_id = actor_id()
            row.note = note
            if source == NON_IREPS:
                _resolve_by_user(exc, RESOLVED_NON_IREPS, now)
            event = ("ledger.non_ireps_approved" if source == NON_IREPS
                     else "ledger.non_ireps_rejected")
            details = {**was, "source": source, "note": note}
        record_event(session, logger, event_type=event,
                     customer_id=exc.customer_id, run_id=None,
                     entity_type="exception_ledger", entity_id=exc.id,
                     details=details)
        session.commit()
        return {"id": exc.id, "status": exc.status,
                "resolved_by": exc.resolved_by,
                "source_decision": None if decision == "undo" else row.source}


class CreditNotFound(Exception):
    """No credit with that id for the customer."""


def set_credit_source(customer_id: int, gold_bank_txn_id: str,
                      source: Optional[str]) -> dict:
    """The Bank Transactions page's Source edit — the SAME decision the
    Analyst queue's Non-IREPS tab records, from the credit's side:

      NON_IREPS  = approve: CreditSource NON_IREPS and the credit's
                   BANK_ONLY exception RESOLVED as USER_NON_IREPS. A
                   credit no run has seen yet gets that resolved row now,
                   so "Other receipts" counts it at once (and build_pool
                   never pools it). Refused while a live match holds it.
      IREPS      = reject: CreditSource IREPS; an approved exception
                   re-opens (it is IREPS work again) and later runs offer
                   the credit to the matcher even with no zone.
      None       = auto: the decision is dropped (an approval re-opens)
                   and the engine's own reading applies again.

    Debits (used_in_recon false) are never reconciled: ValueError."""
    if source not in (IREPS, NON_IREPS, None):
        raise ValueError(f"source must be {IREPS}, {NON_IREPS} or null")
    with SessionLocal() as session:
        txn = session.get(GoldBankTxn, gold_bank_txn_id)
        if txn is None or txn.customer_id != customer_id:
            raise CreditNotFound(gold_bank_txn_id)
        if not txn.used_in_recon:
            raise ValueError("a debit is not reconciled and has no source")
        holder = session.execute(
            select(MatchLedger).where(
                MatchLedger.customer_id == customer_id,
                MatchLedger.gold_bank_txn_id == txn.id,
                MatchLedger.status.in_(("OPEN", "LOCKED")))).scalars().first()
        if source == NON_IREPS:
            if holder is not None:
                raise LedgerConflict(
                    f"credit {txn.bank_ref} is matched by M-{holder.seq}; "
                    "reject that match first")
        row = session.execute(
            select(CreditSource).where(
                CreditSource.customer_id == customer_id,
                CreditSource.gold_bank_txn_id == txn.id)).scalar_one_or_none()
        # the credit's live exception row: the OPEN one, else its approval.
        # A row some run or match resolved is history and never rewritten.
        rows = list(session.execute(
            select(ExceptionLedger).where(
                ExceptionLedger.customer_id == customer_id,
                ExceptionLedger.exception_type == "BANK_ONLY",
                ExceptionLedger.gold_bank_txn_id == txn.id)).scalars())
        exc = (next((e for e in rows if e.status == "OPEN"), None)
               or next((e for e in rows if e.resolved_by == RESOLVED_NON_IREPS), None))
        # any row the ledger holds for the credit carries the engine's code
        was = _source_was(row, exc or (rows[0] if rows else None), txn,
                          holder is not None)
        now = utcnow()

        def reopen_approval():
            if exc is not None and exc.status == "RESOLVED" \
                    and exc.resolved_by == RESOLVED_NON_IREPS:
                _clear_resolution(exc)

        if source is None:
            if row is not None:
                session.delete(row)
            reopen_approval()
        else:
            if row is None:
                row = CreditSource(customer_id=customer_id, gold_bank_txn_id=txn.id)
                session.add(row)
            row.source = source
            row.decided_at = now
            row.decided_by_user_id = actor_id()
            row.note = None
            if source == IREPS:
                reopen_approval()
            else:
                if exc is None:
                    exc = ExceptionLedger(
                        customer_id=customer_id, exception_type="BANK_ONLY",
                        gold_bank_txn_id=txn.id, first_seen_run_id=None,
                        gap_type="UNRECOGNISED_RECEIPT",
                        status="OPEN")   # column default applies only at INSERT
                    session.add(exc)
                if exc.status == "OPEN":
                    _resolve_by_user(exc, RESOLVED_NON_IREPS, now)
        session.flush()
        record_event(session, logger, event_type="ledger.credit_source_set",
                     customer_id=customer_id, run_id=None,
                     entity_type="gold_bank_txn", entity_id=txn.id,
                     details={**was, "source": source, "via": "bank"})
        session.commit()
        return {"gold_bank_txn_id": txn.id, "source_decision": source,
                "exception_id": exc.id if exc is not None else None,
                "exception_status": exc.status if exc is not None else None}


def approve_non_ireps_bulk(customer_id: int, exception_ids: List[str]) -> dict:
    """Approve many non-IREPS receipts in ONE transaction — the Non-IREPS
    tab's "approve this whole group" (125 deposit-interest lines is a
    normal day). Same effect per row as decide_credit_source("approve");
    rows that are not OPEN BANK_ONLY rows of this customer are skipped and
    counted, never half-applied. One audit event carries the count."""
    ids = list(dict.fromkeys(exception_ids))
    if not ids:
        raise ValueError("no exceptions given")
    with SessionLocal() as session:
        rows = list(session.execute(
            select(ExceptionLedger).where(
                ExceptionLedger.id.in_(ids),
                ExceptionLedger.customer_id == customer_id,
                ExceptionLedger.exception_type == "BANK_ONLY")).scalars())
        now = utcnow()
        decided = {r.gold_bank_txn_id: r for r in session.execute(
            select(CreditSource).where(
                CreditSource.customer_id == customer_id,
                CreditSource.gold_bank_txn_id.in_(
                    [r.gold_bank_txn_id for r in rows]))).scalars()}
        approved = 0
        for exc in rows:
            if exc.status != "OPEN":
                continue
            row = decided.get(exc.gold_bank_txn_id)
            if row is None:
                row = CreditSource(customer_id=customer_id,
                                   gold_bank_txn_id=exc.gold_bank_txn_id)
                session.add(row)
            row.source = NON_IREPS
            row.decided_at = now
            row.decided_by_user_id = actor_id()
            _resolve_by_user(exc, RESOLVED_NON_IREPS, now)
            approved += 1
        record_event(session, logger, event_type="ledger.non_ireps_approved",
                     customer_id=customer_id, run_id=None,
                     entity_type="exception_ledger", entity_id=None,
                     details={"approved": approved, "requested": len(ids),
                              "bulk": True})
        session.commit()
        return {"approved": approved, "requested": len(ids),
                "skipped": len(ids) - approved}


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
    # the due-date chain db/overview windows BILL_ONLY rows on (advice ->
    # order -> submission), so the Analyst queue can apply a Command
    # Center date window to the same rows
    due = b.payment_advice_date or b.payment_order_date or b.submission_date
    return {"bill_number": b.bill_number,
            "submission_ref": b.submission_ref,
            "net_payable_amount": b.net_payable_amount,
            "zone": b.zone, "bill_status": b.bill_status,
            "due_date": due.isoformat() if due else None}


def user_names(session, ids) -> Dict[int, str]:
    """users.id -> display name, for the "who decided" fields. An id with
    no users row (a test actor, a removed account) is simply absent."""
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(session.execute(
        select(User.id, User.name).where(User.id.in_(ids))).all())


def ledger_view(customer_id: int) -> dict:
    with SessionLocal() as session:
        match_rows = list(session.execute(
            select(MatchLedger)
            .where(MatchLedger.customer_id == customer_id)
            .order_by(MatchLedger.created_at.desc())).scalars())
        link_rows = list(session.execute(
            select(MatchLedgerBill)
            .where(MatchLedgerBill.match_ledger_id.in_(
                [m.id for m in match_rows]))
            .order_by(MatchLedgerBill.id)).scalars()) if match_rows else []
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
        seq_by_match = {m.id: m.seq for m in match_rows}
        source_rows = {r.gold_bank_txn_id: r for r in session.execute(
            select(CreditSource)
            .where(CreditSource.customer_id == customer_id)).scalars()}
        names = user_names(session,
                           {m.decided_by_user_id for m in match_rows}
                           | {e.resolved_by_user_id for e in exc_rows}
                           | {r.decided_by_user_id for r in source_rows.values()})

        matches = [{
            "id": m.id, "run_id": m.run_id, "match_id": m.match_id,
            "seq": m.seq,
            "confidence": m.confidence, "status": m.status,
            "locked_by": m.locked_by,
            "created_at": m.created_at.isoformat(),
            "locked_at": m.locked_at.isoformat() if m.locked_at else None,
            "note": m.note,
            # the latest user decision: who (None = the system, e.g. an
            # AUTO_HIGH lock, or a decision older than 2026-09-23), when, why
            "decided_by": names.get(m.decided_by_user_id),
            "decided_at": m.decided_at.isoformat() if m.decided_at else None,
            "decision_note": m.decision_note,
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
            "resolved_by": e.resolved_by,
            "resolved_by_match_id": e.resolved_by_match_id,
            "resolved_by_match_seq": seq_by_match.get(e.resolved_by_match_id),
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            "gap_type": e.gap_type,
            "resolved_by_user": names.get(e.resolved_by_user_id),
            # an analyst's IREPS / NON_IREPS decision on the credit, if any
            "source_decision": _src.source if _src else None,
            "source_decided_by": names.get(_src.decided_by_user_id) if _src else None,
            "source_note": _src.note if _src else None,
            "txn": _txn_info(txns.get(e.gold_bank_txn_id))
            if e.gold_bank_txn_id else None,
            "bill": _bill_info(bills.get(e.gold_bill_id))
            if e.gold_bill_id else None,
        } for e in exc_rows
          for _src in [source_rows.get(e.gold_bank_txn_id)
                       if e.gold_bank_txn_id else None]]
    return {"matches": matches, "exceptions": exceptions}
