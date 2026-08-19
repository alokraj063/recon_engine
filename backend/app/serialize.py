"""
Frames -> JSON-safe structures.

json.dumps rejects NaN, and the recon frames carry NaN/NaT/pd.NA, numpy
scalars, one dict-valued column (Recoveries) and one list-valued column
(bill_indices). Everything funnels through clean() so nothing leaks.
"""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd


def clean(v):
    """One JSON-safe value out of whatever a frame cell holds."""
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, np.ndarray)):
        return [clean(x) for x in v]
    # scalars only past this point; pd.isna on a container returns an array.
    # is_scalar covers NaT too, which would otherwise fall into the
    # datetime branch below (NaT is an instance of datetime).
    if v is None or (pd.api.types.is_scalar(v) and pd.isna(v)):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat()  # every recon date is day-granular
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def df_to_records(df):
    if df is None or df.empty:
        return []
    return [{col: clean(val) for col, val in row.items()}
            for row in df.to_dict(orient="records")]


def summary_records(summary):
    """Summary rows plus an explicit indent flag, so the frontend does
    not depend on rendering leading whitespace."""
    out = []
    for row in df_to_records(summary):
        cat = row.get("Category") or ""
        row["indent"] = cat.startswith(" ")
        row["Category"] = cat.strip()
        out.append(row)
    return out
