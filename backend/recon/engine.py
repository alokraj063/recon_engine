"""
Two-sided reconciliation.

Bank statement and Bill Status are both sources of truth, so exceptions
run both ways:

    MATCHED    credit and bill agree
    BANK_ONLY  money in the account with no bill behind it
    BILL_ONLY  bill IREPS advised, with no credit against it

Everything carries the upstream trail from RNOTE / CRN where it exists,
so a reviewer can walk PO -> receipt note or challan -> invoice -> CO6 ->
CO7 -> payment advice -> bank reference on one row.
"""

import pandas as pd

from .config import ReconConfig
from .matching import match_bank_to_billstatus, results_to_frame
from .parsers import (
    parse_hsbc_statement, bank_selfcheck, parse_bill_status,
    load_rnote, load_crn, attach_lineage,
)

# Provenance chain, in the order the document is actually raised. Each
# entry is the output column and the RNOTE / CRN columns to coalesce.
TRAIL = [
    ("PO", ["RN_PONo", "CR_PONo"]),
    ("PO_Date", ["RN_PODate", "CR_PODate"]),
    ("Receipt_Doc", ["RNoteNo", "CRNNo"]),
    ("Receipt_Date", ["RNoteDate", "CRNDate"]),
    ("Receipt_Qty", ["RNoteQty", "CR_Qty"]),
    ("DRR_or_Challan", ["RN_DRRNo", "CR_ChallanNo"]),
    ("Bill_Reg_No", ["RN_BillRegNo", "CR_BillRegNo"]),
]

MATCH_SIDE_COLS = [
    "BillDate", "ContractDate", "PartyCode", "AccountingUnit", "CO6Date",
    "BillAmt", "PassedAmt", "DeductedAmt", "Recoveries", "RecoveryCount",
    "ReasonForReturn", "LineageStatus", "RNOTE_MatchedVia", "CRN_MatchedVia",
]

EXCEPTION_COLS = [
    "exception_type", "action", "BillNumber", "ContractNo", "Zone",
    "AccountingUnit", "Status", "ExpectedBasis", "BillDate", "CO6No",
    "CO6Date", "CO7No", "CO7Date", "PaymentAdviceDateToBank",
    "BillAmt", "PassedAmt", "DeductedAmt", "NetAmt", "RecoveryCount",
    "ReasonForReturn", "LineageStatus", "PO", "PO_Date", "Receipt_Doc",
    "Receipt_Date", "Receipt_Qty", "DRR_or_Challan", "Bill_Reg_No",
    "Sheet", "DataRow",
]

BANK_ACTIONS = {
    "ZONE_BILL_NOT_FOUND":
        "Zone identified from narrative but no bill in the export. Check "
        "whether the bill sits under a different IREPS module or a later "
        "export.",
    "NON_IREPS_OR_UNRECOGNISED":
        "No railway zone in the narrative. Likely intercompany, metro, "
        "customs or a direct customer receipt. Route to the relevant "
        "sub-ledger.",
}

# Matches that stand but need a human eye. They stay in the matched frame
# (bills remain claimed, totals unchanged) and are copied into the
# exception queue with the evidence behind the doubt.
REVIEW_CONFIDENCE = {"AMBIGUOUS", "LOW", "AMOUNT_ONLY", "BATCHED"}

REVIEW_ACTIONS = {
    "AMBIGUOUS":
        "Several bills share this amount and score identically; the pick "
        "was arbitrary. Compare the candidate bills listed and confirm "
        "which one this credit settles.",
    "LOW":
        "Amount matched but only one of zone / date agreed. Check the "
        "candidate bill's detail before accepting the match.",
    "AMOUNT_ONLY":
        "Amount matched but neither zone nor advice date agreed. Treat as "
        "a guess; verify against the candidate bill before posting.",
    "BATCHED":
        "One credit covers several bills whose Net Amts sum to it. Verify "
        "every covered bill before posting.",
}

# Fields shown per candidate bill in a MATCH_REVIEW row, in display order.
CANDIDATE_FIELDS = [
    "BillNumber", "ContractNo", "Zone", "Status",
    "BillAmt", "PassedAmt", "DeductedAmt", "NetAmt",
    "CO6No", "CO6Date", "CO7No", "CO7Date", "PaymentAdviceDateToBank",
    "AccountingUnit", "LineageStatus", "PO", "PO_Date", "Receipt_Doc",
    "Receipt_Date", "DRR_or_Challan", "Bill_Reg_No", "ReasonForReturn",
    "Sheet", "DataRow",
]

BILL_ACTIONS = {
    "ADVICE_DATE":
        "IREPS advised the bank but no credit landed. Chase the railway or "
        "check the next statement.",
    "CO7_ISSUED_NO_ADVICE":
        "CO7 raised, advice not yet issued. Expected to settle in a later "
        "statement, monitor only.",
    "ZERO_NET_NOTHING_DUE":
        "Net payable is nil after deductions. No credit expected, close "
        "without action.",
}


