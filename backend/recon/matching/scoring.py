"""
How good is one bank credit / bill pairing?

Amount is a filter, not a signal: a pairing only reaches these functions
because the amounts already agree. What separates two bills with the same
Net Amt is the zone off the bank narrative and the date IREPS advised the
bank. Those are what get scored here.
"""

import pandas as pd

# Statuses that can have produced a credit. CO7 DONE is in because the
# payment order goes out before the export refreshes to PAYMENT MADE.
PAID_STATUSES = frozenset({"PAYMENT MADE", "CO7 DONE"})

# Advice date means "IREPS instructed the bank to pay on this day", so it
# lines up with the credit exactly and outweighs a zone match on its own.
# CO7 date only says when the payment order was raised, which can be days
# earlier, so it is worth much less.
WEIGHT_ADVICE_DATE = 4
WEIGHT_ZONE = 2
WEIGHT_CO7_DATE = 1

# The same numbers keyed for per-customer override (recon.rules).
DEFAULT_WEIGHTS = {
    "advice_date": WEIGHT_ADVICE_DATE,
    "zone": WEIGHT_ZONE,
    "co7_date": WEIGHT_CO7_DATE,
}

# Field mapping defaults — which gold columns drive the signals
# (per-customer override rides MatchRuleSet.field_map; recon.rules).
from ..rules import FieldMapping  # noqa: E402  (no cycle: rules imports nothing from recon)

DEFAULT_MAPPING = FieldMapping()


def norm_text(v):
    """
    Normalise a cell for comparison.

    These frames carry four different spellings of "nothing here": real
    None, float nan, the string 'nan', and IREPS' '----' placeholder.
    Comparing them raw is how the original matcher ended up labelling
    every non-IREPS receipt as a missing bill.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s.upper() if s and s.lower() not in ("nan", "none", "----") else None


def pick_bill_date(row, mapping=None):
    """
    Which date on the bill to compare against the credit.

    Returns (date, source). Primary date field first, fallback second.
    Source tags stay the LITERAL strings "advice" (primary) and "co7"
    (fallback) even under a custom mapping — they appear in the golden
    CSVs, persisted run payloads and the frontend.
    """
    m = mapping or DEFAULT_MAPPING
    if pd.notna(row.get(m.bill_date_primary)):
        return row[m.bill_date_primary], "advice"
    if m.bill_date_fallback is not None and pd.notna(row.get(m.bill_date_fallback)):
        return row[m.bill_date_fallback], "co7"
    return None, None


def score_pair(bank_row, bill_row, date_tolerance_days=2, weights=None,
               mapping=None):
    """
    Score one pairing.

    Returns (score, exact_ok, date_check, date_gap_days, date_source) —
    position 2 was historically zone_check; it now means "ALL configured
    exact signals agree" (identical for the default single zone signal).
    The individual signals come back alongside the number because the
    confidence label reads them directly rather than reversing the score.

    `weights` overrides signal weights per customer (a signal with a
    `key` reads the weights dict, others carry their own weight); it only
    affects ranking — confidence labels stay signal-derived.
    """
    w = DEFAULT_WEIGHTS if weights is None else weights
    m = mapping or DEFAULT_MAPPING

    checks = []
    for sig in m.exact_signals:
        bv = norm_text(bank_row.get(sig.bank_field))
        checks.append(bv is not None and bv == norm_text(bill_row.get(sig.bill_field)))
    # bool() guard: all([]) is vacuously True — a customer with no exact
    # signals must never see every amount+date pair inflated to HIGH
    exact_ok = bool(m.exact_signals) and all(checks)

    bill_date, date_source = pick_bill_date(bill_row, m)
    date_gap, date_check = None, False
    if bill_date is not None and pd.notna(bank_row.get(m.bank_date_field)):
        date_gap = abs((bank_row[m.bank_date_field] - bill_date).days)
        date_check = date_gap <= date_tolerance_days

    score = 0
    if date_check and date_source == "advice":
        score += w["advice_date"]
    elif date_check:
        score += w["co7_date"]
    for sig, ok in zip(m.exact_signals, checks):
        if ok:
            score += (w.get(sig.key, sig.weight) if sig.key else sig.weight)

    return score, exact_ok, date_check, date_gap, date_source


def confidence_label(zone_check, date_check, date_source):
    """
    Turn the raw signals into something a reviewer can sort on.

    Read from the signals, not the score, because two different signal
    combinations can add up to the same number and they do not mean the
    same thing. The first argument is "all exact signals agree" (named
    for its historical single-signal meaning).
    """
    if zone_check and date_check and date_source == "advice":
        return "HIGH"
    if zone_check and date_check:
        return "MEDIUM"
    if zone_check or date_check:
        return "LOW"
    return "AMOUNT_ONLY"
