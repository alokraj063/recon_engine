"""
Snapshot the engine's output on the repo sample documents.

Run from backend/:  ../.venv/bin/python scripts/make_golden.py

Writes tests/golden/<frame>.csv — the golden master that every refactor
phase is diffed against (tests/test_golden.py). Regenerate ONLY when a
behaviour change is intended, and say so in the commit.
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd  # noqa: E402

from app.serialize import clean  # noqa: E402
from recon import ReconConfig, run  # noqa: E402

SAMPLE_DIR = (BACKEND.parent
              / "Receipt_reconciliation_and_IDR _ Requested_sample_documents")
GOLDEN_DIR = BACKEND / "tests" / "golden"

# Frames snapshotted from recon.engine.run()'s output dict.
FRAMES = ["summary", "matched", "queue", "bills_enriched", "bank", "recoveries"]


def sample_config() -> ReconConfig:
    """The four sample documents, resolved the same way the API defaults are."""
    def pick(patterns):
        for pat in patterns:
            for p in sorted(SAMPLE_DIR.glob(pat)):
                if not p.name.startswith("~$"):   # Excel lock files
                    return p
        raise FileNotFoundError(f"no sample document matching {patterns}")

    return ReconConfig(
        statement_pdf=pick(("*.pdf", "*.PDF")),
        bill_status=pick(("BILL STATUS*.xlsx",)),
        rnote=pick(("RNOTE*.xlsx",)),
        crn=pick(("CRN*.xlsx",)),
    )


def _stable(v):
    """One deterministic, diff-friendly value out of any frame cell:
    dates -> ISO, floats -> 2dp, dict/list -> sorted JSON text."""
    v = clean(v)

    def rnd(x):
        if isinstance(x, float):
            return round(x, 2)
        if isinstance(x, dict):
            return {k: rnd(y) for k, y in x.items()}
        if isinstance(x, list):
            return [rnd(y) for y in x]
        return x

    v = rnd(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return v


def normalize(df: pd.DataFrame) -> str:
    """A frame as canonical CSV text, comparable byte for byte."""
    rows = [{c: _stable(val) for c, val in row.items()}
            for row in df.to_dict(orient="records")]
    return pd.DataFrame(rows, columns=list(df.columns)).to_csv(index=False)


def build_frames() -> dict:
    out = run(sample_config(), verbose=False)
    return {name: out[name] for name in FRAMES}


def main():
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in build_frames().items():
        path = GOLDEN_DIR / f"{name}.csv"
        path.write_text(normalize(df), encoding="utf-8")
        print(f"wrote {path.relative_to(BACKEND)} ({len(df)} rows)")


if __name__ == "__main__":
    main()
