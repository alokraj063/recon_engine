"""
Every tunable in one place. Nothing else in the package hardcodes a path
or a threshold, so changing behaviour means changing this object rather
than hunting through modules.
"""

from dataclasses import dataclass, field
from pathlib import Path

PathLike = str | Path


@dataclass
class ReconConfig:
    # --- inputs -------------------------------------------------------
    statement_pdf: PathLike
    bill_status: PathLike
    rnote: PathLike | None = None
    crn: PathLike | None = None
    output_xlsx: PathLike = "Recon_Output.xlsx"

    # Sheet names inside the RNOTE / CRN exports. IREPS names these
    # unhelpfully (e.g. "report (35)"), so 0 means "first sheet".
    rnote_sheet: int | str = 0
    crn_sheet: int | str = 0

    # --- matching -----------------------------------------------------
    date_tolerance_days: int = 2
    amount_tolerance: float = 0.0
    allow_batched: bool = True
    max_batch_size: int = 3

    # --- expected-payment window --------------------------------------
    # Money cannot arrive before IREPS advises the bank, so 0 means the
    # advice date must fall inside the statement's own dates. Widen for a
    # multi-day statement. Too wide and bills that settled earlier get
    # reported as false shortfalls.
    window_days: int = 0

    # How long a CO7 can sit without an advice before we stop expecting
    # the credit in this statement.
    co7_lookback_days: int = 5

    # --- statuses that can produce a credit ---------------------------
    # CO7 DONE is included because the payment order goes out before the
    # export refreshes the status to PAYMENT MADE.
    paid_statuses: frozenset = field(
        default_factory=lambda: frozenset({"PAYMENT MADE", "CO7 DONE"})
    )

    def validate(self):
        missing = [
            name for name, p in (
                ("statement_pdf", self.statement_pdf),
                ("bill_status", self.bill_status),
                ("rnote", self.rnote),
                ("crn", self.crn),
            )
            if p is not None and not Path(p).exists()
        ]
        if missing:
            raise FileNotFoundError(
                "not found: " + ", ".join(f"{n}={getattr(self, n)}" for n in missing)
            )
        return self
