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


# Coalesce priority when several doc types attach to one bill: earlier
# wins per column. Doc types not listed follow in order of first
# appearance in the lineage frame.
LINEAGE_DOC_PRIORITY = ("RNOTE", "CRN")

# Canonical lineage value column -> the trail column it feeds on
# bills_enriched. Trail names (PO, Receipt_Doc, ...) are frozen
# run-artifact vocabulary.
TRAIL_MAP = [
    ("PO", "po_no"),
    ("PO_Date", "po_date"),
    ("Receipt_Doc", "doc_no"),
    ("Receipt_Date", "doc_date"),
    ("Receipt_Qty", "receipt_qty"),
    ("DRR_or_Challan", "drr_or_challan_no"),
    ("Bill_Reg_No", "bill_reg_no"),
    ("Invoice_Date", "invoice_date"),
    ("Bill_Reg_Date", "bill_reg_date"),
]
TRAIL_COLS = [t[0] for t in TRAIL_MAP]

# The bill->lineage join, in fallback order. The MatchedVia labels are
# FROZEN legacy literals (golden CSVs, persisted payloads, frontend):
# they mean "matched via bill number / submission ref / payment order
# ref" whatever the source system calls those documents.
_JOIN_KEYS = [
    ("bill_number", "invoice_no", "InvoiceNo"),
    ("submission_ref", "submission_ref", "CO6No"),
    ("payment_order_ref", "payment_order_ref", "CO7No"),
]


def _doc_type_order(lineage):
    seen = list(dict.fromkeys(t for t in lineage["doc_type"].tolist()
                              if t is not None and not pd.isna(t)))
    ordered = [t for t in LINEAGE_DOC_PRIORITY if t in seen]
    return ordered + [t for t in seen if t not in ordered]


def attach_lineage(bills, lineage=None):
    """
    Join upstream lineage documents (the canonical unified frame — one
    row per doc, `doc_type` discriminating RNOTE / CRN / any future
    source's documents) onto a bill frame, producing the trail columns
    (TRAIL_MAP), one `{doc_type}_MatchedVia` column per attached type,
    and `LineageStatus`.

    Tries the bill number first (that is the invoice number upstream),
    then the submission ref, then the payment order ref. A bill can
    legitimately appear in no document, in which case the trail stays
    blank and LineageStatus says so.
    """
    out = bills.copy()
    bill_keys = {lineage_col: _key(out[bill_col])
                 for bill_col, lineage_col, _label in _JOIN_KEYS}

    value_cols = [src for _, src in TRAIL_MAP]
    trail = {dst: pd.Series(pd.NA, index=out.index, dtype="object")
             for dst, _src in TRAIL_MAP}
    has = {}

    if lineage is not None and not lineage.empty:
        for doc_type in _doc_type_order(lineage):
            sub = lineage[lineage["doc_type"] == doc_type]
            pick = [c for c in value_cols if c in sub.columns]
            if not pick:
                continue
            base = sub[pick]

            # legacy parity: the FIRST key's lut keeps NA-keyed rows
            # (historical behaviour), the fallback keys drop them
            luts = []
            for i, (_bill_col, lin_col, label) in enumerate(_JOIN_KEYS):
                if lin_col not in sub.columns:
                    continue
                keyed = base.assign(_k=_key(sub[lin_col]))
                if i > 0:
                    keyed = keyed.dropna(subset=["_k"])
                keyed = keyed.drop_duplicates("_k")
                luts.append((lin_col, keyed.set_index("_k")[pick], label))
            if not luts:
                continue

            first_col, first_lut, first_label = luts[0]
            joined = (bill_keys[first_col].to_frame("k")
                      .join(first_lut, on="k")[pick])
            via = pd.Series(pd.NA, index=out.index, dtype="object")
            via[joined.notna().any(axis=1)] = first_label

            for lin_col, lut, label in luts[1:]:
                miss = joined.isna().all(axis=1)
                if not miss.any():
                    break
                alt = (bill_keys[lin_col].loc[miss].to_frame("k")
                       .join(lut, on="k")[pick])
                joined.loc[miss] = alt
                via.loc[miss & joined.notna().any(axis=1)] = label

            out[f"{doc_type}_MatchedVia"] = via
            has[doc_type] = (joined["doc_no"].notna()
                             if "doc_no" in joined.columns
                             else pd.Series(False, index=out.index))
            for dst, src in TRAIL_MAP:
                if src in joined.columns:
                    trail[dst] = trail[dst].fillna(joined[src])

    for dst in TRAIL_COLS:
        out[dst] = trail[dst]

    if has:
        ordered = [t for t in _doc_type_order(lineage) if t in has]
        flags = pd.DataFrame({t: has[t] for t in ordered}, index=out.index)
        out["LineageStatus"] = flags.apply(
            lambda row: "+".join(t for t in ordered if row[t])
            or "NO_UPSTREAM_DOC", axis=1)
    else:
        out["LineageStatus"] = "NO_UPSTREAM_DOC"

    return out
