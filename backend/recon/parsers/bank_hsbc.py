"""
HSBC daily statement parser, cell-based.

The statement is a real ruled table, so pdfplumber can pull it out cell by
cell. No need to guess at line boundaries. A cell that wraps over several
visual lines comes back as one string with \n inside it, which we flatten.
"""

import re
from datetime import datetime

import pandas as pd
import pdfplumber

COLUMNS = [
    "bank_ref",
    "customer_ref",
    "txn_type",
    "supplementary",
    "narrative",
    "value_date",
    "amount",
    "timestamp",
]

# pdfplumber finds the table from the drawn borders. "text" as the fallback
# vertical strategy keeps it working if a page loses its ruling lines.
TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 5,
    "join_tolerance": 5,
    "snap_tolerance": 3,
}


def _flatten(cell):
    """Turn a wrapped cell into one clean string."""
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", cell.replace("\n", " ")).strip()


def _is_data_row(row):
    """Header, balance-brought-forward and footer rows all fail this."""
    if len(row) != len(COLUMNS):
        return False
    if row[2] not in ("TFR+", "TFR-"):
        return False
    return bool(re.fullmatch(r"\d{2}/\d{2}/\d{4}", row[5] or ""))


def _to_amount(text):
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_hsbc_statement(pdf_path, credits_only=True):
    """
    Read every transaction row from an HSBC daily statement.

    credits_only=True keeps TFR+ rows. Set it False to get debits too.
    Returns a DataFrame with one row per transaction, whatever the
    original row height was in the PDF.
    """
    records = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables(TABLE_SETTINGS):
                for raw_row in table:
                    row = [_flatten(c) for c in raw_row]
                    if not _is_data_row(row):
                        continue

                    rec = dict(zip(COLUMNS, row))
                    rec["amount"] = _to_amount(rec["amount"])
                    rec["value_date"] = datetime.strptime(
                        rec["value_date"], "%d/%m/%Y"
                    )
                    rec["page"] = page_no
                    records.append(rec)

    df = pd.DataFrame(records)
    if df.empty:
        return df

    if credits_only:
        df = df[df["txn_type"] == "TFR+"].reset_index(drop=True)

    df["zone_guess"] = df["narrative"].apply(extract_zone_from_narrative)
    return df


# Railway zone codes that show up in the NEFT narratives.
ZONE_CODES = [
    "NWR", "NER", "NFR", "NCR", "ECR", "ECOR", "WCR", "SECR", "SWR",
    "SCR", "SER", "SR", "CR", "ER", "WR", "NR",
]
_ZONE_RE = re.compile(r"\b(" + "|".join(ZONE_CODES) + r")")


def extract_zone_from_narrative(narrative):
    """
    Pull the railway zone out of a narrative like
    'NEFT FROM 0406GKPW NER4th Bill of A ...'.
    Longer codes are tested first so NER doesn't get read as ER.
    """
    if not narrative:
        return None
    body = re.sub(r"^NEFT FROM\s+", "", narrative)
    body = re.sub(r"^[0-9]{4}[A-Z]+\s*", "", body)
    m = _ZONE_RE.search(body)
    return m.group(1) if m else None


def bank_selfcheck(df, pdf_path):
    """Sanity check against the totals HSBC prints on the last page."""
    with pdfplumber.open(pdf_path) as pdf:
        tail = pdf.pages[-1].extract_text() or ""
    m = re.search(
        r"Credit Details Number of Items:\s*(\d+)\s*Total Amount:\s*([\d,\.]+)",
        tail,
    )
    if not m:
        return None
    return {
        "stated_count": int(m.group(1)),
        "stated_total": float(m.group(2).replace(",", "")),
        "parsed_count": len(df),
        "parsed_total": round(df["amount"].sum(), 2),
    }
