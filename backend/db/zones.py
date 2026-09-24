"""
The customer's zone directory: which railway zone / production unit a
zone code names, its region, and its business SEGMENT (TSG | OE, or any
label the customer uses). Display-only reference data — the matcher
never reads it, so it lives here beside the other read-time views, not
in recon/rules.py, and changing it needs no reconcile.

Stored on match_rule_sets.zone_directory as {code: {name, region,
segment, aliases}} (keyed by code so an audit diff reads
"zone_directory.CR.segment"); NULL = DEFAULT_ZONE_DIRECTORY. Lookups are
case-insensitive and follow aliases, because the data spells codes more
than one way: IREPS writes NFR where the directory says NEFR, and both
SCoR and SCOR appear.
"""

from typing import Optional

# The directory as supplied on 2026-09-24. ER is not in that list but is
# a zonal railway with live bills and credits, so it defaults like the
# other zonal railways (EAST / TSG); MTPK is left unmapped on purpose.
# DLW / DMW are the pre-2020 names of BLW / PLW.
DEFAULT_ZONE_DIRECTORY = {
    "CR": {"name": "Central Railway", "region": "WEST", "segment": "TSG", "aliases": []},
    "WR": {"name": "Western Railway", "region": "WEST", "segment": "TSG", "aliases": []},
    "ECR": {"name": "East Central Railway", "region": "EAST", "segment": "TSG", "aliases": []},
    "ECOR": {"name": "East Coast Railway", "region": "EAST", "segment": "TSG", "aliases": []},
    "ER": {"name": "Eastern Railway", "region": "EAST", "segment": "TSG", "aliases": []},
    "NR": {"name": "Northern Railway", "region": "NORTH", "segment": "TSG", "aliases": []},
    "NCR": {"name": "North Central Railway", "region": "NORTH", "segment": "TSG", "aliases": []},
    "NER": {"name": "North Eastern Railway", "region": "NORTH", "segment": "TSG", "aliases": []},
    "NEFR": {"name": "Northeast Frontier Railway", "region": "EAST", "segment": "TSG", "aliases": ["NFR"]},
    "NWR": {"name": "North Western Railway", "region": "NORTH", "segment": "TSG", "aliases": []},
    "SR": {"name": "Southern Railway", "region": "SOUTH", "segment": "TSG", "aliases": []},
    "SCR": {"name": "South Central Railway", "region": "SOUTH", "segment": "TSG", "aliases": []},
    "SER": {"name": "South Eastern Railway", "region": "EAST", "segment": "TSG", "aliases": []},
    "SECR": {"name": "South East Central Railway", "region": "EAST", "segment": "TSG", "aliases": []},
    "SWR": {"name": "South Western Railway", "region": "SOUTH", "segment": "TSG", "aliases": []},
    "SCOR": {"name": "South Coast Railway", "region": "SOUTH", "segment": "TSG", "aliases": []},
    "WCR": {"name": "West Central Railway", "region": "NORTH", "segment": "TSG", "aliases": []},
    "KRCL": {"name": "Konkan Railway", "region": "WEST", "segment": "TSG", "aliases": []},
    "KMRL": {"name": "Kochi Metro Rail Limited", "region": "SOUTH", "segment": "TSG", "aliases": []},
    "KMRCL": {"name": "Kolkata Metro Rail Corporation Limited", "region": "EAST", "segment": "TSG", "aliases": []},
    "ICF": {"name": "Integral Coach Factory", "region": "SOUTH", "segment": "OE", "aliases": []},
    "PLW": {"name": "Patiala Locomotive Works", "region": "NORTH", "segment": "OE", "aliases": ["DMW"]},
    "CLW": {"name": "Chittaranjan Locomotive Works", "region": "EAST", "segment": "OE", "aliases": []},
    "MCF": {"name": "Modern Coach Factory", "region": "NORTH", "segment": "OE", "aliases": []},
    "BLW": {"name": "Banaras Locomotive Works", "region": "NORTH", "segment": "OE", "aliases": ["DLW"]},
    "RCF": {"name": "Rail Coach Factory", "region": "NORTH", "segment": "OE", "aliases": []},
}


class ZoneDirectoryError(ValueError):
    pass


def _key(code) -> str:
    return str(code).strip().upper()


def effective_directory(rule_row) -> dict:
    stored = getattr(rule_row, "zone_directory", None) if rule_row is not None else None
    return stored if stored else DEFAULT_ZONE_DIRECTORY


def lookup(directory: dict) -> dict:
    """UPPER(code or alias) -> {code, name, region, segment}."""
    out = {}
    for code, e in directory.items():
        info = {"code": code, "name": e.get("name"), "region": e.get("region"),
                "segment": e.get("segment")}
        out[_key(code)] = info
        for alias in e.get("aliases") or []:
            out.setdefault(_key(alias), info)
    return out


def resolve(table: dict, zone) -> Optional[dict]:
    if zone is None or not str(zone).strip():
        return None
    return table.get(_key(zone))


def normalize(entries: list) -> dict:
    """API list [{code, name, region, segment, aliases}] -> stored dict.
    Codes and aliases are upper-cased; every code/alias must be unique
    across the whole directory, and every entry needs a segment."""
    out: dict = {}
    seen: set = set()
    for i, e in enumerate(entries):
        code = _key(e.get("code") or "")
        if not code:
            raise ZoneDirectoryError(f"row {i + 1}: code is required")
        segment = str(e.get("segment") or "").strip().upper()
        if not segment:
            raise ZoneDirectoryError(f"{code}: segment is required")
        aliases = sorted({_key(a) for a in (e.get("aliases") or []) if str(a).strip()}
                         - {code})
        for k in [code, *aliases]:
            if k in seen:
                raise ZoneDirectoryError(f"'{k}' appears more than once")
            seen.add(k)
        out[code] = {"name": str(e.get("name") or "").strip() or None,
                     "region": str(e.get("region") or "").strip().upper() or None,
                     "segment": segment, "aliases": aliases}
    return out


def as_list(directory: dict) -> list:
    return [{"code": code, **{k: e.get(k) for k in ("name", "region", "segment")},
             "aliases": list(e.get("aliases") or [])}
            for code, e in sorted(directory.items())]
