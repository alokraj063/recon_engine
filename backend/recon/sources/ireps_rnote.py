"""IREPS RNOTE (receipt note) report -> gold lineage documents."""

from typing import Dict

import pandas as pd

from ..gold import ensure_schema
from ..parsers import load_rnote
from .base import SilverResult, SourceAdapter


class IrepsRnoteAdapter(SourceAdapter):
    source_type = "lineage_rnote"
    adapter_key = "ireps_rnote"
    label = "IREPS RNOTE report"
    system = "IREPS"
    file_kinds = (".xlsx", ".xlsm")

    def parse(self, path, params: dict) -> SilverResult:
        # `sheet` finally reachable from config (the CLI never exposed it)
        return SilverResult({"rnote": load_rnote(path, params.get("sheet", 0))})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        df = ensure_schema(silver.frames["rnote"].copy(), "lineage_rnote")
        df["row_seq"] = range(len(df))
        return {"lineage_rnote": df}
