"""
Repair gold recovery lines lost by the old ingest rule (before
2026-09-29 lines were written for NEW bills only, so a bill first
exported before IREPS applied its deductions never got its per-head
lines even though its totals updated — 94 bills on the dev load).

Replays every bill-status bronze file of a customer from
its stored SILVER rows (what the parser read — no re-parse, no bronze
blob needed) through the file's own adapter to_gold() and the same
db/ingest.sync_recovery_lines an ingest now runs, against the gold bills
that file REPORTED (gold.file_rows) — each bill only from the NEWEST
file carrying it, which is what re-ingesting every export in order
ends with; LOCKED bills only get lines filled in when they have none
and the lines total their stored recovery_sum.

Idempotent: a second pass finds every bill already in sync.
"""

import numbers

import pandas as pd
from sqlalchemy import func, select

from logging_setup import get_logger
from recon.sources import BY_KEY
from recon.sources.base import SilverResult

from .audit import record_event
from .ingest import _record_sightings, sync_recovery_lines
from .models import BronzeFile, GoldFileRow, SilverRecord, SourceConfig

logger = get_logger(__name__)


def _silver_frame(rows) -> pd.DataFrame:
    """Silver JSON payloads -> the parser's frame. JSON loses dtypes, so
    a column whose values are all numbers comes back numeric (to_gold
    does arithmetic on the amounts)."""
    df = pd.DataFrame([r.payload for r in rows])
    for col in df.columns:
        vals = df[col].dropna()
        if len(vals) and all(isinstance(v, numbers.Number) and not isinstance(v, bool)
                             for v in vals):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def backfill_recovery_lines(session, customer_id: int) -> dict:
    report = {"files_replayed": 0, "files_skipped": 0, "bills_refreshed": 0,
              "locked_filled": 0, "lines_inserted": 0, "lines_deleted": 0,
              "skipped": []}
    slot = session.execute(
        select(SourceConfig).where(SourceConfig.customer_id == customer_id,
                                   SourceConfig.source_type == "bill_status",
                                   SourceConfig.is_active.is_(True))
    ).scalar_one_or_none()
    params = (slot.params or {}) if slot is not None else {}
    files = list(session.execute(
        select(BronzeFile).where(BronzeFile.customer_id == customer_id,
                                 BronzeFile.source_type == "bill_status")
        .order_by(BronzeFile.id)).scalars())
    # each bill's lines come from the NEWEST file that reported it, so a
    # second pass finds nothing to do (replaying every file in order would
    # flip a bill whose lines changed between exports back and forth)
    newest = dict(session.execute(
        select(GoldFileRow.gold_row_id, func.max(GoldFileRow.bronze_file_id))
        .where(GoldFileRow.customer_id == customer_id,
               GoldFileRow.frame == "bills")
        .group_by(GoldFileRow.gold_row_id)).all())
    for f in files:
        adapter = BY_KEY.get(f.adapter_key or (slot.adapter_key if slot else ""))
        rows = list(session.execute(
            select(SilverRecord).where(SilverRecord.bronze_file_id == f.id,
                                       SilverRecord.frame_name == "bills")
            .order_by(SilverRecord.row_seq)).scalars())
        reported = dict(session.execute(
            select(GoldFileRow.row_seq, GoldFileRow.gold_row_id)
            .where(GoldFileRow.bronze_file_id == f.id,
                   GoldFileRow.frame == "bills")).all())
        bill_ids = {seq: gid for seq, gid in reported.items() if newest.get(gid) == f.id}
        if not bill_ids:
            continue
        if adapter is None or not rows:
            report["files_skipped"] += 1
            report["skipped"].append({"file": f.original_name,
                                      "reason": "no adapter" if adapter is None
                                      else "no silver rows"})
            continue
        try:
            gold = adapter.to_gold(SilverResult({"bills": _silver_frame(rows)}), params)
        except Exception as e:     # one bad file must not stop the repair
            report["files_skipped"] += 1
            report["skipped"].append({"file": f.original_name,
                                      "reason": f"to_gold failed: {e}"})
            continue
        sync = sync_recovery_lines(
            session, customer_id, gold.get("recoveries"), bill_ids, set(),
            {"customer_id": customer_id, "run_id": None, "bronze_file_id": f.id})
        if sync["ids"]:
            # the file now reports these lines (idempotent)
            _record_sightings(session, customer_id, "recoveries", f.id, sync["ids"])
        session.flush()
        report["files_replayed"] += 1
        for k in ("bills_refreshed", "locked_filled", "lines_inserted", "lines_deleted"):
            report[k] += sync[k]
    if report["bills_refreshed"]:
        record_event(session, logger, event_type="gold.recoveries_backfilled",
                     customer_id=customer_id,
                     details={k: v for k, v in report.items() if k != "skipped"})
    return report
