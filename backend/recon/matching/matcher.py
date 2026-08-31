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

import pandas as pd

from ..rules import FieldMapping
from .scoring import (DEFAULT_MAPPING, PAID_STATUSES, confidence_label,
                      norm_text, pick_bill_date, score_pair)


@dataclass
class MatchResult:
    """One row of the matched output. Fields come from three places:
    the bank row, the bill row, and the scorecard entry."""
    # from the bank credit
    bank_ref: str
    narrative: str
    amount: float
    value_date: datetime | None
    zone_from_narrative: str | None
    # from the bill (canonical gold names; bill_zone stays legacy-named —
    # it carries the FIRST exact signal's bill-side value)
    bill_zone: str | None
    bill_number: str | None
    contract_no: str | None
    payment_order_date: datetime | None
    payment_order_ref: str | None
    payment_advice_date: datetime | None
    org_unit: str | None
    bill_status: str | None
    # from the scorecard
    date_gap_days: int | None
    date_source: str | None
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


def _build_amount_index(candidates, mapping=DEFAULT_MAPPING, decimals=2):
    """Rounded bill amount -> list of bill indexes. Rounding because one
    side was read from a PDF and the other from Excel."""
    index = {}
    for idx, row in candidates.iterrows():
        index.setdefault(round(row[mapping.bill_amount_field], decimals),
                         []).append(idx)
    return index


def _eligible_bills(bill_df, paid_statuses=PAID_STATUSES,
                    mapping=DEFAULT_MAPPING):
    """Bills that could have produced a credit. Nil-amount bills are
    dropped because no money ever moves for them."""
    out = bill_df[bill_df[mapping.eligibility_field].isin(paid_statuses)].copy()
    return out[out[mapping.bill_amount_field].notna()
               & (out[mapping.bill_amount_field] != 0)]