def _coalesce(df, cols):
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    for c in cols:
        if c in df.columns:
            out = out.fillna(df[c])
    return out


def build_trail(df):
    """Flatten the RNOTE and CRN columns into one document chain."""
    for name, srcs in TRAIL:
        df[name] = _coalesce(df, srcs)
    return df


def _expected_bills(bills, bank_df, window_days, co7_lookback_days):
    """
    Which bills could plausibly have been paid in this statement.

    Not all of them. Money cannot arrive before the advice goes out, so
    the default window is zero days either side of the statement dates.
    Widen it and you pull in bills that settled earlier and report them
    as false shortfalls.
    """
    stmt_lo = pd.to_datetime(bank_df["value_date"]).min()
    stmt_hi = pd.to_datetime(bank_df["value_date"]).max()
    lo = stmt_lo - pd.Timedelta(days=window_days)
    hi = stmt_hi + pd.Timedelta(days=window_days)

    advised = bills["PaymentAdviceDateToBank"].between(lo, hi)
    co7_due = (
        (bills["Status"] == "CO7 DONE")
        & bills["CO7Date"].between(stmt_lo - pd.Timedelta(days=co7_lookback_days), hi)
    )

    expected = bills[advised | co7_due].copy()
    expected["ExpectedBasis"] = "CO7_ISSUED_NO_ADVICE"
    expected.loc[advised.reindex(expected.index, fill_value=False),
                 "ExpectedBasis"] = "ADVICE_DATE"
    # Nothing payable means its absence is not a shortfall.
    expected.loc[expected["NetAmt"].fillna(0) == 0,
                 "ExpectedBasis"] = "ZERO_NET_NOTHING_DUE"
    return expected


def _candidate_details(indices, bills, picked):
    """One plain dict per candidate bill, from the enriched frame so the
    RNOTE / CRN trail comes along. `Picked` marks the matcher's choice."""
    out = []
    for i in indices:
        row = bills.loc[i]
        d = {f: row.get(f) for f in CANDIDATE_FIELDS if f in bills.columns}
        d["Picked"] = bool(i == picked)
        out.append(d)
    return out


def _candidate_summary(cands):
    """Excel-friendly one-liner; the structured column can't be written."""
    def one(c):
        adv = c.get("PaymentAdviceDateToBank")
        adv = adv.date().isoformat() if pd.notna(adv) and hasattr(adv, "date") else "no advice"
        mark = "*" if c.get("Picked") else ""
        return f"{mark}{c.get('BillNumber')} ({c.get('Zone')}, {adv})"
    return f"{len(cands)} candidate(s): " + " | ".join(one(c) for c in cands)


def _match_review(matched, bills):
    """The weak matches, as exception rows carrying their evidence."""
    if matched.empty:
        return matched.iloc[0:0].copy()
    review = matched[matched["confidence"].isin(REVIEW_CONFIDENCE)].copy()
    if review.empty:
        return review
    review["exception_type"] = "MATCH_REVIEW"
    review["action"] = review["confidence"].map(REVIEW_ACTIONS)
    review["Candidates"] = [
        _candidate_details(r.candidate_indices, bills,
                           r.bill_indices[0] if r.bill_indices else None)
        for r in review.itertuples()
    ]
    review["CandidateSummary"] = review["Candidates"].apply(_candidate_summary)
    return review


# Keys that mean "no bill number recorded" — rows carrying these must
# never be merged with each other just because the key is equally empty.
UNGROUPABLE_KEYS = {"", "-", "----", "nan", "None"}

ATTEMPT_FIELDS = ["Status", "CO6No", "CO6Date", "CO7No", "CO7Date", "NetAmt",
                  "PaymentAdviceDateToBank", "ReasonForReturn", "Sheet", "DataRow"]


