"""
RNOTE and CRN loaders. These two IREPS reports carry the upstream trail
for a bill: purchase order, receipt note or challan, and the CO6/CO7
numbers, so a matched payment can be traced back to the PO it came from.
"""

import pandas as pd

# Both reports key back to Bill Status on the same three fields.
JOIN_KEYS = ["InvoiceNo", "CO6No", "CO7No"]


def _key(series):
    """
    IREPS writes these numbers as int in one export and str in another.
    Normalise to a plain digit string so joins actually hit.
    """
    num = pd.to_numeric(series, errors="coerce")
    out = num.astype("Int64").astype(str)
    out = out.where(num.notna(), series.astype(str).str.strip())
    return out.replace({"<NA>": None, "nan": None, "None": None, "": None})


def _tidy_ids(df, cols):
    """Keep long IREPS numbers as text, otherwise pandas renders them in
    scientific notation and they stop being usable as references."""
    for c in cols:
        if c in df.columns:
            df[c] = _key(df[c])
    return df


def load_rnote(path, sheet=0):
    df = pd.read_excel(path, sheet_name=sheet)
    df = df.rename(columns={
        "Dept / Rly. Unit": "RN_Zone",
        "PO No.": "RN_PONo",
        "PO Date": "RN_PODate",
        "PO_SR": "RN_POSr",
        "RNOTE No.": "RNoteNo",
        "RNOTE Date": "RNoteDate",
        "RNOTE Qty": "RNoteQty",
        "RO No.": "RN_RONo",
        "RO Date": "RN_RODate",
        "DRR No.": "RN_DRRNo",
        "Bill Claim": "RN_BillClaim",
        "Bill Reg No.": "RN_BillRegNo",
        "Bill Reg/Sign Date": "RN_BillRegDate",
        "Invoice No.": "InvoiceNo",
        "Invoice Date": "RN_InvoiceDate",
        "CO6 No.": "CO6No",
        "CO6 Date": "RN_CO6Date",
        "CO7 No.": "CO7No",
        "CO7 Date": "RN_CO7Date",
        "Claim Amount": "RN_ClaimAmt",
        "Passed Amount": "RN_PassedAmt",
        "Payment / Return Date": "RN_PayReturnDate",
        "Return Reason": "RN_ReturnReason",
    })
    for k in JOIN_KEYS:
        df[k] = _key(df[k])
    df["LineageSource"] = "RNOTE"
    return _tidy_ids(df, ["RNoteNo", "RN_RONo", "RN_BillRegNo", "RN_PONo"])


def load_crn(path, sheet=0):
    df = pd.read_excel(path, sheet_name=sheet)
    df = df.rename(columns={
        "Rly": "CR_Zone",
        "PO No.": "CR_PONo",
        "PO Date": "CR_PODate",
        "PO Sr": "CR_POSr",
        "Challan No.": "CR_ChallanNo",
        "Challan Date": "CR_ChallanDate",
        "CRN TypeClaim No.": "CR_TypeClaim",
        "CRN No.": "CRNNo",
        "CRN date": "CRNDate",
        "Approval Date": "CR_ApprovalDate",
        "CRN Qty": "CR_Qty",
        "Bill Claim": "CR_BillClaim",
        "Bill Reg No.": "CR_BillRegNo",
        "Bill Reg/Sign Date": "CR_BillRegDate",
        "Invoice No.": "InvoiceNo",
        "Invoice Date": "CR_InvoiceDate",
        "CO6 No.": "CO6No",
        "CO6 Date": "CR_CO6Date",
        "CO7 No.": "CO7No",
        "CO7 Date": "CR_CO7Date",
        "Claim Amount": "CR_ClaimAmt",
        "Passed Amount": "CR_PassedAmt",
        "Payment / Return Date": "CR_PayReturnDate",
        "Return Reason": "CR_ReturnReason",
    })
    for k in JOIN_KEYS:
        df[k] = _key(df[k])
    df["LineageSource"] = "CRN"
    return _tidy_ids(df, ["CR_BillRegNo", "CR_ChallanNo", "CR_PONo"])


def attach_lineage(bills, rnote=None, crn=None):
    """
    Add RNOTE and CRN columns to a bill frame.

    Tries Invoice No first (that is the bill number), then CO6, then CO7.
    A bill can legitimately appear in neither report, in which case the
    lineage columns stay blank and LineageStatus says so.
    """
    out = bills.copy()
    out["_inv"] = _key(out["BillNumber"])
    out["_co6"] = _key(out["CO6No"])
    out["_co7"] = _key(out["CO7No"])

    for src, cols in (("RNOTE", rnote), ("CRN", crn)):
        if cols is None or cols.empty:
            continue
        pick = [c for c in cols.columns
                if c.startswith(("RN_", "CR_")) or c in
                ("RNoteNo", "RNoteDate", "RNoteQty", "CRNNo", "CRNDate")]
        lut = cols.drop_duplicates("InvoiceNo").set_index("InvoiceNo")[pick]
        lut6 = cols.dropna(subset=["CO6No"]).drop_duplicates("CO6No").set_index("CO6No")[pick]
        lut7 = cols.dropna(subset=["CO7No"]).drop_duplicates("CO7No").set_index("CO7No")[pick]

        joined = out["_inv"].map(lambda k: k).to_frame("k").join(lut, on="k")[pick]
        via = pd.Series(pd.NA, index=out.index, dtype="object")
        via[joined.notna().any(axis=1)] = "InvoiceNo"

        for keycol, table, label in (("_co6", lut6, "CO6No"), ("_co7", lut7, "CO7No")):
            miss = joined.isna().all(axis=1)
            if not miss.any():
                break
            alt = out.loc[miss, keycol].to_frame("k").join(table, on="k")[pick]
            joined.loc[miss] = alt
            via.loc[miss & joined.notna().any(axis=1)] = label

        out = out.join(joined)
        out[f"{src}_MatchedVia"] = via

    has_rn = out.get("RNoteNo", pd.Series(pd.NA, index=out.index)).notna()
    has_cr = out.get("CRNNo", pd.Series(pd.NA, index=out.index)).notna()
    out["LineageStatus"] = "NO_UPSTREAM_DOC"
    out.loc[has_cr, "LineageStatus"] = "CRN"
    out.loc[has_rn, "LineageStatus"] = "RNOTE"
    out.loc[has_rn & has_cr, "LineageStatus"] = "RNOTE+CRN"

    return out.drop(columns=["_inv", "_co6", "_co7"])
