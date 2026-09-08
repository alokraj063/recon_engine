"""
HSBC bank statement parser, cell-based.

Two HSBC exports are read by this one parser:

* the legacy "Daily statement" PDF (columns: Bank Reference, Customer
  Reference, Transaction Type, Supplementary detail, Transaction Narrative,
  Value Date, Amount, Date & TimeStamp; prints a "Credit Details Number of
  Items / Total Amount" footer on the last page), and
* the current HSBCnet transaction report (``HSBCINR...PDF``; columns: Bank
  reference, Customer reference, Transaction type, Transaction narrative,
  Value date, Account currency, Transaction amount, Transaction time). It has
  no Supplementary column, no printed totals, debits carry a leading minus
  and the time is a bare ``HH.MM``. Besides ``TFR+``/``TFR-`` it also lists
  ``TFR- TAX`` rows (bank charges).

Both are real ruled tables, so pdfplumber pulls them out cell by cell. The
two layouts are the SAME width, so cells are located by HEADER TEXT
(``HEADER_ALIASES``), never by position. A cell that wraps over several
visual lines comes back as one string with \\n inside it, which we flatten.
The output frame keeps the legacy column names and order regardless of
which layout was read; a column the layout lacks comes back as None.
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

# Normalised header text (lowercase, letters only) -> output column. Both
# layouts' headers are listed; "accountcurrency" is recognised so it counts
# as a header cell but maps to nothing (always INR, carries no information).
HEADER_ALIASES = {
    "bankreference": "bank_ref",
    "customerreference": "customer_ref",
    "transactiontype": "txn_type",
    "supplementarydetail": "supplementary",
    "transactionnarrative": "narrative",
    "valuedate": "value_date",
    "amount": "amount",
    "transactionamount": "amount",
    "datetimestamp": "timestamp",
    "transactiontime": "timestamp",
    "accountcurrency": None,
}

# A row only counts as the header when it names at least these.
REQUIRED_HEADERS = {"bank_ref", "txn_type", "narrative", "value_date", "amount"}

# pdfplumber finds the table from the drawn borders. "text" as the fallback
# vertical strategy keeps it working if a page loses its ruling lines.
TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 5,
    "join_tolerance": 5,
    "snap_tolerance": 3,
}

# The legacy layout, used only when no header row has been seen yet.
_LEGACY_MAP = {name: i for i, name in enumerate(COLUMNS)}


def _flatten(cell):
    """Turn a wrapped cell into one clean string."""
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", cell.replace("\n", " ")).strip()


def _norm_header(text):
    """'Date & Ti\\nmeStamp' -> 'datetimestamp'; letters only, lowercase."""
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _column_map(row):
    """
    Read a header row into {column_name: cell_index}.

    Returns None unless the row names every REQUIRED_HEADERS column, so a
    data row can never be mistaken for a header. Header cells that map to
    nothing (Account currency) are accepted and skipped.
    """
    mapping = {}
    for i, cell in enumerate(row):
        key = _norm_header(cell)
        if key not in HEADER_ALIASES:
            continue
        name = HEADER_ALIASES[key]
        if name is not None and name not in mapping:
            mapping[name] = i
    if not REQUIRED_HEADERS.issubset(mapping):
        return None
    return mapping


def _cell(row, colmap, name):
    """The flattened text of column `name`, '' when the layout lacks it or
    the row is too short."""
    i = colmap.get(name)
    if i is None or i >= len(row):
        return ""
    return row[i]


def _is_data_row(row, colmap):
    """Header, balance-brought-forward and footer rows all fail this."""
    if not _cell(row, colmap, "txn_type").startswith(("TFR+", "TFR-")):
        return False
    return bool(re.fullmatch(r"\d{2}/\d{2}/\d{4}", _cell(row, colmap, "value_date")))


def _to_amount(text):
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_hsbc_statement(pdf_path, credits_only=True):
    """
    Read every transaction row from an HSBC statement / transaction report.

    credits_only=True keeps TFR+ rows. Set it False to get debits too.
    Returns a DataFrame with one row per transaction, whatever the
    original row height was in the PDF.
    """
    records = []
    colmap = None  # last header seen; carried across tables and pages

    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables(TABLE_SETTINGS):
                for raw_row in table:
                    row = [_flatten(c) for c in raw_row]

                    header = _column_map(row)
                    if header is not None:
                        colmap = header
                        continue

                    if colmap is not None:
                        use = colmap
                    elif len(row) == len(COLUMNS):
                        use = _LEGACY_MAP  # no header seen yet: legacy positions
                    else:
                        continue
                    if not _is_data_row(row, use):
                        continue

                    rec = {
                        name: (_cell(row, use, name) if name in use else None)
                        for name in COLUMNS
                    }
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


# Railway zone codes that show up in the NEFT narratives (longer first so
# NER is never read as ER, SECR never as SCR/ER).
ZONE_CODES = [
    "NWR", "NER", "NFR", "NCR", "ECR", "ECOR", "WCR", "SECR", "SWR",
    "SCR", "SER", "SR", "CR", "ER", "WR", "NR",
]
# spellings as they actually appear glued into narratives — matched
# case-SENSITIVELY so a lowercase description word (Erection, Set…) can
# never be read as a zone; the extracted value is uppercased (ECOR/SCOR),
# matching how the bills side spells them after norm_text
ZONE_SPELLINGS = sorted(ZONE_CODES + ["ECoR", "SCoR"], key=len, reverse=True)
# Production units pay through IREPS too and the bills carry the unit as
# `zone` (ICF, RCF, CLW, MCF, BLW, PLW, MTPK seen in gold); in the
# narrative the unit code is glued straight after the 4-digit prefix with
# no station mnemonic: "1101CLWHIGH REACH…", "2001MCFaxle…", "RCFLHB…"
PRODUCTION_UNITS = ["MTPK", "ICF", "RCF", "MCF", "CLW", "BLW", "DLW", "DMW",
                    "PLW", "RWF"]

_ZONE_ALT = "|".join(ZONE_SPELLINGS)
# Rule A — zonal railway: "<4-digit unit><STATION> <ZONE><description>"
#   e.g. "3003DHN ECRSET OF…", "3712CTPTY SCoRSet of…", "HQ CRSet…"
_RULE_A = re.compile(r"^(?:\d{4})?[A-Z]{1,6}\s+(" + _ZONE_ALT + r")")
# Rule B — production unit: "<4-digit code><UNIT><description>", no station
#   e.g. "1101CLWHIGH REACH…", "1301ICF1331000197…", "RCFLHB…"
_RULE_B = re.compile(r"^(?:\d{4})?(" + "|".join(PRODUCTION_UNITS)
                     + r")(?=[A-Za-z0-9 ]|$)")
# Rule C — the historical unanchored search, kept VERBATIM as the last
# resort so every narrative the old logic matched (and A/B do not) keeps
# its value — golden parity (tests/test_zone_extraction.py proves it on
# the sample statement). Only reached when A and B both miss, which is
# what retires its known false positive ("…SCREW…" read as SCR).
_ZONE_RE = re.compile(r"\b(" + "|".join(ZONE_CODES) + r")")


def extract_zone_from_narrative(narrative):
    """
    Pull the paying railway zone / production unit out of a NEFT narrative.

    IREPS payments carry the payer as a prefix after "NEFT FROM":
      zonal railway    "<4-digit unit><STATION> <ZONE><description…>"
                       '0406GKPW NER4th Bill of A…'  -> NER
                       '3712CTPTY SCoRSet of WSP…'   -> SCOR
      production unit  "<4-digit code><UNIT><description…>" (no station)
                       '2001MCFaxle mounted disc…'    -> MCF
                       '1301ICF1331000197 90 Sup…'   -> ICF
    Rules are tried in that order (both anchored at the head), then the
    historical unanchored search as a fallback. Anything else — deposit
    interest, customs drawback, ordinary vendors — stays None, which the
    matcher treats as "zone unconfirmed", never as a match.
    """
    if not narrative:
        return None
    neft = re.match(r"^NEFT FROM\s+(.*)$", narrative, re.S)
    body = neft.group(1) if neft else narrative
    if neft:
        # the anchored rules describe the IREPS payer prefix, which only
        # exists on NEFT credits — a vendor whose NAME starts with a unit
        # code ("DMW CNC SOLUTIONS…") must not be read as that unit
        m = _RULE_A.match(body)
        if m:
            return m.group(1).upper()
        m = _RULE_B.match(body)
        if m:
            return m.group(1)
    body = re.sub(r"^[0-9]{4}[A-Z]+\s*", "", body)
    m = _ZONE_RE.search(body)
    return m.group(1) if m else None


def bank_selfcheck(df, pdf_path):
    """Sanity check against the totals HSBC prints on the last page.

    Only the legacy Daily statement prints them; the HSBCnet transaction
    report has no footer, so this returns None for it (no control totals)."""
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
