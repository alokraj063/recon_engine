"""HSBC parser: header-driven column mapping across both HSBC layouts.

The pure tests need no PDF. The real-file tests skip when the (gitignored)
documents are absent locally.
"""

import glob
from pathlib import Path

import pandas as pd
import pytest

from recon.parsers.bank_hsbc import (
    COLUMNS,
    _column_map,
    _is_data_row,
    parse_hsbc_statement,
)
from recon.sources import get_adapter
from recon.sources.base import SelfCheckError

BACKEND = Path(__file__).resolve().parents[1]
SAMPLE_DIR = BACKEND.parent / "Receipt_reconciliation_and_IDR _ Requested_sample_documents"
HSBCNET_DIR = BACKEND.parent / "01_data" / "Bank_stmt_PDF" / "_statement_attachments_only"
HSBCNET_FILE = HSBCNET_DIR / "HSBCINRHSBCINR2026-07-02-03.00.18.000763.PDF"

# Header rows exactly as pdfplumber hands them back (wrapped cells keep \n).
LEGACY_HEADER = ['Bank\nReference', 'Customer\nReference', 'Transactio\nn Type',
                 'Supplementary\ndetail', 'Transaction Narrative', 'Value\nDate',
                 'Amount', 'Date & Ti\nmeStamp']
HSBCNET_HEADER = ['Bank reference', 'Customer reference', 'Transaction type',
                  'Transaction narrative', 'Value date', 'Account currency',
                  'Transaction amount', 'Transaction time']


def _legacy_pdf():
    hits = sorted(glob.glob(str(SAMPLE_DIR / "*.PDF")) + glob.glob(str(SAMPLE_DIR / "*.pdf")))
    if not hits:
        pytest.skip("legacy sample statement not present locally")
    return hits[0]


def _hsbcnet_pdf():
    if not HSBCNET_FILE.exists():
        pytest.skip("HSBCnet transaction report not present locally")
    return str(HSBCNET_FILE)


# ---------------------------------------------------------------- pure ----

def test_column_map_legacy_header():
    m = _column_map(LEGACY_HEADER)
    assert m == {"bank_ref": 0, "customer_ref": 1, "txn_type": 2,
                 "supplementary": 3, "narrative": 4, "value_date": 5,
                 "amount": 6, "timestamp": 7}


def test_column_map_hsbcnet_header_skips_currency():
    m = _column_map(HSBCNET_HEADER)
    assert m == {"bank_ref": 0, "customer_ref": 1, "txn_type": 2,
                 "narrative": 3, "value_date": 4, "amount": 6, "timestamp": 7}
    assert "supplementary" not in m


def test_column_map_rejects_data_and_partial_rows():
    data = ['900134S4E30G', 'IN381031622', 'TFR-', 'J PAN TUBULAR', '30/06/2026',
            'INR', '-1,145,546.55', '15.42']
    assert _column_map(data) is None
    assert _column_map(['Bank reference', 'Transaction type']) is None
    assert _column_map([]) is None


def test_is_data_row_by_mapping():
    m = _column_map(HSBCNET_HEADER)
    base = ['REF', 'NONREF', 'TFR+', 'NEFT FROM X', '30/06/2026', 'INR', '1.00', '15.42']
    assert _is_data_row(base, m)
    assert _is_data_row(base[:2] + ['TFR-'] + base[3:], m)
    assert _is_data_row(base[:2] + ['TFR- TAX'] + base[3:], m)
    assert not _is_data_row(base[:2] + ['CHG'] + base[3:], m)
    assert not _is_data_row(HSBCNET_HEADER, m)
    # the exact failure that dropped every HSBCnet row under positional mapping
    legacy = _column_map(LEGACY_HEADER)
    assert not _is_data_row(base, legacy)  # legacy value_date slot holds 'INR'


# ---------------------------------------------------------- real files ----

def test_hsbcnet_report_parses():
    df = parse_hsbc_statement(_hsbcnet_pdf(), credits_only=False)
    assert list(df.columns) == COLUMNS + ["page", "zone_guess"]
    counts = df["txn_type"].value_counts().to_dict()
    assert counts["TFR+"] == 96
    assert counts["TFR-"] == 220
    assert "TFR- TAX" in counts
    credits = df[df["txn_type"] == "TFR+"]
    assert abs(credits["amount"].sum() - 261_743_702.42) < 0.01
    assert (credits["value_date"] == pd.Timestamp("2026-06-30")).all()
    assert credits["bank_ref"].notna().all() and (credits["bank_ref"] != "").all()
    assert credits["amount"].notna().all()
    assert df["supplementary"].isna().all()          # layout has no such column
    assert (df[df["txn_type"] != "TFR+"]["amount"] < 0).all()  # debits are signed
    assert credits["zone_guess"].notna().any()       # NEFT narratives still yield zones


def test_hsbcnet_report_through_adapter_no_totals_no_error():
    path = _hsbcnet_pdf()
    adapter = get_adapter("bank_statement", "hsbc")
    gold = adapter.to_gold(adapter.parse(path, {}), {})
    bank = gold["bank_txns"]
    assert int(bank["used_in_recon"].sum()) == 96
    # no printed totals on this layout -> nothing to tie, must not raise
    assert adapter.selfcheck(gold, path, {}) is None


def test_legacy_statement_unchanged():
    path = _legacy_pdf()
    df = parse_hsbc_statement(path, credits_only=False)
    assert list(df.columns) == COLUMNS + ["page", "zone_guess"]
    assert (df["txn_type"] == "TFR+").sum() == 43
    assert df["supplementary"].notna().all()
    adapter = get_adapter("bank_statement", "hsbc")
    gold = adapter.to_gold(adapter.parse(path, {}), {})
    check = adapter.selfcheck(gold, path, {})
    assert check is not None and check["parsed_count"] == check["stated_count"]


def test_adapter_selfcheck_raises_on_empty_parse(tmp_path):
    adapter = get_adapter("bank_statement", "hsbc")
    empty = pd.DataFrame(columns=COLUMNS + ["page", "zone_guess", "used_in_recon"])
    with pytest.raises(SelfCheckError):
        adapter.selfcheck({"bank_txns": empty}, tmp_path / "nothing.pdf", {})
