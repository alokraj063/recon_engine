"""
Idempotent, entity-aware gold ingestion (Phase 6).

Two layers of dedup:
- file level: a bronze file whose gold rows already exist is reused
  outright (same bytes -> same rows, parse skipped by the caller's
  bronze sha256 dedup, insert skipped here);
- entity level: bills/bank txns/lineage docs carry natural keys, because
  two daily IREPS exports are different FILES containing mostly the same
  BILLS. A newer export updates an existing bill's mutable fields in
  place instead of inserting a duplicate.

Consistency guard: a bill consumed by a LOCKED match is never mutated —
the attempted change lands in ingest_conflicts and the gold row stays
exactly as it was when the match locked.
"""

import logging
from datetime import date, datetime
from typing import Dict, Optional, Tuple

import pandas as pd
from sqlalchemy import select

from logging_setup import get_logger

from .audit import record_event
from .gold import (BANK_MAP, BILLS_MAP, LINEAGE_MAP, RECOVERIES_MAP,
                   _coerce, _is_na, _json_clean, _persist_frame)
from .models import (GoldBankTxn, GoldBill, GoldLineageDoc, GoldRecovery,
                     IngestConflict, MatchLedger, MatchLedgerBill)

logger = get_logger(__name__)

# Fields a newer export may legitimately change on an existing bill.
BILL_MUTABLE = ["bill_status", "payment_order_ref", "payment_order_date",
                "payment_advice_date", "gross_amount", "approved_amount",
                "deduction_amount", "net_payable_amount", "recovery_count",
                "recovery_sum", "return_reason", "net_check",
                "recovery_check", "sheet", "header_row", "data_row"]

_BLANK_KEYS = {"", "-", "----", "NAN", "NONE"}