def group_bill_attempts(bills):
    """
    One row per bill instead of one per processing attempt.

    IREPS exports a fresh block (new CO6) every time a returned bill is
    resubmitted, so a bounced bill appears several times. The combined row
    is the attempt that actually got settled if any did (the money decides),
    otherwise the chronologically latest; the full journey rides along in
    `Attempts`. Display-only — matching always uses the ungrouped frame.
    """
    key = bills["BillNumber"].astype(str).str.strip()
    groupable = ~(key.isin(UNGROUPABLE_KEYS) | bills["BillNumber"].isna())

    # chronological within a bill: CO6Date ascending (NaT last); in this
    # export a smaller DataRow is more recent, so it breaks ties descending
    order = bills.assign(_key=key, _neg_row=-bills["DataRow"]) \
                 .sort_values(["CO6Date", "_neg_row"], na_position="last")

    rows = []
    for _, grp in order[groupable].groupby("_key", sort=False):
        attempts = grp.drop(columns=["_key", "_neg_row"])
        if attempts["SettledInStatement"].any():
            rep = attempts[attempts["SettledInStatement"]].iloc[-1]
        else:
            rep = attempts.iloc[-1]
        rec = rep.to_dict()
        rec["AttemptCount"] = len(attempts)
        rec["Attempts"] = [] if len(attempts) == 1 else [
            {**{f: r.get(f) for f in ATTEMPT_FIELDS},
             "Current": bool(r.name == rep.name)}
            for _, r in attempts.iterrows()
        ]
        rows.append(rec)

    for _, r in order[~groupable].drop(columns=["_key", "_neg_row"]).iterrows():
        rec = r.to_dict()
        rec["AttemptCount"] = 1
        rec["Attempts"] = []
        rows.append(rec)

    return pd.DataFrame(rows)


def _attach_bill_side(matched, bills):
    """Widen the matched rows with the bill detail and the document chain."""
    if matched.empty:
        return matched
    first = matched["bill_indices"].apply(lambda x: x[0] if x else None)
    cols = [c for c in MATCH_SIDE_COLS + [t[0] for t in TRAIL] if c in bills.columns]
    side = bills.loc[first.dropna().astype(int), cols].reset_index(drop=True)
    return pd.concat([matched.reset_index(drop=True), side], axis=1)


def reconcile(bank_df, bill_df, rnote_df=None, crn_df=None,
              window_days=0, co7_lookback_days=5, date_tolerance_days=2,
              amount_tolerance=0.0, allow_batched=True, max_batch_size=3):
    """
    Returns a dict of frames: matched, bank_only, bill_only, summary,
    bills_enriched.
    """
    bills = build_trail(attach_lineage(bill_df, rnote_df, crn_df))

    results, bank_only = match_bank_to_billstatus(
        bank_df, bills,
        date_tolerance_days=date_tolerance_days,
        amount_tolerance=amount_tolerance,
        allow_batched=allow_batched,
        max_batch_size=max_batch_size,
    )
    # Stable id per match, and the settlement stamped onto the bills so a
    # bill row can say which credit paid it. All confidences are recorded;
    # what counts as "settled" for display is the consumer's call.
    bills["SettledInStatement"] = False
    for col in ("Settled_MatchId", "Settled_BankRef", "Settled_ValueDate",
                "Settled_CreditAmt", "Settled_Confidence"):
        bills[col] = None
    for n, r in enumerate(results):
        for i in r.bill_indices:
            bills.loc[i, "SettledInStatement"] = True
            bills.loc[i, "Settled_MatchId"] = f"m{n}"
            bills.loc[i, "Settled_BankRef"] = r.bank_ref
            bills.loc[i, "Settled_ValueDate"] = r.value_date
            bills.loc[i, "Settled_CreditAmt"] = r.amount
            bills.loc[i, "Settled_Confidence"] = r.confidence

    matched = _attach_bill_side(results_to_frame(results), bills)
    if not matched.empty:
        matched["match_id"] = [f"m{n}" for n in range(len(results))]

    expected = _expected_bills(bills, bank_df, window_days, co7_lookback_days)
    hit_idx = {i for r in results for i in r.bill_indices}
    bill_only = expected[~expected.index.isin(hit_idx)].copy()

    if not bank_only.empty:
        bank_only = bank_only.copy()
        bank_only["exception_type"] = "BANK_ONLY"
        bank_only["action"] = bank_only["gap_type"].map(BANK_ACTIONS)

    if not bill_only.empty:
        bill_only["exception_type"] = "BILL_ONLY"
        bill_only["action"] = bill_only["ExpectedBasis"].map(BILL_ACTIONS)

    match_review = _match_review(matched, bills)

    return {
        "matched": matched,
        "bank_only": bank_only,
        "bill_only": bill_only,
        "match_review": match_review,
        "summary": summarise(bank_df, matched, bank_only, bill_only, expected,
                             match_review),
        "bills_enriched": bills,
        "bills_grouped": group_bill_attempts(bills),
    }


