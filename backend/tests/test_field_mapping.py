"""
Configurable field mapping: the match signals (amount join, date pair,
exact signals, eligibility) follow FieldMapping instead of hardcoded
columns. Pure-pandas synthetic frames, no DB. The golden-master suite
separately proves the DEFAULT mapping reproduces historical behavior
byte-for-byte.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.engine import _expected_bills, reconcile  # noqa: E402
from recon.matching.matcher import match_bank_to_billstatus  # noqa: E402
from recon.rules import ExactSignal, FieldMapping, MatchRuleSet  # noqa: E402


def _bank(**over):
    row = {"bank_ref": "R1", "narrative": "PAYMENT X", "amount": 1000.0,
           "value_date": datetime(2026, 3, 18), "zone_guess": None,
           "customer_ref": "CUST-42"}
    row.update(over)
    return pd.DataFrame([row])


def _bills(**over):
    row = {"bill_number": "B1", "contract_no": "C1", "zone": None,
           "bill_status": "PAYMENT MADE", "net_payable_amount": 1000.0,
           "gross_amount": 1000.0, "approved_amount": 1000.0,
           "deduction_amount": 0.0,
           "payment_advice_date": pd.Timestamp("2026-03-18"),
           "submission_ref": "6-1",
           "submission_date": pd.Timestamp("2026-03-10"),
           "payment_order_ref": "7-1",
           "payment_order_date": pd.Timestamp("2026-03-15"),
           "vendor_code": "CUST-42", "org_unit": "AU1",
           "recoveries": {}, "recovery_count": 0, "return_reason": None,
           "sheet": "s", "data_row": 1}
    row.update(over)
    return pd.DataFrame([row])


def test_from_dict_roundtrip_and_partials():
    assert FieldMapping.from_dict(None) == FieldMapping()
    assert FieldMapping.from_dict({}) == FieldMapping()
    full = FieldMapping()
    assert FieldMapping.from_dict(full.to_dict()) == full
    partial = FieldMapping.from_dict(
        {"bill_amount_field": "gross_amount", "junk_key": 1})
    assert partial.bill_amount_field == "gross_amount"
    assert partial.bill_date_primary == "payment_advice_date"  # default kept
    # explicit null disables the fallback; empty statuses disable co7_due
    nofb = FieldMapping.from_dict(
        {"bill_date_fallback": None, "fallback_due_statuses": []})
    assert nofb.bill_date_fallback is None
    assert nofb.fallback_due_statuses == ()


def test_merged_precedence_with_field_map():
    rules = (MatchRuleSet()
             .merged({"field_map": {"bill_amount_field": "gross_amount"},
                      "not_a_field": 1})
             .merged({"date_tolerance_days": 5}))
    assert rules.field_map.bill_amount_field == "gross_amount"  # DB map survives
    assert rules.date_tolerance_days == 5                    # form wins
    assert rules.field_map.bill_date_primary == "payment_advice_date"


def test_custom_exact_signal_followed():
    """Remap the exact signal to customer_ref <-> vendor_code: a pair that
    agrees there (but has no zone at all) must confirm the signal under
    the custom mapping and NOT under the default."""
    mapping = FieldMapping(exact_signals=(
        ExactSignal("customer_ref", "vendor_code", 3),))
    results, _ = match_bank_to_billstatus(_bank(), _bills(), mapping=mapping)
    assert len(results) == 1
    r = results[0]
    assert r.zone_check is True                 # all signals agree
    assert r.confidence == "HIGH"               # signal + primary date
    # legacy-named columns carry the remapped signal values
    assert r.zone_from_narrative == "CUST-42"
    assert r.bill_zone == "CUST-42"

    default_results, _ = match_bank_to_billstatus(_bank(), _bills())
    assert default_results[0].zone_check is False   # no zone data
    assert default_results[0].confidence == "LOW"   # date only


def test_empty_signals_never_vacuously_high():
    """all([]) is vacuously True — the guard must keep exact_ok False so
    confidence tops out below HIGH, and gap_type falls back."""
    mapping = FieldMapping(exact_signals=())
    results, _ = match_bank_to_billstatus(_bank(), _bills(), mapping=mapping)
    assert results[0].zone_check is False
    assert results[0].confidence == "LOW"       # date agreed, no signals

    unmatched_bank = _bank(amount=999999.0)     # no amount candidate
    _, unmatched = match_bank_to_billstatus(unmatched_bank, _bills(),
                                            mapping=mapping)
    assert list(unmatched["gap_type"]) == ["NON_IREPS_OR_UNRECOGNISED"]


def test_custom_amount_and_eligibility_fields():
    """bill_amount_field drives eligibility nil-drop + the amount index;
    eligibility_field replaces Status."""
    mapping = FieldMapping(bill_amount_field="gross_amount",
                           eligibility_field="return_reason")
    bills = _bills(gross_amount=1000.0, net_payable_amount=0.0,
                   return_reason="PAYMENT MADE")
    results, _ = match_bank_to_billstatus(
        _bank(), bills, paid_statuses=frozenset({"PAYMENT MADE"}),
        mapping=mapping)
    assert len(results) == 1                    # matched on gross_amount
    # same frames under the default mapping: net_payable_amount==0 -> ineligible
    none_results, unmatched = match_bank_to_billstatus(
        _bank(), bills, paid_statuses=frozenset({"PAYMENT MADE"}))
    assert not none_results and len(unmatched) == 1


def test_expected_bills_follows_mapping():
    bank = _bank()
    # primary date remapped to bill_date; bill advised via bill_date only
    mapping = FieldMapping(bill_date_primary="bill_date")
    bills = _bills(bill_date=pd.Timestamp("2026-03-18"),
                   payment_advice_date=pd.NaT)
    exp = _expected_bills(bills, bank, 0, 5, mapping)
    assert len(exp) == 1 and exp["ExpectedBasis"].iloc[0] == "ADVICE_DATE"
    # default mapping: no advice date; co7_due needs bill_status in the
    # fallback_due_statuses ("CO7 DONE") AND CO7Date within lookback
    co7_bills = bills.assign(bill_status="CO7 DONE")
    exp_default = _expected_bills(co7_bills, bank, 0, 5, FieldMapping())
    assert list(exp_default["ExpectedBasis"]) == ["CO7_ISSUED_NO_ADVICE"]
    # a paid-but-not-CO7-DONE status never enters via the fallback date
    assert _expected_bills(bills, bank, 0, 5, FieldMapping()).empty
    # fallback disabled -> nothing expected even for CO7 DONE bills
    exp_nofb = _expected_bills(
        co7_bills, bank, 0, 5, FieldMapping(bill_date_fallback=None))
    assert exp_nofb.empty
    # custom fallback_due_statuses widen the branch
    exp_custom = _expected_bills(
        bills, bank, 0, 5,
        FieldMapping(fallback_due_statuses=("PAYMENT MADE",)))
    assert list(exp_custom["ExpectedBasis"]) == ["CO7_ISSUED_NO_ADVICE"]


def test_reconcile_threads_field_map():
    """End-to-end through reconcile(): custom signal drives confidence."""
    mapping = FieldMapping(exact_signals=(
        ExactSignal("customer_ref", "vendor_code", 3),))
    out = reconcile(_bank(), _bills(), field_map=mapping)
    assert len(out["matched"]) == 1
    assert out["matched"]["confidence"].iloc[0] == "HIGH"
    out_default = reconcile(_bank(), _bills())
    assert out_default["matched"]["confidence"].iloc[0] == "LOW"
