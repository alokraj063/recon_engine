"""
Reconcile-from-gold (two-step workflow) plus the gold-layer read helpers
the browse/ingestions endpoints use.

Snapshot-from-gold is the sibling of db/incremental.py's pool machinery,
with the OPPOSITE ledger semantics on purpose: snapshot uses ALL bills
(including ledger-consumed ones), carries no exceptions forward, and
honours the user's real window_days — it is the legacy one-shot snapshot
minus the parsing, fed from gold instead of freshly parsed frames.
Frames rebuilt here must be equivalent to freshly-parsed ones for
matching purposes: ordering (bronze_file_id, row_seq) reproduces parse
order, which keeps AMBIGUOUS tie-breaking deterministic and lets a
single-export gold reproduce the legacy snapshot's counts exactly.
"""

from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import func, select

from logging_setup import get_logger
from recon.engine import exception_queue, reconcile
from recon.gold import ensure_schema
from recon.rules import MatchRuleSet

from .gold import (BANK_MAP, BILLS_MAP, CRN_MAP, RECOVERIES_MAP, RNOTE_MAP,
                   frame_from_gold)
from .models import (AuditLog, BronzeFile, GoldBankTxn, GoldBill,
                     GoldLineageDoc, GoldRecovery)

logger = get_logger(__name__)

# Browse view of gold.lineage_docs in its canonical unified shape — no
# RN_*/CR_* reversal; those shapes stay engine-internal.
LINEAGE_VIEW_MAP = {c: c for c in (
    "doc_type", "doc_no", "doc_date", "invoice_no", "submission_ref",
    "payment_order_ref",
    "po_no", "po_date", "receipt_qty", "drr_or_challan_no", "bill_reg_no")}

# browse frame name -> (model, colmap, frame_from_gold frame_name, ensure)
BROWSE_FRAMES = {
    "bank": (GoldBankTxn, BANK_MAP, "bank_txns", True),
    "bills": (GoldBill, BILLS_MAP, "bills", True),
    "recoveries": (GoldRecovery, RECOVERIES_MAP, "recoveries", True),
    "lineage": (GoldLineageDoc, LINEAGE_VIEW_MAP, "lineage_view", False),
}

GOLD_FRAME_CAP = 20_000


# --- snapshot-from-gold -------------------------------------------------

def build_snapshot_frames(session, customer_id: int,
                          statement_bronze_id: int) -> dict:
    """Engine-shaped frames for a legacy-semantics snapshot: the chosen
    statement's txns vs ALL current gold bills (entity-upserted latest
    state). ids lists are positional, like the matcher's indices."""
    txn_rows = list(session.execute(
        select(GoldBankTxn)
        .where(GoldBankTxn.bronze_file_id == statement_bronze_id)
        .order_by(GoldBankTxn.row_seq)).scalars())
    bank_all_df, _ = frame_from_gold(txn_rows, BANK_MAP, "bank_txns",
                                     ensure=ensure_schema)
    credit_rows = [r for r in txn_rows if r.used_in_recon]
    bank_df, bank_ids = frame_from_gold(credit_rows, BANK_MAP, "bank_txns",
                                        ensure=ensure_schema)
    if "used_in_recon" in bank_df.columns:
        bank_df = bank_df.drop(columns=["used_in_recon"])

    bill_rows = list(session.execute(
        select(GoldBill).where(GoldBill.customer_id == customer_id)
        .order_by(GoldBill.bronze_file_id, GoldBill.row_seq)).scalars())
    bills_df, bill_ids = frame_from_gold(bill_rows, BILLS_MAP, "bills",
                                         ensure=ensure_schema)

    def lineage(doc_type, colmap, frame_name):
        rows = list(session.execute(
            select(GoldLineageDoc)
            .where(GoldLineageDoc.customer_id == customer_id,
                   GoldLineageDoc.doc_type == doc_type)
            .order_by(GoldLineageDoc.bronze_file_id,
                      GoldLineageDoc.row_seq)).scalars())
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

    return {"bank_all_df": bank_all_df, "bank_df": bank_df,
            "bank_ids": bank_ids, "bills_df": bills_df, "bill_ids": bill_ids,
            "rnote_df": rnote_df, "crn_df": crn_df,
            "recoveries_df": recoveries_df}


