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
from .matching.scoring import PAID_STATUSES
from .parsers import attach_lineage

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

# org_unit deliberately absent: MatchResult already carries it, and under
# one canonical vocabulary a second copy would collide in _attach_bill_side.
MATCH_SIDE_COLS = [
    "bill_date", "contract_date", "vendor_code", "submission_date",
    "gross_amount", "approved_amount", "deduction_amount", "recoveries",
    "recovery_count", "return_reason", "LineageStatus", "RNOTE_MatchedVia",
    "CRN_MatchedVia",
]

EXCEPTION_COLS = [
    "exception_type", "action", "bill_number", "contract_no", "zone",
    "org_unit", "bill_status", "ExpectedBasis", "bill_date", "submission_ref",
    "submission_date", "payment_order_ref", "payment_order_date",
    "payment_advice_date", "gross_amount", "approved_amount",
    "deduction_amount", "net_payable_amount", "recovery_count",
    "return_reason", "LineageStatus", "PO", "PO_Date", "Receipt_Doc",
    "Receipt_Date", "Receipt_Qty", "DRR_or_Challan", "Bill_Reg_No",
    "sheet", "data_row",
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
        "One check did not agree — the flag names which check failed. "
        "Review the candidate bill's detail before accepting the match.",
    "AMOUNT_ONLY":
        "Neither the exact signals nor the date agreed. Treat as a guess; "
        "verify against the candidate bill before posting.",
    "BATCHED":
        "One credit covers several bills whose Net Amts sum to it. Verify "
        "every covered bill before posting.",
}

# Fields shown per candidate bill in a MATCH_REVIEW row, in display order.
CANDIDATE_FIELDS = [
    "bill_number", "contract_no", "zone", "bill_status",
    "gross_amount", "approved_amount", "deduction_amount", "net_payable_amount",
    "submission_ref", "submission_date", "payment_order_ref",
    "payment_order_date", "payment_advice_date",
    "org_unit", "LineageStatus", "PO", "PO_Date", "Receipt_Doc",
    "Receipt_Date", "DRR_or_Challan", "Bill_Reg_No", "return_reason",
    "sheet", "data_row",
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


def _expected_bills(bills, bank_df, window_days, co7_lookback_days, mapping):
    """
    Which bills could plausibly have been paid in this statement.

    Not all of them. Money cannot arrive before the advice goes out, so
    the default window is zero days either side of the statement dates.
    Widen it and you pull in bills that settled earlier and report them
    as false shortfalls. Date/amount/eligibility fields follow the
    customer's field mapping; ExpectedBasis literals stay historical.
    """
    stmt_lo = pd.to_datetime(bank_df[mapping.bank_date_field]).min()
    stmt_hi = pd.to_datetime(bank_df[mapping.bank_date_field]).max()
    lo = stmt_lo - pd.Timedelta(days=window_days)
    hi = stmt_hi + pd.Timedelta(days=window_days)

    advised = bills[mapping.bill_date_primary].between(lo, hi)
    if mapping.bill_date_fallback is not None and mapping.fallback_due_statuses:
        co7_due = (
            bills[mapping.eligibility_field].isin(mapping.fallback_due_statuses)
            & bills[mapping.bill_date_fallback].between(
                stmt_lo - pd.Timedelta(days=co7_lookback_days), hi)
        )
    else:
        co7_due = pd.Series(False, index=bills.index)

    expected = bills[advised | co7_due].copy()
    expected["ExpectedBasis"] = "CO7_ISSUED_NO_ADVICE"
    expected.loc[advised.reindex(expected.index, fill_value=False),
                 "ExpectedBasis"] = "ADVICE_DATE"
    # Nothing payable means its absence is not a shortfall.
    expected.loc[expected[mapping.bill_amount_field].fillna(0) == 0,
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
        adv = c.get("payment_advice_date")
        adv = adv.date().isoformat() if pd.notna(adv) and hasattr(adv, "date") else "no advice"
        mark = "*" if c.get("Picked") else ""
        return f"{mark}{c.get('bill_number')} ({c.get('zone')}, {adv})"
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

ATTEMPT_FIELDS = ["bill_status", "submission_ref", "submission_date",
                  "payment_order_ref", "payment_order_date",
                  "net_payable_amount", "payment_advice_date",
                  "return_reason", "sheet", "data_row"]


def group_bill_attempts(bills):
    """
    One row per bill instead of one per processing attempt.

    IREPS exports a fresh block (new CO6) every time a returned bill is
    resubmitted, so a bounced bill appears several times. The combined row
    is the attempt that actually got settled if any did (the money decides),
    otherwise the chronologically latest; the full journey rides along in
    `Attempts`. Display-only — matching always uses the ungrouped frame.
    """
    key = bills["bill_number"].astype(str).str.strip()
    groupable = ~(key.isin(UNGROUPABLE_KEYS) | bills["bill_number"].isna())

    # chronological within a bill: submission_date ascending (NaT last); in
    # this export a smaller data_row is more recent, so it breaks ties
    # descending
    order = bills.assign(_key=key, _neg_row=-bills["data_row"]) \
                 .sort_values(["submission_date", "_neg_row"], na_position="last")
    groupable = groupable.reindex(order.index)

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
              amount_tolerance=0.0, allow_batched=True, max_batch_size=3,
              paid_statuses=None, weights=None, field_map=None):
    """
    Returns a dict of frames: matched, bank_only, bill_only, summary,
    bills_enriched. field_map (recon.rules.FieldMapping) selects which
    gold columns drive the match signals; None = historical defaults.
    """
    from .rules import FieldMapping
    mapping = field_map or FieldMapping()
    bills = build_trail(attach_lineage(bill_df, rnote_df, crn_df))

    results, bank_only = match_bank_to_billstatus(
        bank_df, bills,
        date_tolerance_days=date_tolerance_days,
        amount_tolerance=amount_tolerance,
        allow_batched=allow_batched,
        max_batch_size=max_batch_size,
        paid_statuses=(frozenset(paid_statuses) if paid_statuses
                       else PAID_STATUSES),
        weights=weights,
        mapping=mapping,
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

    expected = _expected_bills(bills, bank_df, window_days, co7_lookback_days,
                               mapping)
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
                             match_review, mapping=mapping),
        "bills_enriched": bills,
        "bills_grouped": group_bill_attempts(bills),
    }