def summarise(bank_df, matched, bank_only, bill_only, expected, match_review=None):
    rows = [
        ("Bank credits in statement", len(bank_df), bank_df["amount"].sum()),
        ("Bills expected in window", len(expected), expected["NetAmt"].sum()),
        ("Matched", len(matched), matched["amount"].sum() if not matched.empty else 0),
    ]
    if not matched.empty:
        for conf, grp in matched.groupby("confidence"):
            rows.append((f"  {conf}", len(grp), grp["amount"].sum()))
        for lin, grp in matched.groupby("LineageStatus", dropna=False):
            rows.append((f"  lineage {lin}", len(grp), grp["amount"].sum()))
    rows.append(("Exception - bank only", len(bank_only),
                 bank_only["amount"].sum() if not bank_only.empty else 0))
    if not bank_only.empty:
        for g, grp in bank_only.groupby("gap_type"):
            rows.append((f"  {g}", len(grp), grp["amount"].sum()))
    rows.append(("Exception - bill only", len(bill_only),
                 bill_only["NetAmt"].sum() if not bill_only.empty else 0))
    if not bill_only.empty:
        for g, grp in bill_only.groupby("ExpectedBasis"):
            rows.append((f"  {g}", len(grp), grp["NetAmt"].sum()))
    if match_review is not None:
        rows.append(("Review - weak matches", len(match_review),
                     match_review["amount"].sum() if not match_review.empty else 0))
        if not match_review.empty:
            for g, grp in match_review.groupby("confidence"):
                rows.append((f"  {g}", len(grp), grp["amount"].sum()))
    return pd.DataFrame(rows, columns=["Category", "Count", "Amount"])


def exception_queue(out):
    """
    Both sides in one list, so a reviewer works a single queue. Bank rows
    have no bill fields and bill rows have no bank fields; that is the
    point, not a defect.
    """
    a = out["bank_only"].copy()
    if not a.empty:
        a = a.rename(columns={
            "bank_ref": "Bank_Ref", "narrative": "Bank_Narrative",
            "amount": "Amount", "value_date": "Value_Date",
            "zone_guess": "Zone",
        })
    b = out["bill_only"].copy()
    if not b.empty:
        b["Amount"] = b["NetAmt"]
        b["Value_Date"] = b["PaymentAdviceDateToBank"]

    c = out.get("match_review")
    c = c.copy() if c is not None and not c.empty else pd.DataFrame()
    if not c.empty:
        # accounting_unit stays as-is: the bill-side AccountingUnit column
        # already exists on these rows and must not be duplicated.
        c = c.drop(columns=["bill_indices", "candidate_indices"], errors="ignore")
        c = c.rename(columns={
            "bank_ref": "Bank_Ref", "narrative": "Bank_Narrative",
            "amount": "Amount", "value_date": "Value_Date",
            "zone_from_narrative": "Zone", "bill_number": "BillNumber",
            "status": "Status", "contract_no": "ContractNo",
            "co7_no": "CO7No", "co7_date": "CO7Date",
            "advice_date": "PaymentAdviceDateToBank",
        })

    q = pd.concat([a, b, c], ignore_index=True, sort=False)
    front = ["exception_type", "action", "confidence", "Amount", "Value_Date",
             "Zone", "Bank_Ref", "Bank_Narrative", "gap_type",
             "CandidateSummary"]
    order = [c for c in front if c in q.columns]
    order += [c for c in EXCEPTION_COLS if c in q.columns and c not in order]
    order += [c for c in q.columns if c not in order]
    return q[order].sort_values(["exception_type", "Amount"], ascending=[True, False])


def run(cfg: ReconConfig, verbose=True):
    """
    Parse everything, reconcile, return the frames. Does not write a
    file; that is report.write_workbook.
    """
    cfg.validate()

    # One parse of the whole statement; the matcher only sees the credits,
    # but the full table (debits included) is kept for inspection.
    bank_all = parse_hsbc_statement(cfg.statement_pdf, credits_only=False)
    bank_all["UsedInRecon"] = bank_all["txn_type"] == "TFR+"
    bank = (bank_all[bank_all["txn_type"] == "TFR+"]
            .drop(columns=["UsedInRecon"])
            .reset_index(drop=True).copy())
    check = bank_selfcheck(bank, cfg.statement_pdf)
    if verbose:
        print("bank self-check:", check)
    if check and (check["parsed_count"] != check["stated_count"]
                  or abs(check["parsed_total"] - check["stated_total"]) > 1):
        raise ValueError(
            f"statement parse does not tie to the totals HSBC prints: {check}"
        )

    bills, recoveries = parse_bill_status(cfg.bill_status, return_recoveries=True)
    rnote = load_rnote(cfg.rnote, cfg.rnote_sheet) if cfg.rnote else None
    crn = load_crn(cfg.crn, cfg.crn_sheet) if cfg.crn else None

    out = reconcile(
        bank, bills, rnote, crn,
        window_days=cfg.window_days,
        co7_lookback_days=cfg.co7_lookback_days,
        date_tolerance_days=cfg.date_tolerance_days,
        amount_tolerance=cfg.amount_tolerance,
        allow_batched=cfg.allow_batched,
        max_batch_size=cfg.max_batch_size,
    )
    out["bank"] = bank
    out["bank_all"] = bank_all
    out["bills"] = bills
    out["recoveries"] = recoveries
    out["queue"] = exception_queue(out)
    return out
