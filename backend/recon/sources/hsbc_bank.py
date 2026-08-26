"""HSBC daily-statement PDF -> gold bank transactions."""

from typing import Dict, Optional

import pandas as pd

from ..gold import ensure_schema
from ..parsers import bank_selfcheck, parse_hsbc_statement
from .base import SelfCheckError, SilverResult, SourceAdapter


class HsbcBankAdapter(SourceAdapter):
    source_type = "bank_statement"
    adapter_key = "hsbc"
    label = "HSBC statement PDF"
    system = "HSBC"
    file_kinds = (".pdf",)

    def parse(self, path, params: dict) -> SilverResult:
        # full table, debits included; used_in_recon is derived in to_gold
        return SilverResult({"transactions": parse_hsbc_statement(path, credits_only=False)})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        df = silver.frames["transactions"].copy()
        if "used_in_recon" not in df.columns and "txn_type" in df.columns:
            df["used_in_recon"] = df["txn_type"] == "TFR+"
        df = ensure_schema(df, "bank_txns")
        df["row_seq"] = range(len(df))
        return {"bank_txns": df}

    def selfcheck(self, gold, path, params: dict) -> Optional[dict]:
        """The count and total HSBC prints on the last page must tie to
        the parsed credits — everything downstream depends on this parse."""
        bank = gold["bank_txns"]
        credits = bank[bank["used_in_recon"] == True]  # noqa: E712 (empty-frame safe)
        check = bank_selfcheck(credits, path)
        if check and (check["parsed_count"] != check["stated_count"]
                      or abs(check["parsed_total"] - check["stated_total"]) > 1):
            raise SelfCheckError(
                f"statement parse does not tie to the totals HSBC prints: {check}",
                check,
            )
        return check
