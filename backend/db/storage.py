"""
File storage behind a small interface so the local directory can later be
swapped for S3 without touching callers. Bronze files are content-addressed
(sha256) per customer; run artifacts (the workbook) live per run_id.
"""

import hashlib
import shutil
from pathlib import Path

from .base import DATA_DIR


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalStorage:
    """data/bronze/{customer_key}/{sha256}{ext} and data/runs/{run_id}/."""

    def __init__(self, root: Path = DATA_DIR):
        self.root = Path(root)

    def save_bronze(self, customer_key: str, src: Path, sha256: str) -> Path:
        dest = self.root / "bronze" / customer_key / f"{sha256}{Path(src).suffix.lower()}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return dest

    def run_dir(self, run_id: str) -> Path:
        d = self.root / "runs" / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d


storage = LocalStorage()
