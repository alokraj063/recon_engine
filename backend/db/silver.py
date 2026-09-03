"""
Silver persistence: parsed rows in the source's native shape, one JSON
payload per row. Rows are keyed by bronze file, so re-running with the
same bytes never duplicates silver (bronze dedups by sha256 first).

The read helpers below serve the same rows back for browsing — silver is
returned exactly as parsed (IREPS PascalCase, RN_*/CR_*), never
translated to gold names: seeing the source's own vocabulary is the whole
point of the layer.
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select

from logging_setup import get_logger

from .audit import record_event
from .models import BronzeFile, SilverRecord

logger = get_logger(__name__)

SILVER_FRAME_CAP = 20_000


def persist_silver(session, customer_id: int, bronze_file_id: int,
                   frames_records: Dict[str, List[dict]]) -> int:
    """frames_records: frame_name -> list of JSON-safe row dicts.
    Skips frames already persisted for this bronze file. Returns rows added."""
    added = 0
    for frame_name, records in frames_records.items():
        exists = session.execute(
            select(SilverRecord.id)
            .where(SilverRecord.bronze_file_id == bronze_file_id,
                   SilverRecord.frame_name == frame_name)
            .limit(1)
        ).first()
        if exists:
            continue
        session.bulk_insert_mappings(SilverRecord, [
            {"bronze_file_id": bronze_file_id, "customer_id": customer_id,
             "frame_name": frame_name, "row_seq": i, "payload": payload}
            for i, payload in enumerate(records)
        ])
        added += len(records)
    if added:
        record_event(session, logger, event_type="silver.rows_persisted",
                     customer_id=customer_id, entity_type="bronze_file",
                     entity_id=bronze_file_id,
                     details={"frames": list(frames_records.keys()), "rows": added})
    return added


def silver_files(session, customer_id: int) -> List[dict]:
    """Every bronze file owning silver rows, with per-frame counts —
    feeds the silver browser's file/frame pickers."""
    counts: Dict[int, Dict[str, int]] = {}
    for bfid, frame_name, n in session.execute(
            select(SilverRecord.bronze_file_id, SilverRecord.frame_name,
                   func.count())
            .where(SilverRecord.customer_id == customer_id)
            .group_by(SilverRecord.bronze_file_id, SilverRecord.frame_name)):
        counts.setdefault(bfid, {})[frame_name] = n
    if not counts:
        return []
    files = {b.id: b for b in session.execute(
        select(BronzeFile).where(BronzeFile.id.in_(list(counts)))).scalars()}

    out = [{
        "bronze_file_id": bfid,
        "source_type": files[bfid].source_type,
        "original_name": files[bfid].original_name,
        "uploaded_at": files[bfid].uploaded_at.isoformat(),
        "silver_counts": frames,
    } for bfid, frames in counts.items() if bfid in files]
    out.sort(key=lambda x: x["uploaded_at"], reverse=True)
    return out


def silver_frame(session, customer_id: int, frame_name: str,
                 bronze_file_id: Optional[int] = None,
                 limit: int = SILVER_FRAME_CAP) -> Tuple[List[dict], int]:
    """One silver frame's rows, source-native, in parse order
    ((bronze_file_id, row_seq)). Returns (rows, total) — `total` exposes
    truncation, like the gold browse."""
    where = [SilverRecord.customer_id == customer_id,
             SilverRecord.frame_name == frame_name]
    if bronze_file_id is not None:
        where.append(SilverRecord.bronze_file_id == bronze_file_id)
    total = session.execute(
        select(func.count()).select_from(SilverRecord).where(*where)).scalar()
    records = list(session.execute(
        select(SilverRecord).where(*where)
        .order_by(SilverRecord.bronze_file_id, SilverRecord.row_seq)
        .limit(min(limit, SILVER_FRAME_CAP))).scalars())
    rows = [{**(r.payload or {}),
             "bronze_file_id": r.bronze_file_id,
             "row_seq": r.row_seq} for r in records]
    return rows, total
