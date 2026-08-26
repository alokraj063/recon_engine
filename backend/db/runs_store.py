"""
Persisted runs: payload, serialized frames, and the match link table.
Silver/gold ingestion happens separately (db.silver / db.ingest) before a
run is persisted — gold rows are ingestion-owned, not run-owned.

The app layer serializes frames/payloads before calling in (db never
imports app).
"""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select

from logging_setup import get_logger

from .audit import record_event
from .base import SessionLocal
from .models import Run, RunFrame, RunMatchBill
from .storage import storage

logger = get_logger(__name__)


def persist_success(customer_id: int,
                    rule_set_id: Optional[int],
                    mode: str,
                    params: dict,
                    payload: dict,
                    selfcheck: Optional[dict],
                    workbook_src: Optional[Path],
                    frame_records: Dict[str, List[dict]],
                    match_links: List[dict],
                    run_id: Optional[str] = None) -> str:
    """Persist a completed run. match_links rows carry
    {match_id, gold_bill_id, role}. When run_id is given, an existing
    'running' row (incremental guard) is finalised instead of inserted.
    Sets payload["run_id"] before storing so reads return it."""
    run_id = run_id or uuid.uuid4().hex
    payload["run_id"] = run_id

    workbook_path = None
    if workbook_src is not None and Path(workbook_src).exists():
        dest = storage.run_dir(run_id) / "Recon_Output.xlsx"
        shutil.copy2(workbook_src, dest)
        workbook_path = str(dest)

    with SessionLocal() as session:
        existing = session.get(Run, run_id)
        if existing is not None:
            existing.status = "succeeded"
            existing.rule_set_id = rule_set_id
            existing.params = params
            existing.payload = payload
            existing.selfcheck = selfcheck
            existing.workbook_path = workbook_path
        else:
            session.add(Run(
                id=run_id, customer_id=customer_id, rule_set_id=rule_set_id,
                status="succeeded", mode=mode, params=params, payload=payload,
                selfcheck=selfcheck, workbook_path=workbook_path,
            ))
        if match_links:
            session.bulk_insert_mappings(RunMatchBill, [
                {"run_id": run_id, **link} for link in match_links
            ])
        for name, rows in frame_records.items():
            session.add(RunFrame(run_id=run_id, name=name,
                                 row_count=len(rows), rows=rows))
        record_event(session, logger, event_type="run.succeeded",
                     customer_id=customer_id, run_id=run_id,
                     details={"mode": mode})
        session.commit()

    prune_run_files()
    return run_id


def persist_failure(customer_id: int, mode: str, params: dict,
                    error: dict, run_id: Optional[str] = None) -> str:
    """A failed run leaves an audit row, never partial results. Finalises
    an existing 'running' row when run_id is given."""
    run_id = run_id or uuid.uuid4().hex
    with SessionLocal() as session:
        existing = session.get(Run, run_id)
        if existing is not None:
            existing.status = "failed"
            existing.error = error
        else:
            session.add(Run(id=run_id, customer_id=customer_id,
                            status="failed", mode=mode, params=params,
                            error=error))
        record_event(session, logger, event_type="run.failed",
                     level=logging.WARNING, customer_id=customer_id,
                     run_id=run_id, details={"mode": mode, "error": error})
        session.commit()
    return run_id


def get_run(run_id: str) -> Optional[Run]:
    with SessionLocal() as session:
        return session.get(Run, run_id)


def get_frame(run_id: str, name: str) -> Optional[List[dict]]:
    with SessionLocal() as session:
        row = session.execute(
            select(RunFrame).where(RunFrame.run_id == run_id,
                                   RunFrame.name == name)
        ).scalar_one_or_none()
        return None if row is None else row.rows


def frame_names(run_id: str) -> List[str]:
    with SessionLocal() as session:
        return sorted(session.execute(
            select(RunFrame.name).where(RunFrame.run_id == run_id)
        ).scalars())


def list_runs(customer_id: Optional[int] = None, limit: int = 50) -> List[Run]:
    with SessionLocal() as session:
        q = select(Run).order_by(Run.created_at.desc()).limit(limit)
        if customer_id is not None:
            q = q.where(Run.customer_id == customer_id)
        return list(session.execute(q).scalars())


def prune_run_files(keep: int = 20):
    """File retention only — DB rows are kept (they are the audit trail).
    Drops the oldest run directories (workbooks) beyond `keep`."""
    runs_dir = storage.root / "runs"
    if not runs_dir.is_dir():
        return
    dirs = sorted((d for d in runs_dir.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime)
    if len(dirs) > keep:
        for d in dirs[:-keep]:
            shutil.rmtree(d, ignore_errors=True)
