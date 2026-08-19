"""
Matching splits into two modules on purpose.

`scoring` holds the small pure functions that decide how good a single
bank/bill pairing is. They take a row each and return a number. Easy to
test, easy to change the weights without touching the loop.

`matcher` holds the three-pass loop that uses them: score everything,
assign best first, then try batched credits.
"""

from .scoring import (
    PAID_STATUSES, WEIGHT_ADVICE_DATE, WEIGHT_CO7_DATE, WEIGHT_ZONE,
    norm_text, pick_bill_date, score_pair, confidence_label,
)
from .matcher import MatchResult, match_bank_to_billstatus, results_to_frame

__all__ = [
    "PAID_STATUSES", "WEIGHT_ADVICE_DATE", "WEIGHT_CO7_DATE", "WEIGHT_ZONE",
    "norm_text", "pick_bill_date", "score_pair", "confidence_label",
    "MatchResult", "match_bank_to_billstatus", "results_to_frame",
]