# --------------------------------------------------------------------- #
# Pass 1
# --------------------------------------------------------------------- #
def _score_all_pairs(bank_df, candidates, amt_index, date_tolerance_days,
                     amount_tolerance, weights=None,
                     mapping=DEFAULT_MAPPING, decimals=2):
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
        cand = lookup(round(b[mapping.bank_amount_field], decimals))
        if not cand:
            no_candidate.append(pos)
            continue
        for i in cand:
            s, zc, dc, gap, dsrc = score_pair(b, candidates.loc[i],
                                              date_tolerance_days, weights,
                                              mapping)
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
def _assign(bank_df, candidates, pairs, top_score, tied, top_idx,
            mapping=DEFAULT_MAPPING):
    """
    Claim best first. One credit per bill, one bill per credit.

    A credit whose best pairing was taken by someone else is NOT allowed
    to settle for a worse one; it falls through to the exception queue. A
    missing match you can investigate beats a wrong match you cannot see.
    """
    # the legacy zone_* result columns carry the FIRST exact signal's
    # values (identical to zone under the default mapping)
    sig = mapping.exact_signals[0] if mapping.exact_signals else None
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
                f"the same; picked bill {row.get('bill_number')} arbitrarily"
            )
            conf = "AMBIGUOUS"
        elif conf != "HIGH":
            # name the exact root cause with the CUSTOMER'S field names —
            # the mapping is in scope, so no abstract "signal(s)" wording
            def _sig_name(s):
                return s.key or s.bill_field

            all_sigs = ", ".join(_sig_name(s) for s in mapping.exact_signals) \
                or "no exact signals configured"
            # which specific signals failed (norm_text semantics match
            # score_pair: a missing value counts as not agreed)
            failed_sigs = ", ".join(
                _sig_name(s) for s in mapping.exact_signals
                if norm_text(b.get(s.bank_field)) is None
                or norm_text(b.get(s.bank_field)) != norm_text(row.get(s.bill_field))
            ) or all_sigs
            if p["date_source"] is None or p["date_gap"] is None:
                date_why = "date unconfirmed (no bill date to compare)"
            else:
                date_field = (mapping.bill_date_primary
                              if p["date_source"] == "advice"
                              else mapping.bill_date_fallback)
                date_why = (f"date unconfirmed (gap {p['date_gap']}d vs "
                            f"{date_field}, over tolerance)")
            if p["zone_check"] and not p["date_check"]:
                flags.append(f"REVIEW - amount and {all_sigs} matched; {date_why}")
            elif p["date_check"] and not p["zone_check"]:
                flags.append(
                    f"REVIEW - amount and date matched; {failed_sigs} did not agree")
            else:
                flags.append(
                    f"REVIEW - amount matched only; {failed_sigs} and date "
                    f"both unconfirmed")

        results.append(MatchResult(
            bank_ref=b.get("bank_ref", ""),
            narrative=b["narrative"],
            amount=b[mapping.bank_amount_field],
            value_date=b.get(mapping.bank_date_field),
            zone_from_narrative=norm_text(b.get(sig.bank_field)) if sig else None,
            bill_zone=norm_text(row.get(sig.bill_field)) if sig else None,
            bill_number=row.get("bill_number"),
            contract_no=row.get("contract_no"),
            payment_order_date=row.get("payment_order_date"),
            payment_order_ref=row.get("payment_order_ref"),
            payment_advice_date=row.get("payment_advice_date"),
            org_unit=row.get("org_unit"),
            bill_status=row.get("bill_status"),
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
def find_batch(bank_row, candidates, free_idx, date_tol, amt_tol, max_size,
               mapping=DEFAULT_MAPPING, batch_slack=0.5, decimals=2):
    """
    A set of bills agreeing on every exact signal whose amounts sum to
    the credit.

    Signal and date filter first, combinations second. The other way
    round would search millions of subsets and turn up coincidental sums.
    """
    sig_vals = [norm_text(bank_row.get(s.bank_field))
                for s in mapping.exact_signals]
    if (not mapping.exact_signals or any(v is None for v in sig_vals)
            or pd.isna(bank_row.get(mapping.bank_date_field))):
        return None

    pool = []
    for i in free_idx:
        row = candidates.loc[i]
        if any(norm_text(row.get(s.bill_field)) != v
               for s, v in zip(mapping.exact_signals, sig_vals)):
            continue
        bill_date, _ = pick_bill_date(row, mapping)
        if bill_date is None:
            continue
        if abs((bank_row[mapping.bank_date_field] - bill_date).days) > date_tol:
            continue
        pool.append(i)

    if len(pool) < 2:
        return None

    target = round(bank_row[mapping.bank_amount_field], decimals)
    # legacy hardcoded 0.5 (50 paise) lives on as batch_slack's default;
    # per-customer config via MatchRuleSet.batch_amount_slack
    tol = max(amt_tol, batch_slack)
    for size in range(2, min(max_size, len(pool)) + 1):
        for combo in combinations(pool, size):
            total = round(sum(candidates.loc[i, mapping.bill_amount_field]
                              for i in combo), decimals)
            if abs(total - target) <= tol:
                return list(combo)
    return None


def _batched_pass(bank_df, candidates, leftover, used_bills, date_tol, amt_tol,
                  max_size, mapping=DEFAULT_MAPPING, batch_slack=0.5,
                  decimals=2):
    sig = mapping.exact_signals[0] if mapping.exact_signals else None
    results, still_open = [], []
    free = [i for i in candidates.index if i not in used_bills]

    for bp in leftover:
        b = bank_df.loc[bp]
        idxs = find_batch(b, candidates, free, date_tol, amt_tol, max_size,
                          mapping, batch_slack, decimals)
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
            amount=b[mapping.bank_amount_field],
            value_date=b.get(mapping.bank_date_field),
            zone_from_narrative=norm_text(b.get(sig.bank_field)) if sig else None,
            bill_zone=norm_text(rows[sig.bill_field].iloc[0]) if sig else None,
            bill_number=", ".join(str(x) for x in rows["bill_number"]),
            contract_no=", ".join(str(x) for x in rows["contract_no"]),
            payment_order_date=rows["payment_order_date"].max(),
            payment_order_ref=", ".join(str(x) for x in rows["payment_order_ref"]),
            payment_advice_date=rows["payment_advice_date"].max(),
            org_unit=rows["org_unit"].iloc[0],
            bill_status=", ".join(sorted(set(rows["bill_status"]))),
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
    weights=None,
    mapping=None,
    batch_amount_slack=0.5,
    amount_decimals=2,
):
    """
    Returns (results, unmatched_bank).

    results is a list of MatchResult. unmatched_bank is a DataFrame of
    credits with no match, tagged with gap_type.
    """
    mapping = mapping or FieldMapping()
    candidates = _eligible_bills(bill_df, paid_statuses, mapping)
    amt_index = _build_amount_index(candidates, mapping, amount_decimals)

    pairs, no_candidate = _score_all_pairs(
        bank_df, candidates, amt_index, date_tolerance_days, amount_tolerance,
        weights, mapping, amount_decimals)
    top_score, tied, top_idx = _ceilings(pairs)
    results, used_bills, used_bank = _assign(
        bank_df, candidates, pairs, top_score, tied, top_idx, mapping)

    leftover = [i for i in bank_df.index if i not in used_bank and i not in no_candidate]
    if allow_batched:
        batched, leftover = _batched_pass(
            bank_df, candidates, leftover, used_bills,
            date_tolerance_days, amount_tolerance, max_batch_size, mapping,
            batch_amount_slack, amount_decimals)
        results.extend(batched)

    unmatched = bank_df.loc[sorted(set(leftover) | set(no_candidate))].copy()
    if not unmatched.empty:
        # gap literals stay historical; presence of the FIRST exact
        # signal's bank field decides which one
        sig = mapping.exact_signals[0] if mapping.exact_signals else None
        if sig is None:
            unmatched["gap_type"] = "NON_IREPS_OR_UNRECOGNISED"
        else:
            unmatched["gap_type"] = unmatched[sig.bank_field].apply(
                lambda z: "ZONE_BILL_NOT_FOUND" if norm_text(z)
                else "NON_IREPS_OR_UNRECOGNISED"
            )
    return results, unmatched


def results_to_frame(results):
    return pd.DataFrame([asdict(r) for r in results])
