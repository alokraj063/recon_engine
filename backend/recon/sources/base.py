"""
Source adapters: the seam where a new bank or PO system plugs in.

An adapter owns one input kind for one source system: parse the raw file
into silver (the source's native shape), transform silver into the gold
schema, and optionally self-check the parse against control totals the
document itself carries. Parsers stay where they are; adapters are thin
call-throughs so adding a source never touches matching or the engine.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

import pandas as pd


class SelfCheckError(ValueError):
    """The parse does not tie to the document's own control totals.
    ValueError subclass so existing callers that catch ValueError keep
    working (the API maps it to BANK_SELFCHECK_FAILED)."""

    def __init__(self, message: str, check: Optional[dict] = None):
        super().__init__(message)
        self.check = check


class SilverResult:
    """Parsed output in the source's native shape: one or more frames
    plus whatever metadata the parse produced."""

    def __init__(self, frames: Dict[str, pd.DataFrame],
                 meta: Optional[dict] = None):
        self.frames = frames
        self.meta = meta or {}


class SourceAdapter(ABC):
    source_type: str        # bank_statement | bill_status | lineage_rnote | lineage_crn
    adapter_key: str        # e.g. hsbc, ireps
    label: str = ""         # human name for UI dropdowns (falls back to key)
    system: str = ""        # source family, e.g. "HSBC", "IREPS" — the UI
                            # groups an ERP's documents by this
    file_kinds: Tuple[str, ...] = ()

    @abstractmethod
    def parse(self, path, params: dict) -> SilverResult:
        """Raw file -> silver frames."""

    @abstractmethod
    def to_gold(self, silver: SilverResult, params: dict) -> Dict[str, pd.DataFrame]:
        """Silver -> gold frames (adds row_seq, applies ensure_schema)."""

    def selfcheck(self, gold: Dict[str, pd.DataFrame], path,
                  params: dict) -> Optional[dict]:
        """Validate the parse against control totals in the document.
        Raise SelfCheckError to fail the run loudly; return the check
        details (or None when the source has no control totals)."""
        return None
