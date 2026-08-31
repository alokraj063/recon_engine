"""
Adapter-seam guards. ensure_schema silently adds any declared-but-missing
gold column as all-NA while an unrenamed silver column would leak into
extras — so the silver->gold rename maps must cover the canonical schema
exactly, or a drifted map degrades with zero errors.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.gold import GOLD_COLUMNS  # noqa: E402
from recon.sources.ireps_bills import (BILLS_TO_GOLD,  # noqa: E402
                                       RECOVERIES_TO_GOLD)
from recon.sources.ireps_crn import CRN_TO_GOLD  # noqa: E402
from recon.sources.ireps_rnote import RNOTE_TO_GOLD  # noqa: E402


def test_bills_map_matches_gold_schema():
    assert set(BILLS_TO_GOLD.values()) == set(GOLD_COLUMNS["bills"])
    # a silver name mapping onto itself would mask a missed rename
    assert not set(BILLS_TO_GOLD) & set(GOLD_COLUMNS["bills"])


def test_recoveries_map_matches_gold_schema():
    assert set(RECOVERIES_TO_GOLD.values()) == set(GOLD_COLUMNS["recoveries"])
    assert not set(RECOVERIES_TO_GOLD) & set(GOLD_COLUMNS["recoveries"])


def test_lineage_maps_match_gold_schema():
    # doc_type is set by the adapter, not renamed from a silver column
    want = set(GOLD_COLUMNS["lineage_docs"]) - {"doc_type"}
    for name, colmap in (("rnote", RNOTE_TO_GOLD), ("crn", CRN_TO_GOLD)):
        assert set(colmap.values()) == want, name
        assert not set(colmap) & set(GOLD_COLUMNS["lineage_docs"]), name
