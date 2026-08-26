"""
Silver persistence: parsed rows in the source's native shape, one JSON
payload per row. Rows are keyed by bronze file, so re-running with the
same bytes never duplicates silver (bronze dedups by sha256 first).
"""

from typing import Dict, List

from sqlalchemy import select

from logging_setup import get_logger

from .audit import record_event
from .models import SilverRecord

logger = get_logger(__name__)


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
