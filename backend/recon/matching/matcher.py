"""
The three-pass matcher.

  Pass 1  score every credit/bill pairing that shares an amount, assign
          nothing. Ends with a flat scorecard.
  Pass 2  sort that scorecard and claim best first, so an early row in
          the statement can no longer take a bill a later row needed.
  Pass 3  for credits nothing matched, look for a small set of bills
          whose Net Amts sum to the credit.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from itertools import combinations
from typing import Optional

import pandas as pd

from .scoring import PAID_STATUSES, norm_text, pick_bill_date, score_pair, confidence_label


@dataclass
class MatchResult:
    """One row of the matched output. Fields come from three places:
    the bank row, the bill row, and the scorecard entry."""
    # from the bank credit
    bank_ref: str
    narrative: str
    amount: float
    value_date: Optional[datetime]
    zone_from_narrative: Optional[str]
    # from the bill
    bill_zone: Optional[str]
    bill_number: Optional[str]
    contract_no: Optional[str]
    co7_date: Optional[datetime]
    co7_no: Optional[str]
    advice_date: Optional[datetime]
    accounting_unit: Optional[str]
    status: Optional[str]
    # from the scorecard
    date_gap_days: Optional[int]
    date_source: Optional[str]
    amount_check: bool
    zone_check: bool
    date_check: bool
    confidence: str
    n_candidates: int
    tied_candidates: int
    flag: str
    bill_indices: list = field(default_factory=list)
    # every bill that was in contention for this credit: all ceiling-score
    # ties for a single match, all covered bills for a batched one. This is
    # the evidence the review queue shows behind an AMBIGUOUS/LOW flag.
    candidate_indices: list = field(default_factory=list)


def _build_amount_index(candidates):
    """Rounded Net Amt -> list of bill indexes. Rounding because one side
    was read from a PDF and the other from Excel."""
    index = {}
    for idx, row in candidates.iterrows():
        index.setdefault(round(row["NetAmt"], 2), []).append(idx)
    return index


def _eligible_bills(bill_df, paid_statuses=PAID_STATUSES):
    """Bills that could have produced a credit. Nil-net bills are dropped
    because no money ever moves for them."""
    out = bill_df[bill_df["Status"].isin(paid_statuses)].copy()
    return out[out["NetAmt"].notna() & (out["NetAmt"] != 0)]


# --------------------------------------------------------------------- #
# Pass 1
# --------------------------------------------------------------------- #
def _score_all_pairs(bank_df, candidates, amt_index, date_tolerance_days,
                     amount_tolerance):
    """
    Score everything, assign nothing.

    Returns (pairs, no_candidate). `pairs` is one dict per credit/bill
    combination sharing an amount. `no_candidate` is the positions of
    credits where no bill matched on amount at all.
    """
    def lookup(amt):
        if amount_tolerance == 0.0:
            return list(amt_index.get(amt, []))
        out = []
        for a, idxs in amt_index.items():
            if abs(a - amt) <= amount_tolerance:
                out.extend(idxs)
        return out

    pairs, no_candidate = [], []
    for pos, b in bank_df.iterrows():
        cand = lookup(round(b["amount"], 2))
        if not cand:
            no_candidate.append(pos)
            continue
        for i in cand:
            s, zc, dc, gap, dsrc = score_pair(b, candidates.loc[i], date_tolerance_days)
            pairs.append({
                "bank_pos": pos, "bill_idx": i, "score": s,
                "zone_check": zc, "date_check": dc,
                "date_gap": gap, "date_source": dsrc,
                "n_candidates": len(cand),
            })
    return pairs, no_candidate


def _ceilings(pairs):
    """
    Best score reached per credit, how many pairings tied there, and which
    bills those were.

    The tie counter and index list reset whenever a new maximum appears,
    so they describe the final ceiling rather than ties anywhere.
    """
    top_score, tied, top_idx = {}, {}, {}
    for p in pairs:
        bp = p["bank_pos"]
        if bp not in top_score or p["score"] > top_score[bp]:
            top_score[bp] = p["score"]
            tied[bp] = 1
            top_idx[bp] = [p["bill_idx"]]
        elif p["score"] == top_score[bp]:
            tied[bp] += 1
            top_idx[bp].append(p["bill_idx"])
    return top_score, tied, top_idx


# --------------------------------------------------------------------- #
# Pass 2
# --------------------------------------------------------------------- #
def _assign(bank_df, candidates, pairs, top_score, tied, top_idx):
    """
    Claim best first. One credit per bill, one bill per credit.

    A credit whose best pairing was taken by someone else is NOT allowed
    to settle for a worse one; it falls through to the exception queue. A
    missing match you can investigate beats a wrong match you cannot see.
    """
    # -score for descending, bank_pos so reruns are identical
    pairs = sorted(pairs, key=lambda p: (-p["score"], p["bank_pos"]))
    used_bills, used_bank, results = set(), set(), []

    for p in pairs:
        bp = p["bank_pos"]
        if bp in used_bank or p["bill_idx"] in used_bills:
            continue
        if p["score"] < top_score[bp]:
            continue

        b = bank_df.loc[bp]
        row = candidates.loc[p["bill_idx"]]
        used_bank.add(bp)
        used_bills.add(p["bill_idx"])

        conf = confidence_label(p["zone_check"], p["date_check"], p["date_source"])
        n_tied = tied[bp]
        flags = []
        if n_tied > 1:
            flags.append(
                f"AMBIGUOUS - {n_tied} bills share this Net Amt and score "
                f"the same; picked bill {row.get('BillNumber')} arbitrarily"
            )
            conf = "AMBIGUOUS"
        elif conf != "HIGH":
            flags.append("REVIEW - amount matched but zone/advice date not both confirmed")

        results.append(MatchResult(
            bank_ref=b.get("bank_ref", ""),
            narrative=b["narrative"],
            amount=b["amount"],
            value_date=b.get("value_date"),
            zone_from_narrative=norm_text(b.get("zone_guess")),
            bill_zone=norm_text(row.get("Zone")),
            bill_number=row.get("BillNumber"),
            contract_no=row.get("ContractNo"),
            co7_date=row.get("CO7Date"),
            co7_no=row.get("CO7No"),
            advice_date=row.get("PaymentAdviceDateToBank"),
            accounting_unit=row.get("AccountingUnit"),
            status=row.get("Status"),
            date_gap_days=p["date_gap"],
            date_source=p["date_source"],
            amount_check=True,
            zone_check=p["zone_check"],
            date_check=p["date_check"],
            confidence=conf,
            n_candidates=p["n_candidates"],
            tied_candidates=n_tied,
            flag="; ".join(flags),
            bill_indices=[p["bill_idx"]],
            candidate_indices=list(top_idx[bp]),
        ))

    return results, used_bills, used_bank


# --------------------------------------------------------------------- #
# Pass 3
# --------------------------------------------------------------------- #
def find_batch(bank_row, candidates, free_idx, date_tol, amt_tol, max_size):
    """
    A set of bills in the same zone whose Net Amts sum to the credit.

    Zone and date filter first, combinations second. The other way round
    would search millions of subsets and turn up coincidental sums.
    """
    zone = norm_text(bank_row.get("zone_guess"))
    if zone is None or pd.isna(bank_row.get("value_date")):
        return None

    pool = []
    for i in free_idx:
        row = candidates.loc[i]
        if norm_text(row.get("Zone")) != zone:
            continue
        bill_date, _ = pick_bill_date(row)
        if bill_date is None:
            continue
        if abs((bank_row["value_date"] - bill_date).days) > date_tol:
            continue
        pool.append(i)

    if len(pool) < 2:
        return None

    target = round(bank_row["amount"], 2)
    tol = max(amt_tol, 0.5)
    for size in range(2, min(max_size, len(pool)) + 1):
        for combo in combinations(pool, size):
            total = round(sum(candidates.loc[i, "NetAmt"] for i in combo), 2)
            if abs(total - target) <= tol:
                return list(combo)
    return None


def _batched_pass(bank_df, candidates, leftover, used_bills, date_tol, amt_tol,
                  max_size):
    results, still_open = [], []
    free = [i for i in candidates.index if i not in used_bills]

    for bp in leftover:
        b = bank_df.loc[bp]
        idxs = find_batch(b, candidates, free, date_tol, amt_tol, max_size)
        if idxs is None:
            still_open.append(bp)
            continue
        rows = candidates.loc[idxs]
        for i in idxs:
            used_bills.add(i)
            free.remove(i)
        results.append(MatchResult(
            bank_ref=b.get("bank_ref", ""),
            narrative=b["narrative"],
            amount=b["amount"],
            value_date=b.get("value_date"),
            zone_from_narrative=norm_text(b.get("zone_guess")),
            bill_zone=norm_text(rows["Zone"].iloc[0]),
            bill_number=", ".join(str(x) for x in rows["BillNumber"]),
            contract_no=", ".join(str(x) for x in rows["ContractNo"]),
            co7_date=rows["CO7Date"].max(),
            co7_no=", ".join(str(x) for x in rows["CO7No"]),
            advice_date=rows["PaymentAdviceDateToBank"].max(),
            accounting_unit=rows["AccountingUnit"].iloc[0],
            status=", ".join(sorted(set(rows["Status"]))),
            date_gap_days=None,
            date_source="advice",
            amount_check=True,
            zone_check=True,
            date_check=True,
            confidence="BATCHED",
            n_candidates=len(idxs),
            tied_candidates=1,
            flag=f"BATCHED - one credit covers {len(idxs)} bills, verify before posting",
            bill_indices=list(idxs),
            candidate_indices=list(idxs),
        ))
    return results, still_open


# --------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------- #
def match_bank_to_billstatus(
    bank_df,
    bill_df,
    date_tolerance_days=2,
    amount_tolerance=0.0,
    allow_batched=True,
    max_batch_size=3,
    paid_statuses=PAID_STATUSES,
):
    """
    Returns (results, unmatched_bank).

    results is a list of MatchResult. unmatched_bank is a DataFrame of
    credits with no match, tagged with gap_type.
    """
    candidates = _eligible_bills(bill_df, paid_statuses)
    amt_index = _build_amount_index(candidates)

    pairs, no_candidate = _score_all_pairs(
        bank_df, candidates, amt_index, date_tolerance_days, amount_tolerance)
    top_score, tied, top_idx = _ceilings(pairs)
    results, used_bills, used_bank = _assign(
        bank_df, candidates, pairs, top_score, tied, top_idx)

    leftover = [i for i in bank_df.index if i not in used_bank and i not in no_candidate]
    if allow_batched:
        batched, leftover = _batched_pass(
            bank_df, candidates, leftover, used_bills,
            date_tolerance_days, amount_tolerance, max_batch_size)
        results.extend(batched)

    unmatched = bank_df.loc[sorted(set(leftover) | set(no_candidate))].copy()
    if not unmatched.empty:
        unmatched["gap_type"] = unmatched["zone_guess"].apply(
            lambda z: "ZONE_BILL_NOT_FOUND" if norm_text(z)
            else "NON_IREPS_OR_UNRECOGNISED"
        )
    return results, unmatched


def results_to_frame(results):
    return pd.DataFrame([asdict(r) for r in results])
