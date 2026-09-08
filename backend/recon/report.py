"""
Excel output. Nothing here decides anything; it only lays out frames the
engine already produced.
"""

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
        (out["matched"].drop(columns=["bill_indices", "candidate_indices"],
                             errors="ignore")
            .to_excel(xl, sheet_name="Matched", index=False))
        # Candidates is structured (list of dicts) for the API; Excel gets
        # the flat CandidateSummary string instead.
        (out["queue"].drop(columns=["Candidates", "bill_indices",
                                    "candidate_indices"], errors="ignore")
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
