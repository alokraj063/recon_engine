"""Adapter registry: (source_type, adapter_key) -> adapter instance."""

from .base import SelfCheckError, SilverResult, SourceAdapter
from .hsbc_bank import HsbcBankAdapter
from .ireps_bills import IrepsBillsAdapter
from .ireps_crn import IrepsCrnAdapter
from .ireps_rnote import IrepsRnoteAdapter

_ADAPTERS = (
    HsbcBankAdapter(),
    IrepsBillsAdapter(),
    IrepsRnoteAdapter(),
    IrepsCrnAdapter(),
)

REGISTRY = {(a.source_type, a.adapter_key): a for a in _ADAPTERS}


def get_adapter(source_type: str, adapter_key: str) -> SourceAdapter:
    try:
        return REGISTRY[(source_type, adapter_key)]
    except KeyError:
        known = sorted(f"{t}/{k}" for t, k in REGISTRY)
        raise KeyError(
            f"no adapter for source_type='{source_type}' "
            f"adapter_key='{adapter_key}'; registered: {known}"
        ) from None


__all__ = ["SourceAdapter", "SilverResult", "SelfCheckError",
           "REGISTRY", "get_adapter"]
