"""
Receivables reconciliation: HSBC daily statement against the IREPS
Bill Status export, with RNOTE / CRN lineage attached.

Typical use:

    from recon import ReconConfig, run

    cfg = ReconConfig(
        statement_pdf="Daily_statement_18Mar2026.PDF",
        bill_status="BILL STATUS 20032026.xlsx",
        rnote="RNOTE IREPS 31032026.xlsx",
        crn="CRN IREPS 31032026.xlsx",
    )
    out = run(cfg)
    out["summary"]

Or from a shell:

    python -m recon --statement x.PDF --bills y.xlsx --rnote z.xlsx --crn w.xlsx
"""

from .config import ReconConfig
from .engine import reconcile, exception_queue, run
from .matching import MatchResult, match_bank_to_billstatus, results_to_frame
from .parsers import (
    parse_hsbc_statement,
    bank_selfcheck,
    parse_bill_status,
    load_rnote,
    load_crn,
    attach_lineage,
)
from .report import write_workbook

__version__ = "1.0.0"

__all__ = [
    "ReconConfig", "reconcile", "exception_queue", "run",
    "MatchResult", "match_bank_to_billstatus", "results_to_frame",
    "parse_hsbc_statement", "bank_selfcheck", "parse_bill_status",
    "load_rnote", "load_crn", "attach_lineage", "write_workbook",
]
