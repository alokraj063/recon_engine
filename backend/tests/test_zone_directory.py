"""
The customer's zone directory (db/zones.py): every Analyst-queue row
carries its zone's segment (TSG / OE) resolved through the customer's
own, editable directory — case-insensitive, following aliases — and an
edit is audited per field.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import zones  # noqa: E402
from tests.test_multi_statement import bills, world  # noqa: E402,F401
from tests.test_unrecognised_receipts import _ingest, _reconcile, credits  # noqa: E402


def test_lookup_is_case_insensitive_and_follows_aliases():
    table = zones.lookup(zones.DEFAULT_ZONE_DIRECTORY)
    assert zones.resolve(table, "SCoR")["segment"] == "TSG"
    # IREPS spells Northeast Frontier NFR; the directory's code is NEFR
    assert zones.resolve(table, "NFR")["code"] == "NEFR"
    assert zones.resolve(table, "icf")["segment"] == "OE"
    assert zones.resolve(table, "MTPK") is None
    assert zones.resolve(table, None) is None
    assert zones.resolve(table, "  ") is None


def test_normalize_rejects_duplicates_and_missing_segment():
    with pytest.raises(zones.ZoneDirectoryError):
        zones.normalize([{"code": "CR", "segment": "TSG"},
                         {"code": "XX", "segment": "OE", "aliases": ["cr"]}])
    with pytest.raises(zones.ZoneDirectoryError):
        zones.normalize([{"code": "CR", "segment": " "}])
    out = zones.normalize([{"code": " cr ", "segment": "tsg", "aliases": ["CR", "c-r"]}])
    assert out == {"CR": {"name": None, "region": None, "segment": "TSG",
                          "aliases": ["C-R"]}}


def _ledger(client, key):
    r = client.get("/api/ledger", params={"customer_id": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_ledger_rows_carry_the_customers_segment(world):  # noqa: F811
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    # B1 (NR, 5000) is paid by C1; C2 has an ICF zone and no bill;
    # B2 (NR) is left unpaid
    _ingest(pk, {"bills": bills(["B1", "B2"])}, {"bills": bz["bills"]})
    _ingest(pk, {"bank_txns": credits([("C1", 5000.0, "NR"), ("C2", 7000.0, "ICF")])},
            {"bank_txns": bz["a"]})
    _reconcile(client, key, bz["a"])

    data = _ledger(client, key)
    (m,) = data["matches"]
    assert m["zone_info"]["segment"] == "TSG" and m["zone_info"]["code"] == "NR"
    by_type = {e["exception_type"]: e for e in data["exceptions"]}
    assert by_type["BANK_ONLY"]["zone_info"]["segment"] == "OE"
    assert by_type["BILL_ONLY"]["zone_info"]["segment"] == "TSG"

    # re-classify NR: the queue follows at once, no reconcile needed
    cfg = client.get(f"/api/customers/{key}/zones").json()
    assert cfg["is_default"] is True
    edited = [{**z, "segment": "OE"} if z["code"] == "NR" else z for z in cfg["zones"]]
    r = client.put(f"/api/customers/{key}/zones", json={"zones": edited})
    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is False
    (m,) = _ledger(client, key)["matches"]
    assert m["zone_info"]["segment"] == "OE"

    ev = [e for e in client.get("/api/audit", params={"customer_id": key}).json()
          if e["event_type"] == "config.zones_updated"]
    assert len(ev) == 1 and ev[0]["entity_label"] == "Zone directory"
    assert ev[0]["details"]["changes"] == [
        {"field": "zone_directory.NR.segment", "from": "TSG", "to": "OE"}]

    # an invalid directory is refused and changes nothing
    r = client.put(f"/api/customers/{key}/zones",
                   json={"zones": [{"code": "NR", "segment": ""}]})
    assert r.status_code == 400
    # reset returns to the defaults
    r = client.put(f"/api/customers/{key}/zones", json={"zones": None})
    assert r.status_code == 200 and r.json()["is_default"] is True
    (m,) = _ledger(client, key)["matches"]
    assert m["zone_info"]["segment"] == "TSG"
