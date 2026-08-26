"""
Bronze registry: every input file a run consumes is hashed, copied into
managed storage and recorded once per (customer, sha256). Re-registering
the same bytes returns the existing row — uploads and the repo sample
defaults both funnel through here.
"""

from pathlib import Path
from typing import Optional

from sqlalchemy import select

from logging_setup import get_logger

from .audit import record_event
from .models import BronzeFile, Customer
from .storage import file_sha256, storage

logger = get_logger(__name__)


def register_file(session, customer: Customer, source_type: str,
                  path: Path, original_name: str,
                  adapter_key: Optional[str] = None) -> BronzeFile:
    path = Path(path)
    sha = file_sha256(path)
    existing = session.execute(
        select(BronzeFile).where(BronzeFile.customer_id == customer.id,
                                 BronzeFile.sha256 == sha)
    ).scalar_one_or_none()
    if existing is not None:
        record_event(session, logger, event_type="bronze.file_deduped",
                     customer_id=customer.id, entity_type="bronze_file",
                     entity_id=existing.id,
                     details={"source_type": source_type})
        return existing

    stored = storage.save_bronze(customer.key, path, sha)
    rec = BronzeFile(
        customer_id=customer.id,
        source_type=source_type,
        adapter_key=adapter_key,
        original_name=original_name,
        stored_path=str(stored),
        sha256=sha,
        size_bytes=path.stat().st_size,
    )
    session.add(rec)
    session.flush()   # assign id without committing
    record_event(session, logger, event_type="bronze.file_registered",
                 customer_id=customer.id, entity_type="bronze_file",
                 entity_id=rec.id,
                 details={"source_type": source_type, "size_bytes": rec.size_bytes})
    return rec
