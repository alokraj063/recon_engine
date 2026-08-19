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


def pick_bill_date(row):
    """
    Which date on the bill to compare against the credit.

    Returns (date, source). Advice date first, CO7 date as fallback for
    bills the export has not advised yet.
    """
    if pd.notna(row.get("PaymentAdviceDateToBank")):
        return row["PaymentAdviceDateToBank"], "advice"
    if pd.notna(row.get("CO7Date")):
        return row["CO7Date"], "co7"
    return None, None


def score_pair(bank_row, bill_row, date_tolerance_days=2):
    """
    Score one pairing.

    Returns (score, zone_check, date_check, date_gap_days, date_source).
    The individual signals come back alongside the number because the
    confidence label reads them directly rather than reversing the score.
    """
    zone_bank = norm_text(bank_row.get("zone_guess"))
    zone_bill = norm_text(bill_row.get("Zone"))
    zone_check = zone_bank is not None and zone_bank == zone_bill

    bill_date, date_source = pick_bill_date(bill_row)
    date_gap, date_check = None, False
    if bill_date is not None and pd.notna(bank_row.get("value_date")):
        date_gap = abs((bank_row["value_date"] - bill_date).days)
        date_check = date_gap <= date_tolerance_days

    score = 0
    if date_check and date_source == "advice":
        score += WEIGHT_ADVICE_DATE
    elif date_check:
        score += WEIGHT_CO7_DATE
    if zone_check:
        score += WEIGHT_ZONE

    return score, zone_check, date_check, date_gap, date_source


def confidence_label(zone_check, date_check, date_source):
    """
    Turn the raw signals into something a reviewer can sort on.

    Read from the signals, not the score, because two different signal
    combinations can add up to the same number and they do not mean the
    same thing.
    """
    if zone_check and date_check and date_source == "advice":
        return "HIGH"
    if zone_check and date_check:
        return "MEDIUM"
    if zone_check or date_check:
        return "LOW"
    return "AMOUNT_ONLY"
