"""
Golden-master gate: the engine's output on the sample documents must match
the committed snapshots in tests/golden/ byte for byte.

If a test fails after a deliberate behaviour change, regenerate with
scripts/make_golden.py and commit the new CSVs alongside the change.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import make_golden  # noqa: E402


@pytest.fixture(scope="module")
def frames():
    """One full engine run over the sample documents, shared by all checks."""
    return make_golden.build_frames()


@pytest.mark.parametrize("name", make_golden.FRAMES)
def test_frame_matches_golden(frames, name):
    golden_path = make_golden.GOLDEN_DIR / f"{name}.csv"
    assert golden_path.exists(), (
        f"missing {golden_path}; run scripts/make_golden.py first"
    )
    got = make_golden.normalize(frames[name])
    want = golden_path.read_text(encoding="utf-8")
    assert got == want, (
        f"'{name}' drifted from tests/golden/{name}.csv — if this change is "
        f"intended, rerun scripts/make_golden.py and commit the diff"
    )
