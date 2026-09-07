"""
RNOTE / CRN loaders accept legacy .xls (BIFF) alongside .xlsx/.xlsm.

load_rnote/load_crn already go through pandas.read_excel, which picks
its engine (openpyxl vs xlrd) from the file itself — no parser code
changed for this. These tests exist to prove that generic dispatch
actually works end to end for both reports, and that the adapters'
file_kinds (the thing that gates uploads) were updated to allow it.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest
import xlwt

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.parsers import load_crn, load_rnote  # noqa: E402
from recon.sources import get_adapter  # noqa: E402

RNOTE_ROW = {
    "Dept / Rly. Unit": "ICF", "PO No.": "PO123", "PO Date": "01/01/2026",
    "PO_SR": "1", "RNOTE No.": "RN1", "RNOTE Date": "05/01/2026",
    "RNOTE Qty": "10", "RO No.": "RO1", "RO Date": "06/01/2026",
    "DRR No.": "DRR1", "Bill Claim": "BC1", "Bill Reg No.": "BR1",
    "Bill Reg/Sign Date": "07/01/2026", "Invoice No.": "INV1",
    "Invoice Date": "02/01/2026", "CO6 No.": "CO6-1", "CO6 Date": "08/01/2026",
    "CO7 No.": "CO7-1", "CO7 Date": "09/01/2026", "Claim Amount": "1000",
    "Passed Amount": "990", "Payment / Return Date": "10/01/2026",
    "Return Reason": "",
}

CRN_ROW = {
    "Rly": "ICF", "PO No.": "PO456", "PO Date": "01/02/2026",
    "PO Sr": "1", "Challan No.": "CH1", "Challan Date": "05/02/2026",
    "CRN TypeClaim No.": "TC1", "CRN No.": "CRN1", "CRN date": "06/02/2026",
    "Approval Date": "07/02/2026", "CRN Qty": "5", "Bill Claim": "BC2",
    "Bill Reg No.": "BR2", "Bill Reg/Sign Date": "08/02/2026",
    "Invoice No.": "INV2", "Invoice Date": "02/02/2026", "CO6 No.": "CO6-2",
    "CO6 Date": "09/02/2026", "CO7 No.": "CO7-2", "CO7 Date": "10/02/2026",
    "Claim Amount": "2000", "Passed Amount": "1990",
    "Payment / Return Date": "11/02/2026", "Return Reason": "",
}


def _xlsx(tmp_path, name, row):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = list(row)
    ws.append(headers)
    ws.append([row[h] for h in headers])
    path = tmp_path / name
    wb.save(path)
    return path


def _xls(tmp_path, name, row):
    wb = xlwt.Workbook()
    ws = wb.add_sheet("s")
    headers = list(row)
    for c, h in enumerate(headers):
        ws.write(0, c, h)
    for c, h in enumerate(headers):
        ws.write(1, c, row[h])
    path = tmp_path / name
    wb.save(path)
    return path


def test_rnote_xls_is_parsed(tmp_path):
    path = _xls(tmp_path, "rnote.xls", RNOTE_ROW)
    df = load_rnote(path)
    assert len(df) == 1
    assert df.iloc[0]["RNoteNo"] == "RN1"
    assert df.iloc[0]["InvoiceNo"] == "INV1"
    assert df.iloc[0]["LineageSource"] == "RNOTE"


def test_crn_xls_is_parsed(tmp_path):
    path = _xls(tmp_path, "crn.xls", CRN_ROW)
    df = load_crn(path)
    assert len(df) == 1
    assert df.iloc[0]["CRNNo"] == "CRN1"
    assert df.iloc[0]["InvoiceNo"] == "INV2"
    assert df.iloc[0]["LineageSource"] == "CRN"


def test_rnote_xls_and_xlsx_produce_identical_records(tmp_path):
    from_xlsx = load_rnote(_xlsx(tmp_path, "a.xlsx", RNOTE_ROW))
    from_xls = load_rnote(_xls(tmp_path, "a.xls", RNOTE_ROW))
    pd.testing.assert_frame_equal(from_xlsx, from_xls)


def test_crn_xls_and_xlsx_produce_identical_records(tmp_path):
    from_xlsx = load_crn(_xlsx(tmp_path, "a.xlsx", CRN_ROW))
    from_xls = load_crn(_xls(tmp_path, "a.xls", CRN_ROW))
    pd.testing.assert_frame_equal(from_xlsx, from_xls)


@pytest.mark.parametrize("source_type,adapter_key", [
    ("lineage_rnote", "ireps_rnote"),
    ("lineage_crn", "ireps_crn"),
])
def test_lineage_adapters_accept_xls(source_type, adapter_key):
    assert ".xls" in get_adapter(source_type, adapter_key).file_kinds


def test_rnote_adapter_parses_xls(tmp_path):
    path = _xls(tmp_path, "rnote.xls", RNOTE_ROW)
    adapter = get_adapter("lineage_rnote", "ireps_rnote")
    silver = adapter.parse(path, {})
    gold = adapter.to_gold(silver, {})
    assert gold["lineage_rnote"].iloc[0]["doc_type"] == "RNOTE"


def test_crn_adapter_parses_xls(tmp_path):
    path = _xls(tmp_path, "crn.xls", CRN_ROW)
    adapter = get_adapter("lineage_crn", "ireps_crn")
    silver = adapter.parse(path, {})
    gold = adapter.to_gold(silver, {})
    assert gold["lineage_crn"].iloc[0]["doc_type"] == "CRN"
