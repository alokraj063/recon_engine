"""
In-memory store of completed runs, so the workbook download and a
page-reload refetch never recompute. Local single-user tool: a dict and
a prune are all the persistence this needs.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunRecord:
    tmpdir: Path
    payload: dict
    workbook_path: Path
    # source frames (name -> DataFrame), serialized lazily on first request
    frames: dict = field(default_factory=dict)
    frame_cache: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


RUNS: dict[str, RunRecord] = {}


def new_run(tmpdir: Path, payload: dict, workbook_path: Path,
            frames: dict | None = None) -> str:
    run_id = uuid.uuid4().hex
    RUNS[run_id] = RunRecord(tmpdir, payload, workbook_path, frames or {})
    payload["run_id"] = run_id
    prune()
    return run_id


def get_run(run_id: str) -> RunRecord | None:
    return RUNS.get(run_id)


def prune(keep: int = 10):
    """Drop the oldest runs and their temp files beyond `keep`."""
    while len(RUNS) > keep:
        oldest = min(RUNS, key=lambda k: RUNS[k].created_at)
        rec = RUNS.pop(oldest)
        shutil.rmtree(rec.tmpdir, ignore_errors=True)
