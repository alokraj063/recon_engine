"""Oracle "External AR Statement" -> gold ar_invoices (one snapshot per file)."""

from typing import Dict

import pandas as pd

from ..gold import ensure_schema
from ..parsers import parse_ar_statement
from .base import SelfCheckError, SilverResult, SourceAdapter

AR_TO_GOLD = {
    "Invoice number": "invoice_number",
    "Invoice date": "invoice_date",
    "Due date": "due_date",
    "Customer number": "customer_number",
    "Customer name": "customer_name",
    "Operating unit": "operating_unit",
    "Sales Rep": "sales_rep",
    "Sales Order Type": "sales_order_type",
    "Category": "category",
    "Sub category": "subcategory",
    "Functional Currency": "currency",
    "Functional amount": "functional_amount",
    "Functional amount - open": "functional_amount_open",
}
_DATES = ("invoice_date", "due_date")
_AMOUNTS = ("functional_amount", "functional_amount_open")
_TEXT = ("invoice_number", "customer_number", "operating_unit", "sales_rep",
         "sales_order_type", "category", "subcategory", "customer_name")


def _text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


class OracleArAdapter(SourceAdapter):
    source_type = "ar_statement"
    adapter_key = "oracle_ar"
    label = "Oracle AR statement"
    system = "Oracle"
    file_kinds = (".xlsx", ".xlsm", ".xls")

    def parse(self, path, params: dict) -> SilverResult:
        df, meta = parse_ar_statement(path)
        meta = {**meta, "statement_date": (meta["statement_date"].isoformat()
                                           if meta.get("statement_date") else None)}
        return SilverResult({"ar_statement": df}, meta)

    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        df = silver.frames["ar_statement"].rename(columns=AR_TO_GOLD).copy()
        for c in _DATES:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        for c in _AMOUNTS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        for c in _TEXT:
            if c in df.columns:
                df[c] = df[c].map(_text)
        # statement date: the file's own title, else the caller's, else
        # the newest invoice it lists
        stated = silver.meta.get("statement_date") or params.get("statement_date")
        if stated:
            as_of = pd.Timestamp(stated)
        elif "invoice_date" in df.columns and df["invoice_date"].notna().any():
            as_of = df["invoice_date"].max().normalize()
        else:
            as_of = pd.NaT
        df["statement_date"] = as_of
        df = ensure_schema(df, "ar_invoices")
        df["row_seq"] = range(len(df))
        return {"ar_invoices": df}

    def selfcheck(self, gold, path, params):
        df = gold["ar_invoices"]
        if df.empty:
            raise SelfCheckError("AR statement lists no invoices")
        if df["statement_date"].isna().all():
            raise SelfCheckError("AR statement has no statement date")
        return None