def _norm_key(v) -> Optional[str]:
    """Natural-key normalisation; blank/placeholder values never merge
    (mirrors engine.group_bill_attempts' UNGROUPABLE_KEYS rule).

    Date-like values normalise to the ISO date: a DB Date column reads
    back as datetime.date (str -> '2026-03-18') while parsed frames carry
    Timestamps (str -> '2026-03-18 00:00:00') — raw str() of the two
    never matches, which silently killed the bank-txn entity dedup."""
    if _is_na(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip().upper()
    return s if s and s not in _BLANK_KEYS else None


def _existing_file_rows(session, model, bronze_file_id) -> Dict[int, str]:
    return {seq: rid for rid, seq in session.execute(
        select(model.id, model.row_seq)
        .where(model.bronze_file_id == bronze_file_id))}


def _consumed_bill_ids(session, customer_id, statuses=("LOCKED",)) -> set:
    return set(session.execute(
        select(MatchLedgerBill.gold_bill_id)
        .join(MatchLedger, MatchLedger.id == MatchLedgerBill.match_ledger_id)
        .where(MatchLedger.customer_id == customer_id,
               MatchLedger.status.in_(statuses))
    ).scalars())


def new_stats() -> dict:
    return {"files_reused": 0, "rows_inserted": 0, "bills_updated": 0,
            "rows_reused": 0, "conflicts": 0}


def _ingest_keyed(session, model, df, colmap, base, key_cols_db,
                  key_cols_frame, stats,
                  existing_file_rows: Optional[Dict[int, str]] = None
                  ) -> Dict[int, str]:
    """Generic natural-key ingest: reuse the existing row when the key
    matches, insert otherwise. No mutation of existing rows.

    A row whose natural key is blank/placeholder (_norm_key -> None)
    never matches ANY existing row by design — two such rows from two
    DIFFERENT files really are unrelated and must both land in gold. But
    that same rule made a re-ingest of the identical bronze file
    non-idempotent: replaying it would try to insert that row again,
    hitting the (bronze_file_id, row_seq) uniqueness constraint.
    `existing_file_rows` ({row_seq: id} already owned by THIS bronze
    file) catches that specific case: a blank-keyed row already sitting
    at this row_seq means this exact ingest already ran once, so it's
    reused instead of reinserted."""
    existing_file_rows = existing_file_rows or {}
    existing = {}
    for row in session.execute(
            select(model).where(model.customer_id == base["customer_id"])
    ).scalars():
        key = tuple(_norm_key(getattr(row, c)) for c in key_cols_db)
        if all(k is not None for k in key):
            existing.setdefault(key, row.id)

    ids: Dict[int, str] = {}
    to_insert = []
    for rec in df.to_dict(orient="records"):
        seq = int(rec["row_seq"])
        key = tuple(_norm_key(rec.get(c)) for c in key_cols_frame)
        if all(k is not None for k in key) and key in existing:
            ids[seq] = existing[key]
            stats["rows_reused"] += 1
            continue
        if seq in existing_file_rows:
            ids[seq] = existing_file_rows[seq]
            stats["rows_reused"] += 1
            continue
        to_insert.append(rec)
    if to_insert:
        sub = pd.DataFrame(to_insert)
        inserted = _persist_frame(session, model, sub, colmap, base)
        ids.update(inserted)
        stats["rows_inserted"] += len(inserted)
        for rec in to_insert:
            key = tuple(_norm_key(rec.get(c)) for c in key_cols_frame)
            if all(k is not None for k in key) and key not in existing:
                existing[key] = ids[int(rec["row_seq"])]
    return ids


DEFAULT_BILL_KEY = ("bill_number", "submission_ref")
DEFAULT_BANK_KEY = ("bank_ref", "value_date", "amount")


def _ingest_bills(session, df, base, stats,
                  key_cols=DEFAULT_BILL_KEY,
                  existing_file_rows: Optional[Dict[int, str]] = None
                  ) -> Tuple[Dict[int, str], set]:
    """Bill upsert by the customer's entity key (default (bill_number,
    submission_ref) — an ERP without a CO6-like ref configures its own
    via source_configs.params["entity_key"]); LOCKED bills never mutate.
    Returns ({row_seq: gold_bill_id}, set of newly inserted ids).

    A bill whose entity key is blank/placeholder (e.g. bill_number '-',
    IREPS's works-contract convention) never matches ANY existing bill —
    correct across different files (each is a genuinely distinct, un-
    linkable bill), but that same rule made replaying the SAME bronze
    file non-idempotent: it kept trying to insert that row again and hit
    the (bronze_file_id, row_seq) uniqueness constraint. `existing_file_
    rows` ({row_seq: id} already owned by THIS bronze file) catches that:
    a blank-keyed row already sitting at this row_seq means this exact
    ingest already ran, so it's reused instead of reinserted."""
    customer_id = base["customer_id"]
    existing_file_rows = existing_file_rows or {}
    locked = _consumed_bill_ids(session, customer_id, ("LOCKED",))
    existing = {}
    for row in session.execute(
            select(GoldBill).where(GoldBill.customer_id == customer_id)
    ).scalars():
        key = tuple(_norm_key(getattr(row, c)) for c in key_cols)
        if all(k is not None for k in key):
            existing.setdefault(key, row)

    columns = GoldBill.__table__.columns
    ids: Dict[int, str] = {}
    inserted_ids: set = set()
    to_insert = []
    for rec in df.to_dict(orient="records"):
        seq = int(rec["row_seq"])
        # frame side of the entity identity — MUST track the canonical gold
        # names; a stale name here silently re-inserts every bill on every
        # ingest (rec.get returns None, no error)
        key = tuple(_norm_key(rec.get(c)) for c in key_cols)
        row = existing.get(key) if all(k is not None for k in key) else None
        if row is None:
            if seq in existing_file_rows:
                ids[seq] = existing_file_rows[seq]
                stats["rows_reused"] += 1
                continue
            to_insert.append(rec)
            continue
        ids[seq] = row.id
        # what would change?
        changes = {}
        for db_col in BILL_MUTABLE:
            src = next(s for s, d in BILLS_MAP.items() if d == db_col)
            new_val = _coerce(rec.get(src), columns[db_col].type)
            if new_val != getattr(row, db_col):
                changes[db_col] = {"from": _json_clean(getattr(row, db_col)),
                                   "to": _json_clean(new_val)}
        if not changes:
            stats["rows_reused"] += 1
            continue
        if row.id in locked:
            # flag, never silently overwrite a settled bill
            session.add(IngestConflict(
                customer_id=customer_id, gold_bill_id=row.id,
                bronze_file_id=base["bronze_file_id"],
                changed_fields=changes))
            stats["conflicts"] += 1
            # field names only, never the from/to values (bill amounts) —
            # the full detail already lives in ingest_conflicts.changed_fields
            record_event(session, logger, event_type="gold.ingest_conflict",
                        level=logging.WARNING, customer_id=customer_id,
                        run_id=base.get("run_id"), entity_type="gold_bill",
                        entity_id=row.id,
                        details={"bronze_file_id": base["bronze_file_id"],
                                 "changed_field_names": list(changes.keys())})
            continue
        for db_col, ch in changes.items():
            src = next(s for s, d in BILLS_MAP.items() if d == db_col)
            setattr(row, db_col, _coerce(rec.get(src), columns[db_col].type))
        stats["bills_updated"] += 1

    if to_insert:
        sub = pd.DataFrame(to_insert)
        inserted = _persist_frame(session, GoldBill, sub, BILLS_MAP, base)
        ids.update(inserted)
        inserted_ids = set(inserted.values())
        stats["rows_inserted"] += len(inserted)
    return ids, inserted_ids


def ingest_gold_frames(session, customer_id: int,
                       gold_frames: Dict[str, pd.DataFrame],
                       bronze_ids: Dict[str, int],
                       run_id: Optional[str] = None,
                       entity_keys: Optional[Dict[str, list]] = None
                       ) -> Tuple[Dict[str, Dict[int, str]], dict]:
    """Ingest every gold frame idempotently. Returns
    ({frame: {row_seq: gold id}}, stats). entity_keys overrides the
    natural keys per frame ({"bills": [...], "bank_txns": [...]}) — from
    the customer's source_configs params; None = the defaults."""
    stats = new_stats()
    entity_keys = entity_keys or {}
    bill_key = tuple(entity_keys.get("bills") or DEFAULT_BILL_KEY)
    bank_key = tuple(entity_keys.get("bank_txns") or DEFAULT_BANK_KEY)

    def base(frame):
        return {"customer_id": customer_id, "run_id": run_id,
                "bronze_file_id": bronze_ids[frame]}

    fixed_models = {"bank_txns": GoldBankTxn, "bills": GoldBill,
                    "recoveries": GoldRecovery}
    # any other frame is a lineage slot (canonical unified shape)
    models = {frame: fixed_models.get(frame, GoldLineageDoc)
              for frame in gold_frames}
    ids: Dict[str, Dict[int, str]] = {}
    reused_files = set()
    # {row_seq: id} already owned by this bronze file, per frame — kept
    # even when the whole-file shortcut below doesn't apply, so each
    # _ingest_* can still recognise "this row_seq was already inserted
    # under this exact bronze file" for rows with no usable entity key
    # (see _ingest_bills' docstring: a blank/placeholder key never
    # matches by key, so without this a replayed ingest of the same file
    # tries to insert those rows again and hits the (bronze_file_id,
    # row_seq) uniqueness constraint)
    file_rows: Dict[str, Dict[int, str]] = {}

    # file-level dedup first: same bytes -> reuse every row
    for frame, df in gold_frames.items():
        rows = _existing_file_rows(session, models[frame], bronze_ids[frame])
        file_rows[frame] = rows
        if rows and len(rows) == len(df):
            ids[frame] = rows
            reused_files.add(frame)
    # count FILES, not frames — one bills upload yields two gold frames
    # (bills + recoveries) sharing a bronze file
    stats["files_reused"] += len({bronze_ids[f] for f in reused_files})

    if "bank_txns" in gold_frames and "bank_txns" not in reused_files:
        ids["bank_txns"] = _ingest_keyed(
            session, GoldBankTxn, gold_frames["bank_txns"], BANK_MAP,
            base("bank_txns"),
            # frame names -> DB names (only "timestamp" differs)
            key_cols_db=tuple(BANK_MAP.get(c, c) for c in bank_key),
            key_cols_frame=bank_key,
            stats=stats,
            existing_file_rows=file_rows.get("bank_txns"))

    inserted_bill_ids: set = set()
    if "bills" in gold_frames and "bills" not in reused_files:
        ids["bills"], inserted_bill_ids = _ingest_bills(
            session, gold_frames["bills"], base("bills"), stats,
            key_cols=bill_key, existing_file_rows=file_rows.get("bills"))

    if "recoveries" in gold_frames and "recoveries" not in reused_files:
        bill_ids = ids.get("bills", {})
        df = gold_frames["recoveries"]
        keep = []
        for rec in df.to_dict(orient="records"):
            seq = rec.get("bill_row_seq")
            gold_bill = (bill_ids.get(int(seq)) if not _is_na(seq) else None)
            # recovery lines ride with their bill: only NEW bills get rows,
            # merged bills keep the recovery detail from first ingest
            if gold_bill is not None and gold_bill in inserted_bill_ids:
                rec["_gold_bill_id"] = gold_bill
                keep.append(rec)
        if keep:
            bill_by_seq = {int(r["row_seq"]): r["_gold_bill_id"] for r in keep}
            sub = pd.DataFrame(keep).drop(columns=["_gold_bill_id"])
            ids["recoveries"] = _persist_frame(
                session, GoldRecovery, sub, RECOVERIES_MAP,
                base("recoveries"),
                resolve=lambda rec: {"gold_bill_id":
                                     bill_by_seq[int(rec["row_seq"])]})
            stats["rows_inserted"] += len(keep)
        else:
            ids.setdefault("recoveries", {})

    for frame in gold_frames:
        if frame.startswith("lineage") and frame not in reused_files:
            ids[frame] = _ingest_lineage(
                session, gold_frames[frame], LINEAGE_MAP, base(frame), stats,
                existing_file_rows=file_rows.get(frame))
    record_event(session, logger, event_type="gold.ingest_completed",
                 customer_id=customer_id, run_id=run_id,
                 details={**stats, "frames": list(gold_frames.keys())})
    return ids, stats


def _ingest_lineage(session, df, colmap, base, stats,
                    existing_file_rows: Optional[Dict[int, str]] = None
                    ) -> Dict[int, str]:
    """Lineage docs are append-only, keyed by (doc_type, doc_no) — the
    doc_type now rides in the frame itself (canonical unified shape), so
    one code path serves every lineage slot.

    A doc with a blank doc_no never matches by key, same rationale as
    _ingest_bills/_ingest_keyed above — `existing_file_rows` is the same
    guard against re-inserting it on a replayed ingest of this bronze file."""
    existing_file_rows = existing_file_rows or {}
    existing = {}
    for rid, doc_type, doc_no in session.execute(
            select(GoldLineageDoc.id, GoldLineageDoc.doc_type,
                   GoldLineageDoc.doc_no)
            .where(GoldLineageDoc.customer_id == base["customer_id"])):
        key = (_norm_key(doc_type), _norm_key(doc_no))
        if key[0] is not None and key[1] is not None:
            existing.setdefault(key, rid)

    ids: Dict[int, str] = {}
    to_insert = []
    for rec in df.to_dict(orient="records"):
        seq = int(rec["row_seq"])
        key = (_norm_key(rec.get("doc_type")), _norm_key(rec.get("doc_no")))
        if key[0] is not None and key[1] is not None and key in existing:
            ids[seq] = existing[key]
            stats["rows_reused"] += 1
        elif seq in existing_file_rows:
            ids[seq] = existing_file_rows[seq]
            stats["rows_reused"] += 1
        else:
            to_insert.append(rec)
    if to_insert:
        inserted = _persist_frame(session, GoldLineageDoc,
                                  pd.DataFrame(to_insert), colmap, base)
        ids.update(inserted)
        stats["rows_inserted"] += len(inserted)
    return ids
