"""
Excel output. Nothing here decides anything; it only lays out frames the
engine already produced.
"""

from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
BANK_FILL = PatternFill("solid", fgColor="FCE4D6")
BILL_FILL = PatternFill("solid", fgColor="DDEBF7")
BODY_FONT = Font(name="Arial", size=10)
HEAD_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")


def write_workbook(out, path):
    """Four sheets: summary, matched, the combined exception queue, and
    the deduction detail."""
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        out["summary"].to_excel(xl, sheet_name="Summary", index=False)
        (out["matched"].drop(columns=["bill_indices", "candidate_indices",
                                      "candidate_gaps",
                                      "candidate_date_sources"],
                             errors="ignore")
            .to_excel(xl, sheet_name="Matched", index=False))
        # Candidates is structured (list of dicts) for the API; Excel gets
        # the flat CandidateSummary string instead.
        (out["queue"].drop(columns=["Candidates", "bill_indices",
                                    "candidate_indices", "candidate_gaps",
                                    "candidate_date_sources"],
                           errors="ignore")
            .to_excel(xl, sheet_name="Exception_Queue", index=False))
        if "recoveries" in out:
            out["recoveries"].to_excel(xl, sheet_name="Recovery_Detail", index=False)
    _format(path)
    return path


def _style_sheet(ws):
    for cell in ws[1]:
        cell.font = HEAD_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = max((len(str(c.value)) for c in col[:60] if c.value), default=8)
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 42)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = BODY_FONT


def _format(path, colour_queue=True):
    wb = load_workbook(path)
    for ws in wb.worksheets:
        _style_sheet(ws)

    # Colour the two sides of the queue so they read apart at a glance.
    if colour_queue:
        ws = wb["Exception_Queue"]
        for row in ws.iter_rows(min_row=2):
            row[0].fill = BANK_FILL if row[0].value == "BANK_ONLY" else BILL_FILL
    wb.save(path)


# Sheets composed at DOWNLOAD time from the durable ledger (item 3.3).
# Formatting only: the caller hands over plain DataFrames, this module
# knows nothing about where they came from (recon -> db never imports).
LEDGER_SHEETS = ("Manual_Matches", "Decisions", "Matches", "Exceptions")


def append_ledger_sheets(path_in, frames, path_out):
    """Copy the workbook at path_in to path_out with one extra sheet per
    entry of `frames` ({sheet_name: DataFrame}), styled like the run
    sheets. The source file is never modified. Sheet names must not
    collide with existing ones (a stored run workbook never carries a
    ledger sheet, so a stale name here is a bug, not a merge)."""
    wb = load_workbook(path_in)
    _append_frames(wb, frames)
    wb.save(path_out)
    return path_out


def write_ledger_workbook(frames, path):
    """A workbook made ONLY of ledger sheets (the customer-level export)."""
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        first = True
        for name, df in frames.items():
            df.to_excel(xl, sheet_name=name, index=False)
            first = False
        if first:   # openpyxl refuses an empty workbook
            pd.DataFrame().to_excel(xl, sheet_name="Matches", index=False)
    _format(path, colour_queue=False)
    return path


def _append_frames(wb, frames):
    for name, df in frames.items():
        if name in wb.sheetnames:
            raise ValueError(f"workbook already has a sheet named {name!r}")
        ws = wb.create_sheet(name)
        ws.append([str(c) for c in df.columns])
        for row in df.itertuples(index=False):
            ws.append([_cell(v) for v in row])
        _style_sheet(ws)


