"""
One module per source document. Each returns a plain DataFrame and knows
nothing about matching, so a parser can be used on its own.
"""

from .bank_hsbc import parse_hsbc_statement, bank_selfcheck, extract_zone_from_narrative
from .bill_status import BillStatusFormatError, parse_bill_status
from .lineage import load_rnote, load_crn, attach_lineage

__all__ = [
    "parse_hsbc_statement", "bank_selfcheck", "extract_zone_from_narrative",
    "parse_bill_status", "BillStatusFormatError",
    "load_rnote", "load_crn", "attach_lineage",
]
