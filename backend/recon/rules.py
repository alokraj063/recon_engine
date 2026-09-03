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

DEFAULT_PAID_STATUSES = frozenset({"PAYMENT MADE", "CO7 DONE"})
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
    # statuses whose FALLBACK date makes a bill expected in the window
    # (engine._expected_bills' co7_due branch); empty disables the branch
    fallback_due_statuses: Tuple[str, ...] = ("CO7 DONE",)

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
