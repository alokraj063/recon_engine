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

def _slot_order(inputs):
    """Deterministic processing order: the two singleton roles first,
    then every lineage slot by name (0..N per customer). Lineage
    precedence in the join is doc-type driven (attach_lineage), so slot
    order only decides parse order."""
    fixed = [s for s in ("bank_statement", "bill_status") if s in inputs]
    return fixed + sorted(s for s in inputs
                          if s not in ("bank_statement", "bill_status"))


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


def check_signal_coverage(bank_df, bills_df, mapping) -> Optional[dict]:
    """WARN-level guard for a silently degraded configuration: a new bank
    or ERP adapter that never populates a mapped exact-signal column
    leaves every match LOW/AMOUNT_ONLY with no error anywhere. Returns
    None when every configured signal column carries at least one value
    on each non-empty side, else {"passed": False, "problems": [...]}.
    Field NAMES only — safe for logs and audit details."""
    from .matching.scoring import norm_text

    problems = []
    for sig in mapping.exact_signals:
        for side, df, col in (("bank", bank_df, sig.bank_field),
                              ("bill", bills_df, sig.bill_field)):
            if df is None or df.empty:
                continue
            if col not in df.columns or \
                    df[col].map(norm_text).isna().all():
                problems.append({"side": side, "field": col,
                                 "signal": sig.key or sig.bill_field})
    if not problems:
        return None
    return {"passed": False, "problems": problems}


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
    for source_type in _slot_order(inputs):
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
        for name, df in gold.items():
            # two lineage slots may share an adapter (same frame name) —
            # key lineage frames by their slot so neither clobbers the other
            gold_all[source_type if name.startswith("lineage") else name] = df

    # --- gold -> the exact frames the engine has always consumed -------
    bank_all = _drop_helpers(gold_all["bank_txns"])
    bank = (bank_all[bank_all["used_in_recon"] == True]   # noqa: E712 (empty-frame safe)
            .drop(columns=["used_in_recon"])
            .reset_index(drop=True).copy())
    bills = _drop_helpers(gold_all["bills"])
    recoveries = _drop_helpers(gold_all["recoveries"])
    # every lineage frame (any slot) carries the same canonical
    # lineage_docs shape with its own doc_type values — concat in slot
    # order; attach_lineage's doc-type priority handles precedence
    lineage_frames = [_drop_helpers(df) for name, df in gold_all.items()
                      if name.startswith("lineage") and df is not None]
    lineage = (pd.concat(lineage_frames, ignore_index=True)
               if lineage_frames else None)

    coverage = check_signal_coverage(bank, bills, rules.field_map)
    if coverage is not None:
        logger.warning("pipeline.signal_coverage", extra={
            "event_type": "pipeline.signal_coverage",
            "details": coverage})
        sinks.on_selfcheck("signal_coverage", None, coverage)

    out = reconcile(
        bank, bills, lineage,
        window_days=rules.window_days,
        co7_lookback_days=rules.co7_lookback_days,
        date_tolerance_days=rules.date_tolerance_days,
        amount_tolerance=rules.amount_tolerance,
        allow_batched=rules.allow_batched,
        max_batch_size=rules.max_batch_size,
        paid_statuses=rules.paid_statuses,
        weights=rules.weights,
        field_map=rules.field_map,
        copy_overrides=rules.copy_overrides,
        batch_amount_slack=rules.batch_amount_slack,
        amount_decimals=rules.amount_decimals,
    )
    out["bank"] = bank
    out["bank_all"] = bank_all
    out["bills"] = bills
    out["recoveries"] = recoveries
    out["queue"] = exception_queue(out)
    return out