def _cell(v):
    """Excel-safe scalar: NaN/NaT -> blank, pandas timestamps -> datetime."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, (list, dict)):
        return str(v)
    return v


# --- Oracle "AR Receipt Upload" WebADI --------------------------------------
# The custom Receipt Upload ADI, laid out cell-for-cell like the template
# Oracle hands out (bneradFCC77 AR Receipt Upload): column A empty, the
# metadata block in B/D from row 4, HEADER1/2 at rows 11-12, the column
# header at row 15, its "* *List Double Click" hint row at 16, records from
# row 17 — so Finance can paste a sheet's records straight into their own
# downloaded template. Two unnamed columns sit between Adjustment Type and
# Messages in the template; they stay empty here too.
WEBADI_COLUMNS = [
    "Upl", "Operating Unit ID Selected", "Customer Name", "Receipt Method",
    "Receipt Number", "Currency", "Receipt Amount", "Receipt Date", "GL Date",
    "Invoice Number", "Receipt Amount Applied", "Adjustment Amount",
    "Factoring Amount", "Adjustment Type", None, None, "Messages",
]
WEBADI_HINTS = [
    None, "* *List Double Click", "* *List Double click", "* *Receipt Method",
    "* *Receipt Number", "* *Currency", "* *Receipt Amount", "* *Receipt Date",
    "* *GL Date", "* *Transaction Number",
    "* *Receipt Amount to be applied on Invoice",
    "* *Adjustment amount to be applied on Invoice",
    "* Factoring Amount to be applied", "* *Select the Adjustment Type",
    None, None, None,
]
WEBADI_META_KEYS = ("BATCH_ID", "RESPONSIBILITY", "OU_NAME_RESP", "USER_NAME",
                    "DATABASE", "CREATION_DATE")
_WEBADI_MONEY = {"Receipt Amount", "Receipt Amount Applied", "Adjustment Amount",
                 "Factoring Amount"}
_WEBADI_DATES = {"Receipt Date", "GL Date"}
_WEBADI_DATE_FORMAT = "dd\\-mmm\\-yyyy"      # the template's own format
_WEBADI_HEADER_ROW = 15
_WEBADI_FILL = PatternFill("solid", fgColor="DDEBF7")


def _adi_date(v):
    """The records carry the ADI's DD-Mon-YYYY text (what the preview shows);
    the template's date cells are real dates, so parse it back for the file."""
    if isinstance(v, str):
        try:
            return datetime.strptime(v.strip(), "%d-%b-%Y")
        except ValueError:
            return v
    return v


