"""
Per-customer matching rules — the "delta layer". Everything the matcher
and the expected-payment window can be tuned by, in one object.

Resolution order: these dataclass defaults <- the customer's DB rule set
<- the API form tunables (the form wins; the DB supplies what the form
cannot express: paid_statuses, signal weights, and the field mapping).

FieldMapping makes the MATCH SIGNALS configurable per customer — which
gold columns form the amount join, the date comparison, the exact-match
signals and the eligibility filter. Only signals are configurable:
display columns (TRAIL, MATCH_SIDE_COLS, exception/candidate fields)
stay gold-canonical and hardcoded — a different bank/ERP adapter maps
into the same canonical gold columns, so display never varies.
"""

from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, Optional, Tuple

# CO7 DONE removed 2026-09-22: a payment order does not guarantee payment
DEFAULT_PAID_STATUSES = frozenset({"PAYMENT MADE"})
DEFAULT_WEIGHTS = {"advice_date": 4, "zone": 2, "co7_date": 1}

# Sections a copy_overrides dict may carry. The CODES (gap_type,
# ExpectedBasis, review confidences) are frozen machine values — only the
# human-facing text keyed by them is configurable. `labels` optionally
# renames a code for display (UI-only; stored values never change).
COPY_SECTIONS = ("gap_type", "expected_basis", "review", "labels")


@dataclass(frozen=True)
class ExactSignal:
    """One exact-match field pair (norm_text equality on both sides).
    `key`, when set, lets the customer's weights dict override the
    signal's weight (the default zone signal keeps its historical
    weights-dict key "zone")."""
    bank_field: str
    bill_field: str
    weight: int = 2
    key: Optional[str] = None

    def to_dict(self) -> dict:
        return {"bank_field": self.bank_field, "bill_field": self.bill_field,
                "weight": self.weight, "key": self.key}

    @classmethod
    def from_dict(cls, d: dict) -> "ExactSignal":
        return cls(bank_field=d["bank_field"], bill_field=d["bill_field"],
                   weight=int(d.get("weight", 2)), key=d.get("key"))


@dataclass(frozen=True)
class ReferenceSignal:
    """The bill's own identifier written inside a bank text field — IREPS
    production units (ICF, RCF…) put the bill number straight into the
    NEFT narrative ("NEFT FROM 1301ICF2002260301912 10 …"). Found = the
    credit NAMES that bill, the strongest evidence there is, so it only
    ever adds to a pairing's score: among bills sharing the credit's
    amount the named one outranks every unnamed one, which also breaks an
    AMBIGUOUS tie. It never makes a pairing HIGH on its own — confidence
    labels stay derived from the exact + date signals. A bill value
    shorter than `min_length` is never looked for (a short number turns
    up inside any long digit run by chance)."""
    bank_field: str
    bill_field: str
    weight: int = 8
    key: Optional[str] = None
    min_length: int = 6

    def to_dict(self) -> dict:
        return {"bank_field": self.bank_field, "bill_field": self.bill_field,
                "weight": self.weight, "key": self.key,
                "min_length": self.min_length}

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceSignal":
        return cls(bank_field=d["bank_field"], bill_field=d["bill_field"],
                   weight=int(d.get("weight", 8)), key=d.get("key"),
                   min_length=int(d.get("min_length", 6)))


