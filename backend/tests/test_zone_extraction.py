"""
Zone / production-unit extraction from HSBC NEFT narratives
(recon/parsers/bank_hsbc.extract_zone_from_narrative): the three ordered
rules on real narrative shapes, and golden PARITY — every row of the
sample statement's snapshot (tests/golden/bank.csv) must still get the
value it has, so the golden CSVs need no re-baseline for this change.
"""

import csv
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from recon.parsers.bank_hsbc import extract_zone_from_narrative as zone  # noqa: E402

CASES = [
    # Rule A — zonal railway with a station, zone glued to the description
    ("NEFT FROM 3003DHN ECRSET OF PRESSUR SBINN52026071008659701 SBOI", "ECR"),
    ("NEFT FROM 0911NED SCRMATERIAL SUPPL SBINN520260702", "SCR"),
    ("NEFT FROM 0406GKPW NER4th Bill of A SBINN5", "NER"),
    ("NEFT FROM 3712CTPTY SCoRSet of WSP SBINN52026080660398791", "SCOR"),
    ("NEFT FROM 3102KUR ECoRLABOUR Charge SBINN52026082494824387", "ECOR"),
    ("NEFT FROM 3406MBWS SECRSPARES SBINN5", "SECR"),
    ("NEFT FROM HQ CRSet of Maint Kitsfor SBINN520260630", "CR"),
    ("NEFT FROM 3601HQ WCRN2 BOARD SBINN5202607131284250", "WCR"),
    ("NEFT FROM 3105MCS ECoRPARTS SBINN5", "ECOR"),      # MCS is a station, not MCF
    # Rule B — production unit glued after the prefix, no station
    ("NEFT FROM 2001MCFaxle mounted disc SBINN52026072939767358 SBOI /ATTN", "MCF"),
    ("NEFT FROM 1101CLWHIGH REACH PANTOGR SBINN52026070291743281", "CLW"),
    ("NEFT FROM 2501RCFEP BRAKE SYSTEM ME SBINN52026071008508016", "RCF"),
    ("NEFT FROM 1301ICF1331000197 100 In SBINN52026072837723162", "ICF"),
    ("NEFT FROM 1501DMW13310001 SBINN5", "DMW"),
    ("NEFT FROM RCFLHB BRAKE SYSTEM SLR W SBINN52026071415036664", "RCF"),
    ("NEFT FROM MCFAxle mounted disc SBINN5", "MCF"),
    ("NEFT FROM ICF332026010 SBINN5", "ICF"),
    ("NEFT FROM CLWHIGH REACH PANTOGRAPH SBINN52026080147644772", "CLW"),
    # the historical false positive: SCREW is not SCR — this is a CLW payment
    ("NEFT FROM 1101CLWHEX SOC HD SCREW 5 SBINN520260903", "CLW"),
    # a lowercase description word must never read as a zone (Rule A is
    # case-sensitive); nothing else in the head -> the legacy search, which
    # is word-boundary anchored -> None
    ("NEFT FROM 9999XYZ Erection works SBINN5", None),
    # non-IREPS credits stay blank
    ("NEFT FROM SIEMENS LIMITED DEUTN52026063001055188 DEUT", None),
    ("DEPOSIT INTEREST 071-002786-097 CLUSTER DEPOSIT ROLLED OVER", None),
    ("NEFT FROM EPAO CUSTOMS DUTY DRAWBAC SBINN52026070290772430", None),
    ("NEFT FROM DELHI METRO RAIL CORPORAT ICICN22026072416933801", None),
    ("NEFT FROM ICICI SECURITIES LTD ICICN2", None),          # ICI.. is not ICF
    ("", None),
    (None, None),
]


def test_unit_code_in_a_vendor_name_is_not_a_unit():
    """Rules A/B only apply after 'NEFT FROM' (the IREPS payer prefix);
    a vendor whose name starts with a unit code keeps whatever the legacy
    search says — never DMW."""
    assert zone("DMW CNC SOLUTIONS INDIA (P) LTD D218000279350002 /5750000103") != "DMW"


@pytest.mark.parametrize("narrative,expected", CASES)
def test_extraction(narrative, expected):
    assert zone(narrative) == expected


GOLDEN = BACKEND / "tests" / "golden" / "bank.csv"


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden bank.csv not generated")
def test_golden_parity_on_the_sample_statement():
    """Every credit of the sample statement keeps its snapshot value
    (zoned AND blank rows). A difference here means the golden CSVs
    would move — stop and re-baseline deliberately, never silently."""
    with GOLDEN.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, "empty golden bank.csv"
    diffs = [(r["narrative"], r["zone_guess"], zone(r["narrative"]))
             for r in rows if (zone(r["narrative"]) or "") != (r["zone_guess"] or "")]
    assert not diffs, f"{len(diffs)} golden rows would change: {diffs[:5]}"