def write_webadi_workbook(sheets, path):
    """sheets: [{"name", "meta": {WEBADI_META_KEYS: value}, "records":
    [{column: value}]}] — one worksheet each. Values are written as given
    (dates arrive as the DD-Mon-YYYY text the ADI expects)."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for sheet in sheets or [{"name": "WebADI", "meta": {}, "records": []}]:
        ws = wb.create_sheet(sheet["name"][:31])
        meta = sheet.get("meta") or {}
        for i, key in enumerate(WEBADI_META_KEYS):
            ws.cell(row=4 + i, column=2, value=key)
            c = ws.cell(row=4 + i, column=4, value=(
                _adi_date(meta.get(key)) if key == "CREATION_DATE" else meta.get(key)))
            if key == "CREATION_DATE":
                c.number_format = _WEBADI_DATE_FORMAT
        ws.cell(row=11, column=2, value="HEADER1")
        ws.cell(row=11, column=4, value="* Text")
        ws.cell(row=11, column=5,
                value="This is a custom ADI for Receipt creation and application")
        ws.cell(row=12, column=2, value="HEADER2")
        ws.cell(row=12, column=4, value="* Text")
        ws.cell(row=12, column=5, value="Please enter the fields and upload the record")
        for j, (head, hint) in enumerate(zip(WEBADI_COLUMNS, WEBADI_HINTS)):
            h = ws.cell(row=_WEBADI_HEADER_ROW, column=2 + j, value=head)
            h.font = Font(name="Arial", size=10, bold=True)
            h.fill = _WEBADI_FILL
            h.alignment = Alignment(vertical="center", wrap_text=True)
            ws.cell(row=_WEBADI_HEADER_ROW + 1, column=2 + j, value=hint).font = \
                Font(name="Arial", size=9, italic=True, color="595959")
        for r, rec in enumerate(sheet.get("records") or []):
            for j, head in enumerate(WEBADI_COLUMNS):
                if head is None:
                    continue
                value = _cell(rec.get(head))
                if head in _WEBADI_DATES:
                    value = _adi_date(value)
                c = ws.cell(row=_WEBADI_HEADER_ROW + 2 + r, column=2 + j,
                            value=value)
                c.font = BODY_FONT
                if head in _WEBADI_DATES and isinstance(value, datetime):
                    c.number_format = _WEBADI_DATE_FORMAT
                if head in _WEBADI_MONEY:
                    c.number_format = "0.00"
        ws.column_dimensions["A"].width = 3
        for j, head in enumerate(WEBADI_COLUMNS):
            ws.column_dimensions[get_column_letter(2 + j)].width = (
                6 if head == "Upl" else 58 if head == "Operating Unit ID Selected"
                else 30 if head in ("Customer Name", "Receipt Method", "Adjustment Type")
                else 16 if head else 4)
        ws.freeze_panes = ws.cell(row=_WEBADI_HEADER_ROW + 2, column=3)
    wb.save(path)
    return path


# --- Daily Collection (the collections team's month-to-date sheet) ---------
# Laid out like the team's own "DAILY COLLECTION <d Mon yy>.xlsx": one sheet
# per 4-4-5 fiscal month named "Sep 26", totals in rows 2-3, the header in
# row 4, rows from 5. Column R carries the Category with no header, as in
# theirs. DAILY / TOTAL COLLECTION and the totals are FORMULAS, as in
# theirs, so a row the team adds by hand still adds up.
DAILY_COLLECTION_COLUMNS = [
    "SL.NO", "EFT DATE", "Bank", "Bank Ref", "Region", "RLY", "EFT AMOUNT",
    "BILL NO.", "Bill Date", "INVOICE AMOUNT", "Receipt No.", "Receipts Date",
    "DAILY COLLECTION", "TOTAL COLLECTION", "BRANCH", "Invoice Value",
    "OD/NOD", None, "Week",
]
_DC_ROW_KEYS = [c if c is not None else "Category" for c in DAILY_COLLECTION_COLUMNS]
_DC_MONEY = {"EFT AMOUNT", "INVOICE AMOUNT", "DAILY COLLECTION",
             "TOTAL COLLECTION", "Invoice Value"}
_DC_DATES = {"EFT DATE", "Bill Date", "Receipts Date"}
_DC_HEADER_ROW = 4
_DC_FILL = PatternFill("solid", fgColor="FFF2CC")


def _dc_value(key, v):
    if key == "BILL NO." and isinstance(v, str) and v.isdigit() and len(v) <= 15:
        return int(v)       # the team's sheet holds bill numbers as numbers
    return _cell(v)


def write_daily_collection_workbook(sheets, path):
    """sheets: [{"name": "Sep 26", "rows": [{column: value, ...}]}], rows
    in order; a row whose "DAILY COLLECTION" is set closes its day."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    col = {k: get_column_letter(i + 1) for i, k in enumerate(_DC_ROW_KEYS)}
    for sheet in sheets or [{"name": "Daily Collection", "rows": []}]:
        ws = wb.create_sheet(sheet["name"][:31])
        rows = sheet.get("rows") or []
        first, last = _DC_HEADER_ROW + 1, _DC_HEADER_ROW + max(len(rows), 1)
        for j, head in enumerate(DAILY_COLLECTION_COLUMNS):
            h = ws.cell(row=_DC_HEADER_ROW, column=j + 1, value=head)
            h.font = Font(name="Arial", size=10, bold=True)
            h.fill = _DC_FILL
            h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for key in ("EFT AMOUNT", "INVOICE AMOUNT", "DAILY COLLECTION"):
            c = col[key]
            ws[f"{c}3"] = f"=SUM({c}{first}:{c}{last})"
            ws[f"{c}3"].number_format = "#,##0.00"
            ws[f"{c}3"].font = Font(name="Arial", size=10, bold=True)
        for key in ("EFT AMOUNT", "INVOICE AMOUNT"):
            c = col[key]
            ws[f"{c}2"] = f"={c}3/1000000"     # MINR, as the team reads it
            ws[f"{c}2"].number_format = "0.00"
            ws[f"{c}2"].font = Font(name="Arial", size=10, bold=True)

        day_start, prev_day_end = first, None
        for i, rec in enumerate(rows):
            r = first + i
            for j, key in enumerate(_DC_ROW_KEYS):
                if key in ("DAILY COLLECTION", "TOTAL COLLECTION"):
                    continue
                c = ws.cell(row=r, column=j + 1, value=_dc_value(key, rec.get(key)))
                c.font = BODY_FONT
                if key in _DC_MONEY:
                    c.number_format = "#,##0.00"
                elif key in _DC_DATES:
                    c.number_format = "dd-mm-yyyy"
            if rec.get("DAILY COLLECTION") is not None:
                m, n = col["DAILY COLLECTION"], col["TOTAL COLLECTION"]
                ws[f"{m}{r}"] = f"=SUM({col['INVOICE AMOUNT']}{day_start}:{col['INVOICE AMOUNT']}{r})"
                ws[f"{n}{r}"] = f"={m}{r}" + (f"+{n}{prev_day_end}" if prev_day_end else "")
                for cell in (ws[f"{m}{r}"], ws[f"{n}{r}"]):
                    cell.number_format = "#,##0.00"
                    cell.font = Font(name="Arial", size=10, bold=True)
                prev_day_end, day_start = r, r + 1

        widths = {"SL.NO": 7, "EFT DATE": 12, "Bank": 7, "Bank Ref": 26,
                  "Region": 9, "RLY": 8, "BILL NO.": 16, "Bill Date": 12,
                  "Receipt No.": 11, "Receipts Date": 12, "BRANCH": 9,
                  "OD/NOD": 8, "Category": 11, "Week": 10}
        for key, letter in col.items():
            ws.column_dimensions[letter].width = widths.get(key, 16)
        ws.freeze_panes = ws.cell(row=_DC_HEADER_ROW + 1, column=1)
    wb.save(path)
    return path