def run_snapshot(session, customer_id: int, statement_bronze_id: int,
                 rules: MatchRuleSet) -> Tuple[dict, List[str], List[str]]:
    """Legacy-semantics snapshot over gold frames. Returns
    (out frames dict, bank_ids, bill_ids) — ids positional. NO ledger
    reads or writes; the user's REAL window_days applies."""
    f = build_snapshot_frames(session, customer_id, statement_bronze_id)
    out = reconcile(
        f["bank_df"], f["bills_df"], f["rnote_df"], f["crn_df"],
        window_days=rules.window_days,
        co7_lookback_days=rules.co7_lookback_days,
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
    out["bank"] = f["bank_df"]
    out["bank_all"] = f["bank_all_df"]
    out["bills"] = f["bills_df"]
    out["recoveries"] = f["recoveries_df"]
    out["queue"] = exception_queue(out)
    return out, f["bank_ids"], f["bill_ids"]


def statement_credits_frame(session, statement_bronze_id: int):
    """The chosen statement's FULL credits rebuilt from gold — the frame
    a bank-adapter selfcheck must verify. Incremental runs match on a
    pool (a designed subset of the statement), so checking the pool
    against the statement's printed totals would mismatch by design."""
    txn_rows = list(session.execute(
        select(GoldBankTxn)
        .where(GoldBankTxn.bronze_file_id == statement_bronze_id)
        .order_by(GoldBankTxn.row_seq)).scalars())
    credit_rows = [r for r in txn_rows if r.used_in_recon]
    bank_df, _ = frame_from_gold(credit_rows, BANK_MAP, "bank_txns",
                                 ensure=ensure_schema)
    if "used_in_recon" in bank_df.columns:
        bank_df = bank_df.drop(columns=["used_in_recon"])
    return bank_df


# --- gold browse helpers (feed the /api/gold/* endpoints) ---------------

def gold_frame(session, frame: str, customer_id: int,
               bronze_file_id: Optional[int] = None,
               limit: int = GOLD_FRAME_CAP):
    """One gold table as an engine-shaped frame. Returns
    (df, provenance, total) where provenance[i] = (bronze_file_id,
    row_seq) of frame row i — the caller stamps these onto the serialized
    records (frame_from_gold deliberately omits them)."""
    model, colmap, frame_name, use_ensure = BROWSE_FRAMES[frame]
    where = [model.customer_id == customer_id]
    if bronze_file_id is not None:
        where.append(model.bronze_file_id == bronze_file_id)
    total = session.execute(
        select(func.count()).select_from(model).where(*where)).scalar()
    rows = list(session.execute(
        select(model).where(*where)
        .order_by(model.bronze_file_id, model.row_seq)
        .limit(min(limit, GOLD_FRAME_CAP))).scalars())
    df, _ = frame_from_gold(rows, colmap, frame_name,
                            ensure=ensure_schema if use_ensure else None)
    provenance = [(r.bronze_file_id, r.row_seq) for r in rows]
    return df, provenance, total


def gold_files(session, customer_id: int) -> List[dict]:
    """Every bronze file owning gold rows, with per-frame counts; bank
    statements additionally get their credit count + value-date range —
    this feeds both the Reconcile statement picker and the gold tabs'
    ingestion filters."""
    counts: Dict[int, Dict[str, int]] = {}
    for model, key in ((GoldBankTxn, "bank_txns"), (GoldBill, "bills"),
                       (GoldRecovery, "recoveries"),
                       (GoldLineageDoc, "lineage_docs")):
        for bfid, n in session.execute(
                select(model.bronze_file_id, func.count())
                .where(model.customer_id == customer_id)
                .group_by(model.bronze_file_id)):
            counts.setdefault(bfid, {})[key] = n
    if not counts:
        return []
    files = {b.id: b for b in session.execute(
        select(BronzeFile).where(BronzeFile.id.in_(list(counts)))).scalars()}

    out = []
    for bfid, gold_counts in counts.items():
        b = files.get(bfid)
        if b is None:
            continue
        statement = None
        if b.source_type == "bank_statement":
            lo, hi, credits = session.execute(
                select(func.min(GoldBankTxn.value_date),
                       func.max(GoldBankTxn.value_date),
                       func.count())
                .where(GoldBankTxn.bronze_file_id == bfid,
                       GoldBankTxn.used_in_recon.is_(True))).one()
            statement = {
                "value_date_min": lo.isoformat() if lo else None,
                "value_date_max": hi.isoformat() if hi else None,
                "credits": credits,
            }
        out.append({
            "bronze_file_id": bfid,
            "source_type": b.source_type,
            "original_name": b.original_name,
            "uploaded_at": b.uploaded_at.isoformat(),
            "gold_counts": gold_counts,
            "statement": statement,
        })
    out.sort(key=lambda x: x["uploaded_at"], reverse=True)
    return out


def list_ingestions(session, customer_id: int, limit: int = 50) -> List[dict]:
    """ingestion.completed audit events, newest first, with bronze file
    names joined at read time (names stay out of audit details)."""
    events = list(session.execute(
        select(AuditLog)
        .where(AuditLog.customer_id == customer_id,
               AuditLog.event_type == "ingestion.completed")
        .order_by(AuditLog.created_at.desc()).limit(limit)).scalars())
    file_ids = {f["bronze_file_id"]
                for e in events for f in (e.details or {}).get("files", [])}
    names = {b.id: b.original_name for b in session.execute(
        select(BronzeFile).where(BronzeFile.id.in_(list(file_ids)))).scalars()
    } if file_ids else {}
    return [{
        "id": e.id,
        "at": e.created_at.isoformat(),
        "stats": (e.details or {}).get("stats"),
        "selfcheck_passed": (e.details or {}).get("selfcheck_passed"),
        "files": [{**f, "original_name": names.get(f.get("bronze_file_id"))}
                  for f in (e.details or {}).get("files", [])],
    } for e in events]


def get_statement_bronze(session, customer_id: int,
                         statement_bronze_id: int) -> Optional[BronzeFile]:
    """The bronze row for a reconcile target — must belong to the
    customer, be a bank statement, and own gold txn rows."""
    b = session.get(BronzeFile, statement_bronze_id)
    if (b is None or b.customer_id != customer_id
            or b.source_type != "bank_statement"):
        return None
    has_gold = session.execute(
        select(GoldBankTxn.id)
        .where(GoldBankTxn.bronze_file_id == statement_bronze_id)
        .limit(1)).first()
    return b if has_gold else None
