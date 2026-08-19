"""
Parser for the IREPS "View Bills Status" export.

Block layout in the sheet (column D onwards):

    Contract No <..> Contract Date <..> Bill Date <..> Bill Number <..>
    Zone <..> Party Name <..> PartyCode <..>
    [Payment Advice Date to Bank <..> Accounting Unit(Division) <..>]
    CO6 No | CO6 Date | Status | Bill Amt | Passed Amt | Deducted Amt |
    Net Amt | CO7 No | CO7 Date
    <data row>
    Recovery Details : <head>          <- optional, 1..6 lines
                       <head>
    Reason For Return : <text>         <- optional, on RETURNED bills

The last two labels only appear once a payment advice has been raised,
so blocks legitimately come in two widths. Recovery and return-reason
blocks are optional and variable length.
"""

import re
from collections import OrderedDict

import openpyxl
import pandas as pd

# Order matters: each label's value runs until the next label starts.
HEADER_LABELS = [
    "Contract No",
    "Contract Date",
    "Bill Date",
    "Bill Number",
    "Zone",
    "Party Name",
    "PartyCode",
    "Payment Advice Date to Bank",
    "Accounting Unit(Division)",
]

FIELD_NAMES = {
    "Contract No": "ContractNo",
    "Contract Date": "ContractDate",
    "Bill Date": "BillDate",
    "Bill Number": "BillNumber",
    "Zone": "Zone",
    "Party Name": "PartyName",
    "PartyCode": "PartyCode",
    "Payment Advice Date to Bank": "PaymentAdviceDateToBank",
    "Accounting Unit(Division)": "AccountingUnit",
}

DATA_COLS = [
    "CO6No", "CO6Date", "Status", "BillAmt", "PassedAmt",
    "DeductedAmt", "NetAmt", "CO7No", "CO7Date",
]

FIRST_COL = 4          # column D
LAST_COL = 12          # column L
_LABEL_RE = re.compile("|".join(re.escape(l) for l in HEADER_LABELS))


def _clean(text):
    """IREPS pads everything with non-breaking spaces."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


def parse_header_line(text):
    """
    Split the block header into every label/value pair it contains.

    Driven off the label list rather than fixed positions, so a block
    missing the payment-advice fields parses fine, and anything IREPS
    adds later shows up in 'UnparsedHeader' instead of being dropped.
    """
    txt = _clean(text)
    hits = list(_LABEL_RE.finditer(txt))
    out = OrderedDict()
    consumed = []

    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(txt)
        value = txt[m.end():end].strip()
        out[FIELD_NAMES[m.group(0)]] = value or None
        consumed.append((m.start(), end))

    # Anything before the first label, or between labels we did not map.
    leftover = txt[:hits[0].start()].strip() if hits else txt
    out["UnparsedHeader"] = leftover or None
    for name in FIELD_NAMES.values():
        out.setdefault(name, None)
    return out


def _read_kv_lines(ws, start_row, max_row):
    """
    Read a 'Recovery Details' or 'Reason For Return' block.

    The label sits in column D on the first line only; the values run
    down column E until a blank. Returns the parsed items and the row
    where the block ended.
    """
    items = []
    r = start_row
    while r <= max_row:
        raw = ws.cell(row=r, column=5).value
        if raw is None or _clean(raw) == "":
            break
        text = _clean(raw).lstrip("#")
        if ":" in text:
            head, _, val = text.partition(":")
            items.append((head.strip(), val.strip()))
        else:
            items.append((None, text))
        r += 1
    return items, r


def _amount(text):
    """
    Recovery values are usually a single number, but IREPS sometimes packs
    several into one line: '1539.9, 2354.05, 9122.77'. Sum those.
    """
    if text is None:
        return None
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    nums = []
    for p in parts:
        try:
            nums.append(float(p))
        except ValueError:
            return None
    return sum(nums) if nums else None


def parse_bill_status(xlsx_path, return_recoveries=False):
    """
    Parse every bill block in the workbook.

    Returns a DataFrame with one row per bill. Set return_recoveries=True
    to also get a long-format frame of the deduction lines, one row per
    recovery head.
    """
    records = []
    recovery_rows = []
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        max_row = ws.max_row
        meta = {}
        r = 1

        while r <= max_row:
            v = ws.cell(row=r, column=FIRST_COL).value
            text = _clean(v)

            if text.startswith("Contract No"):
                meta = parse_header_line(v)
                meta["Sheet"] = sheet_name
                meta["HeaderRow"] = r

            elif text == "CO6 No":
                data_row = r + 1
                vals = [ws.cell(row=data_row, column=c).value
                        for c in range(FIRST_COL, LAST_COL + 1)]
                rec = dict(meta)
                rec.update(dict(zip(DATA_COLS, vals)))
                rec["DataRow"] = data_row

                nxt = _clean(ws.cell(row=data_row + 1, column=FIRST_COL).value)
                recoveries, reason = [], None

                if nxt.startswith("Recovery Details"):
                    recoveries, after = _read_kv_lines(ws, data_row + 1, max_row)
                    nxt2 = _clean(ws.cell(row=after, column=FIRST_COL).value)
                    if nxt2.startswith("Reason For Return"):
                        rr, _ = _read_kv_lines(ws, after, max_row)
                        reason = " ".join(x[1] for x in rr if x[1])
                elif nxt.startswith("Reason For Return"):
                    rr, _ = _read_kv_lines(ws, data_row + 1, max_row)
                    reason = " ".join(x[1] for x in rr if x[1])

                rec["Recoveries"] = {h: v for h, v in recoveries if h}
                rec["RecoveryCount"] = len(recoveries)
                rec["ReasonForReturn"] = reason
                records.append(rec)

                idx = len(records) - 1
                for head, amt in recoveries:
                    recovery_rows.append({
                        "BillIndex": idx,
                        "BillNumber": rec.get("BillNumber"),
                        "CO6No": rec.get("CO6No"),
                        "Sheet": sheet_name,
                        "RecoveryHead": head,
                        "RecoveryAmt": _amount(amt),
                        "RecoveryText": amt,
                    })
            r += 1

    df = pd.DataFrame(records)
    if df.empty:
        return (df, pd.DataFrame()) if return_recoveries else df

    for col in ["BillAmt", "PassedAmt", "DeductedAmt", "NetAmt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # '----' is the IREPS placeholder for "not issued yet"
    for col in ["CO7No", "CO7Date"]:
        df[col] = df[col].replace("----", None)

    df["CO6Date"] = pd.to_datetime(df["CO6Date"], errors="coerce")
    df["CO7Date"] = pd.to_datetime(df["CO7Date"], errors="coerce")
    for col in ["BillDate", "ContractDate", "PaymentAdviceDateToBank"]:
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")

    df["Zone"] = df["Zone"].astype(str).str.strip()
    df["Status"] = df["Status"].astype(str).str.strip()

    # Reconciliation flags
    # Net Amt is rounded to whole rupees by IREPS, so allow a rupee of slack
    df["NetCheck"] = (
        (df["PassedAmt"] - df["DeductedAmt"] - df["NetAmt"]).abs() < 1.0
    )
    rec_sum = df["Recoveries"].apply(
        lambda d: sum(_amount(v) or 0 for v in d.values()) if d else 0
    )
    df["RecoverySum"] = rec_sum
    df["RecoveryCheck"] = (df["DeductedAmt"].fillna(0) - rec_sum).abs() < 1.0

    if return_recoveries:
        return df, pd.DataFrame(recovery_rows)
    return df
