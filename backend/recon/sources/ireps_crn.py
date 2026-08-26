"""IREPS CRN (challan) report -> gold lineage documents."""

from typing import Dict

import pandas as pd

from ..gold import ensure_schema
from ..parsers import load_crn
from .base import SilverResult, SourceAdapter


class IrepsCrnAdapter(SourceAdapter):
    source_type = "lineage_crn"
    adapter_key = "ireps_crn"
    label = "IREPS CRN report"
    system = "IREPS"
    file_kinds = (".xlsx", ".xlsm")

    def parse(self, path, params: dict) -> SilverResult:
        return SilverResult({"crn": load_crn(path, params.get("sheet", 0))})

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        df = ensure_schema(silver.frames["crn"].copy(), "lineage_crn")
        df["row_seq"] = range(len(df))
        return {"lineage_crn": df}
