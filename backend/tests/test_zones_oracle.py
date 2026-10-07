"""SCoR has no Oracle customer: oracle_zone books it under its legacy railway."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import zones as z  # noqa: E402

TABLE = z.lookup(z.DEFAULT_ZONE_DIRECTORY)


def code(info):
    return info and info["code"]


def test_ordinary_zone_is_itself():
    assert code(z.oracle_zone(TABLE, "NFR")) == "NEFR"
    assert z.oracle_zone(TABLE, "NOPE") is None


def test_scor_reads_the_ar_customer_first():
    info = z.oracle_zone(TABLE, "SCoR", ar_customer="EAST COAST RAILWAY",
                         division="GUNTKAL DIVISION")      # AR wins over division
    assert code(info) == "ECOR" and info["legacy"] and info["region"] == "EAST"
    assert code(z.oracle_zone(TABLE, "SCOR", ar_customer="south  central railway")) == "SCR"


def test_scor_falls_back_to_the_division():
    for division, want in [("VISHKHAPATANAM DIVISION", "ECOR"),
                           ("GUNTKAL DIVISION", "SCR"),
                           ("VIJAYWADA DIVISION", "SCR"),
                           ("CARRIAGE REPAIR SHOP,TIRUPATI", "SCR")]:
        assert code(z.oracle_zone(TABLE, "SCoR", division=division)) == want


def test_scor_unresolved_is_none_not_a_guess():
    # an AR name that is not a directory railway (a depot-level customer)
    assert z.oracle_zone(TABLE, "SCoR", ar_customer="DY.CMM/GSD/SCR/METTUGUDA") is None
    assert z.oracle_zone(TABLE, "SCoR") is None
    assert z.needs_legacy_zone(TABLE, "SCoR") and not z.needs_legacy_zone(TABLE, "SR")
