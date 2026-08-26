"""
Orchestration: source files -> adapters (silver -> gold) -> reconcile.

Gold frames flow in memory straight into the engine; persistence is a
side effect via the optional `sinks` callbacks. The CLI passes no sinks
and runs with no database at all; the API passes DB-writing sinks. A run
never depends on reading anything back from a database.
"""

from typing import Dict, Optional

import pandas as pd

from logging_setup import get_logger

from .rules import MatchRuleSet
from .sources.base import SilverResult, SourceAdapter

logger = get_logger(__name__)

SOURCE_ORDER = ("bank_statement", "bill_status", "lineage_rnote", "lineage_crn")


class PipelineSinks:
    """Optional persistence callbacks. Every hook is a no-op by default."""

    def on_silver(self, source_type: str, adapter: SourceAdapter,
                  silver: SilverResult) -> None:
        pass

    def on_gold(self, source_type: str, adapter: SourceAdapter,
                gold: Dict[str, pd.DataFrame]) -> None:
        pass

    def on_selfcheck(self, source_type: str, adapter: SourceAdapter,
                     check: Optional[dict]) -> None:
        pass


def _drop_helpers(df: Optional[pd.DataFrame], cols=("row_seq", "bill_row_seq")):
    """Gold bookkeeping columns stay out of the engine-facing frames so
    the output is byte-identical with the pre-refactor engine."""
    if df is None:
        return None
    return df.drop(columns=[c for c in cols if c in df.columns])


def run_pipeline(inputs: Dict[str, object],
                 adapters: Dict[str, SourceAdapter],
                 params: Optional[Dict[str, dict]] = None,
                 rules: Optional[MatchRuleSet] = None,
                 sinks: Optional[PipelineSinks] = None,
                 verbose: bool = False) -> dict:
    """
    inputs: source_type -> file path (lineage entries may be None/absent).
    Returns the same 12-frame dict `recon.engine.run` always has.
    """
    from .engine import exception_queue, reconcile   # engine must not import pipeline at module level

    params = params or {}
    rules = rules or MatchRuleSet()
    sinks = sinks or PipelineSinks()

    gold_all: Dict[str, pd.DataFrame] = {}
    for source_type in SOURCE_ORDER:
        path = inputs.get(source_type)
        if path is None:
            continue
        adapter = adapters[source_type]
        p = params.get(source_type, {})

        silver = adapter.parse(path, p)
        sinks.on_silver(source_type, adapter, silver)
        gold = adapter.to_gold(silver, p)
        sinks.on_gold(source_type, adapter, gold)
        check = adapter.selfcheck(gold, path, p)   # raises SelfCheckError -> fail loud
        sinks.on_selfcheck(source_type, adapter, check)
        if verbose and check is not None:
            print(f"{source_type} self-check:", check)
        if check is not None:
            # counts only — check can carry rupee totals (e.g. HSBC's
            # stated_total/parsed_total), which stay out of log lines
            logger.info("pipeline.selfcheck", extra={
                "event_type": "pipeline.selfcheck",
                "details": {"source_type": source_type, "passed": True,
                           "parsed_count": check.get("parsed_count"),
                           "stated_count": check.get("stated_count")}})
        gold_all.update(gold)

    # --- gold -> the exact frames the engine has always consumed -------
    bank_all = _drop_helpers(gold_all["bank_txns"])
    bank = (bank_all[bank_all["used_in_recon"] == True]   # noqa: E712 (empty-frame safe)
            .drop(columns=["used_in_recon"])
            .reset_index(drop=True).copy())
    bills = _drop_helpers(gold_all["bills"])
    recoveries = _drop_helpers(gold_all["recoveries"])
    rnote = _drop_helpers(gold_all.get("lineage_rnote"))
    crn = _drop_helpers(gold_all.get("lineage_crn"))

    out = reconcile(
        bank, bills, rnote, crn,
        window_days=rules.window_days,
        co7_lookback_days=rules.co7_lookback_days,
        date_tolerance_days=rules.date_tolerance_days,
        amount_tolerance=rules.amount_tolerance,
        allow_batched=rules.allow_batched,
        max_batch_size=rules.max_batch_size,
        paid_statuses=rules.paid_statuses,
        weights=rules.weights,
        field_map=rules.field_map,
    )
    out["bank"] = bank
    out["bank_all"] = bank_all
    out["bills"] = bills
    out["recoveries"] = recoveries
    out["queue"] = exception_queue(out)
    return out
