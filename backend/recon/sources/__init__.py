"""Adapter registry: (source_type, adapter_key) -> adapter instance.

Slots reference adapters by ROLE, not exact source_type: any lineage
adapter can serve any lineage_* slot (resolve_adapter). get_adapter keeps
the historical exact lookup for the fixed singleton slots and the CLI.
"""

from .base import SelfCheckError, SilverResult, SourceAdapter, role_of
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
BY_KEY = {a.adapter_key: a for a in _ADAPTERS}


def get_adapter(source_type: str, adapter_key: str) -> SourceAdapter:
    try:
        return REGISTRY[(source_type, adapter_key)]
    except KeyError:
        known = sorted(f"{t}/{k}" for t, k in REGISTRY)
        raise KeyError(
            f"no adapter for source_type='{source_type}' "
            f"adapter_key='{adapter_key}'; registered: {known}"
        ) from None


def resolve_adapter(slot_source_type: str, adapter_key: str) -> SourceAdapter:
    """Adapter for a SLOT: looked up by key, validated by role — so a
    customer's third lineage slot (e.g. lineage_grn) can use any
    registered lineage adapter without a registry entry per slot name."""
    adapter = BY_KEY.get(adapter_key)
    if adapter is None:
        raise KeyError(f"no adapter with key '{adapter_key}'; "
                       f"registered: {sorted(BY_KEY)}")
    if adapter.role != role_of(slot_source_type):
        raise KeyError(
            f"adapter '{adapter_key}' has role '{adapter.role}', slot "
            f"'{slot_source_type}' needs role '{role_of(slot_source_type)}'")
    return adapter


__all__ = ["SourceAdapter", "SilverResult", "SelfCheckError",
           "REGISTRY", "BY_KEY", "get_adapter", "resolve_adapter", "role_of"]