def summarise(bank_df, matched, bank_only, bill_only, expected, match_review=None,
              mapping=None):
    from .rules import FieldMapping
    bill_amt = (mapping or FieldMapping()).bill_amount_field
    rows = [
        ("Bank credits in statement", len(bank_df), bank_df["amount"].sum()),
        ("Bills expected in window", len(expected), expected[bill_amt].sum()),
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
                 bill_only[bill_amt].sum() if not bill_only.empty else 0))
    if not bill_only.empty:
        for g, grp in bill_only.groupby("ExpectedBasis"):
            rows.append((f"  {g}", len(grp), grp[bill_amt].sum()))
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
        a = a.rename(columns={"narrative": "bank_narrative",
                              "zone_guess": "zone"})
    b = out["bill_only"].copy()
    if not b.empty:
        b["amount"] = b["net_payable_amount"]
        b["value_date"] = b["payment_advice_date"]

    c = out.get("match_review")
    c = c.copy() if c is not None and not c.empty else pd.DataFrame()
    if not c.empty:
        # MatchResult bill fields (bill_number, bill_status, contract_no,
        # payment_order_*, payment_advice_date) already carry the canonical
        # bill-side names, so only the bank-derived pair needs renaming.
        c = c.drop(columns=["bill_indices", "candidate_indices"], errors="ignore")
        c = c.rename(columns={"narrative": "bank_narrative",
                              "zone_from_narrative": "zone"})

    q = pd.concat([a, b, c], ignore_index=True, sort=False)
    front = ["exception_type", "action", "confidence", "amount", "value_date",
             "zone", "bank_ref", "bank_narrative", "gap_type",
             "CandidateSummary"]
    order = [c for c in front if c in q.columns]
    order += [c for c in EXCEPTION_COLS if c in q.columns and c not in order]
    order += [c for c in q.columns if c not in order]
    return q[order].sort_values(["exception_type", "amount"], ascending=[True, False])


def run(cfg: ReconConfig, verbose=True, sinks=None):
    """
    Parse everything through the source adapters, reconcile, return the
    frames. Does not write a file; that is report.write_workbook.
    Thin wrapper over pipeline.run_pipeline with the HSBC/IREPS adapters —
    kept so `from recon import run` and the CLI behave exactly as before.
    """
    from .pipeline import run_pipeline      # pipeline imports engine; keep one-way at module level
    from .rules import MatchRuleSet
    from .sources import get_adapter

    cfg.validate()

    adapters = {t: get_adapter(t, k) for t, k in (
        ("bank_statement", "hsbc"),
        ("bill_status", "ireps"),
        ("lineage_rnote", "ireps_rnote"),
        ("lineage_crn", "ireps_crn"),
    )}
    inputs = {
        "bank_statement": cfg.statement_pdf,
        "bill_status": cfg.bill_status,
        "lineage_rnote": cfg.rnote,
        "lineage_crn": cfg.crn,
    }
    params = {
        "lineage_rnote": {"sheet": cfg.rnote_sheet},
        "lineage_crn": {"sheet": cfg.crn_sheet},
    }
    rules = MatchRuleSet(
        date_tolerance_days=cfg.date_tolerance_days,
        amount_tolerance=cfg.amount_tolerance,
        window_days=cfg.window_days,
        co7_lookback_days=cfg.co7_lookback_days,
        allow_batched=cfg.allow_batched,
        max_batch_size=cfg.max_batch_size,
        paid_statuses=frozenset(cfg.paid_statuses),
    )
    return run_pipeline(inputs, adapters, params, rules,
                        sinks=sinks, verbose=verbose)