@dataclass(frozen=True)
class FieldMapping:
    """Which gold columns drive matching. Defaults reproduce the
    historical hardcoded behaviour exactly (golden-gated).

    Frozen legacy literals (NEVER rename — they live in golden CSVs,
    persisted run payloads, match_rule_sets rows and the frontend): the
    date-source tags "advice"/"co7" and weights-dict keys "advice_date"/
    "co7_date" (meaning PRIMARY/FALLBACK under a custom mapping), the
    weights key "zone", status VALUES like "CO7 DONE", the tunable name
    co7_lookback_days, and MatchResult's zone_from_narrative/bill_zone/
    zone_check columns (they carry the FIRST exact signal's values)."""
    bank_amount_field: str = "amount"
    bill_amount_field: str = "net_payable_amount"
    bank_date_field: str = "value_date"
    bill_date_primary: str = "payment_advice_date"
    bill_date_fallback: Optional[str] = "payment_order_date"  # None disables fallback
    exact_signals: Tuple[ExactSignal, ...] = (
        ExactSignal("zone_guess", "zone", 2, key="zone"),)
    eligibility_field: str = "bill_status"
    # Statuses whose FALLBACK date makes a bill expected in the window
    # (engine._expected_bills' co7_due branch); empty disables the branch.
    # EMPTY by default since 2026-09-23, on the same reasoning that took
    # CO7 DONE out of paid_statuses: a payment order can sit as long as a
    # PASSED or REGISTERED bill does, so it is no more a promise of a
    # credit than they are. Only an ADVISED bill (payment made) is
    # expected to be paid. A customer whose source advises differently can
    # put its own statuses back.
    fallback_due_statuses: Tuple[str, ...] = ()
    # Bill identifiers looked for inside a bank text field (see
    # ReferenceSignal). ON by default since 2026-09-30: on the 31 Aug-
    # 22 Sep load every ICF/RCF credit carried its bill number, and in 19
    # of 24 AMBIGUOUS ties the narrative named a bill the matcher had not
    # picked. Weight key "bill_ref" lets the weights dict override it.
    reference_signals: Tuple[ReferenceSignal, ...] = (
        ReferenceSignal("narrative", "bill_number", 8, key="bill_ref"),)

    def to_dict(self) -> dict:
        return {
            "bank_amount_field": self.bank_amount_field,
            "bill_amount_field": self.bill_amount_field,
            "bank_date_field": self.bank_date_field,
            "bill_date_primary": self.bill_date_primary,
            "bill_date_fallback": self.bill_date_fallback,
            "exact_signals": [s.to_dict() for s in self.exact_signals],
            "eligibility_field": self.eligibility_field,
            "fallback_due_statuses": list(self.fallback_due_statuses),
            "reference_signals": [s.to_dict() for s in self.reference_signals],
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "FieldMapping":
        """Partial dict over defaults; unknown keys ignored.
        from_dict(None) == from_dict({}) == FieldMapping()."""
        if not d:
            return cls()
        kwargs = {}
        for key in ("bank_amount_field", "bill_amount_field",
                    "bank_date_field", "bill_date_primary",
                    "eligibility_field"):
            if d.get(key):
                kwargs[key] = d[key]
        if "bill_date_fallback" in d:        # explicit null disables fallback
            kwargs["bill_date_fallback"] = d["bill_date_fallback"] or None
        if "exact_signals" in d:
            kwargs["exact_signals"] = tuple(
                ExactSignal.from_dict(s) for s in d["exact_signals"])
        if "fallback_due_statuses" in d:
            kwargs["fallback_due_statuses"] = tuple(
                d["fallback_due_statuses"] or ())
        if "reference_signals" in d:         # explicit [] turns them off
            kwargs["reference_signals"] = tuple(
                ReferenceSignal.from_dict(s)
                for s in (d["reference_signals"] or ()))
        return cls(**kwargs)


@dataclass(frozen=True)
class MatchRuleSet:
    date_tolerance_days: int = 2
    amount_tolerance: float = 0.0
    window_days: int = 0
    co7_lookback_days: int = 5
    allow_batched: bool = True
    max_batch_size: int = 3
    paid_statuses: FrozenSet[str] = DEFAULT_PAID_STATUSES
    weights: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    field_map: FieldMapping = FieldMapping()
    # Per-customer advisory text overrides, keyed by COPY_SECTIONS then by
    # the stable codes ({"gap_type": {"SIGNAL_BILL_NOT_FOUND": "..."}}).
    # None/{} = the historical defaults (engine.DEFAULT_COPY). Partial
    # dicts merge over defaults at use time (engine.resolve_copy).
    copy_overrides: Optional[dict] = None
    # Subset-sum batching slack in currency units (legacy hardcoded 0.5 —
    # 50 paise); effective slack = max(amount_tolerance, batch_amount_slack)
    batch_amount_slack: float = 0.5
    # Decimal places amounts are rounded to for the amount join
    amount_decimals: int = 2
    # AR view: days past the due date before an open bill shows OVERDUE
    ar_overdue_days: int = 30
    # How long a credit whose only same-amount bill is still IN FLIGHT
    # (PASSED / REGISTERED / CO7 DONE) is excused as "awaiting source
    # status". Past it the status lag is a process problem, not a timing
    # quirk, and the credit counts as an ordinary unmatched exception.
    awaiting_status_days: int = 7
    # Pairing window: a bill is only a candidate for a credit when its
    # compared date (advice, else payment order) lies at most this many
    # days BEFORE the credit's date — and never more than
    # date_tolerance_days AFTER it (money does not arrive before IREPS
    # advises the bank). A bill with no date at all is not judged. None =
    # no window (the historical behaviour): an incremental pool keeps
    # every unconsumed bill, so a bill paid before the bank data starts
    # looks unpaid for ever and pairs, on amount alone, with a much later
    # credit of the same amount.
    max_pairing_gap_days: Optional[int] = None

    def merged(self, overrides: Optional[dict]) -> "MatchRuleSet":
        """A copy with any non-None overrides applied. Unknown keys are
        ignored so a DB row can carry extra columns harmlessly."""
        if not overrides:
            return self
        fields = {k: v for k, v in overrides.items()
                  if v is not None and hasattr(self, k)}
        if "paid_statuses" in fields:
            fields["paid_statuses"] = frozenset(fields["paid_statuses"])
        if "field_map" in fields and isinstance(fields["field_map"], dict):
            fields["field_map"] = FieldMapping.from_dict(fields["field_map"])
        return replace(self, **fields)
