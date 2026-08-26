"""
Gold-canonicalization purity check (dev-only; delete after the migration
settles). Proves the regenerated golden CSVs differ from the pre-rename
baseline by PURE column renames — identical values, identical row order.

Usage:
    python scripts/verify_rename.py <baseline_dir> [<new_dir>]

<baseline_dir> holds the pre-rename golden CSVs; <new_dir> defaults to
tests/golden. Any value difference means the rename leaked into engine
behaviour and the refactor must stop.

ENGINE_RENAMES below is THE reference map for the whole refactor — every
code change is checked against it.
"""

import json
import sys
from pathlib import Path

import pandas as pd

# old engine name -> canonical name. Anything not listed must be unchanged.
ENGINE_RENAMES = {
    # bills frame
    "BillNumber": "bill_number",
    "BillDate": "bill_date",
    "ContractNo": "contract_no",
    "ContractDate": "contract_date",
    "CO6No": "submission_ref",
    "CO6Date": "submission_date",
    "CO7No": "payment_order_ref",
    "CO7Date": "payment_order_date",
    "PaymentAdviceDateToBank": "payment_advice_date",
    "PartyCode": "vendor_code",
    "PartyName": "vendor_name",
    "AccountingUnit": "org_unit",
    "Status": "bill_status",
    "ReasonForReturn": "return_reason",
    "BillAmt": "gross_amount",
    "PassedAmt": "approved_amount",
    "DeductedAmt": "deduction_amount",
    "NetAmt": "net_payable_amount",
    "Recoveries": "recoveries",
    "RecoveryCount": "recovery_count",
    "RecoverySum": "recovery_sum",
    "NetCheck": "net_check",
    "RecoveryCheck": "recovery_check",
    "Sheet": "sheet",
    "HeaderRow": "header_row",
    "DataRow": "data_row",
    "UnparsedHeader": "unparsed_header",
    # recoveries frame
    "BillIndex": "bill_index",
    "RecoveryHead": "recovery_head",
    "RecoveryAmt": "recovery_amt",
    "RecoveryText": "recovery_text",
    # bank frame
    "UsedInRecon": "used_in_recon",
    # MatchResult fields (matched frame + review rows in the queue)
    "co7_no": "payment_order_ref",
    "co7_date": "payment_order_date",
    "advice_date": "payment_advice_date",
    "accounting_unit": "org_unit",
    "status": "bill_status",
    # queue display spine
    "Bank_Ref": "bank_ref",
    "Bank_Narrative": "bank_narrative",
    "Amount": "amount",
    "Value_Date": "value_date",
    "Zone": "zone",
}

# matched frame: the bill-side display column AccountingUnit duplicated
# MatchResult.accounting_unit; under one vocabulary they would collide, so
# MATCH_SIDE_COLS dropped it. Allowed ONLY if its values matched the kept
# column in the baseline.
ALLOWED_DROPS = {"matched": [("AccountingUnit", "accounting_unit")]}

# queue: bill_only rows filled AccountingUnit, review rows accounting_unit
# (with AccountingUnit alongside, equal). Both now land in one org_unit
# column — verified as a coalesce of the two baseline columns.
MERGES = {"queue": [(("AccountingUnit", "accounting_unit"), "org_unit")]}

# renames that must NOT apply to a given frame (same literal, different
# meaning): summary's Amount is its own display column, not the queue spine
FRAME_MAP_EXCLUDE = {"summary": {"Amount"}}

# columns whose cells embed dicts keyed by renamed field names
JSON_COLS = {"queue": ["Candidates"], "bills_enriched": ["Attempts"]}

FRAMES = ["summary", "matched", "queue", "bills_enriched", "bank", "recoveries"]


def _map_json(cell: str) -> str:
    """Rename keys inside a serialized list-of-dicts cell, re-normalized."""
    if cell == "" or pd.isna(cell):
        return cell
    data = json.loads(cell)
    if isinstance(data, list):
        data = [{ENGINE_RENAMES.get(k, k): v for k, v in d.items()}
                if isinstance(d, dict) else d for d in data]
    return json.dumps(data, sort_keys=True)


def _renorm_json(cell: str) -> str:
    if cell == "" or pd.isna(cell):
        return cell
    return json.dumps(json.loads(cell), sort_keys=True)


def check_frame(name: str, old_dir: Path, new_dir: Path) -> list[str]:
    errors: list[str] = []
    old = pd.read_csv(old_dir / f"{name}.csv", dtype=str, keep_default_na=False)
    new = pd.read_csv(new_dir / f"{name}.csv", dtype=str, keep_default_na=False)
    excluded = FRAME_MAP_EXCLUDE.get(name, set())
    rename = {k: v for k, v in ENGINE_RENAMES.items() if k not in excluded}

    for dropped, kept in ALLOWED_DROPS.get(name, []):
        if dropped in old.columns:
            if not old[dropped].equals(old[kept]):
                errors.append(f"{name}: dropped column {dropped!r} did not "
                              f"duplicate {kept!r} in the baseline")
            old = old.drop(columns=[dropped])

    for (col_a, col_b), target in MERGES.get(name, []):
        if col_a in old.columns and col_b in old.columns:
            a, b = old[col_a], old[col_b]
            clash = (a != "") & (b != "") & (a != b)
            if clash.any():
                errors.append(f"{name}: merge {col_a}/{col_b} -> {target} has "
                              f"conflicting values at rows {list(clash[clash].index[:5])}")
            merged = a.where(a != "", b)
            # merged column takes the FIRST old column's position
            old[col_a] = merged
            old = old.drop(columns=[col_b])
            rename = {**rename, col_a: target}

    expected = [rename.get(c, c) for c in old.columns]
    if list(new.columns) != expected:
        errors.append(
            f"{name}: header mismatch\n  expected: {expected}\n  got:      {list(new.columns)}")
        return errors

    old.columns = expected
    for col in expected:
        o, n = old[col], new[col]
        if col in JSON_COLS.get(name, []):
            o = o.map(_map_json)
            n = n.map(_renorm_json)
        if not o.equals(n):
            diff = (o != n)
            idx = list(diff[diff].index[:5])
            errors.append(f"{name}: VALUE diff in column {col!r} at rows {idx} "
                          f"(e.g. {o.iloc[idx[0]]!r} -> {n.iloc[idx[0]]!r})")
    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    old_dir = Path(sys.argv[1])
    new_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        Path(__file__).resolve().parents[1] / "tests" / "golden")
    all_errors: list[str] = []
    for name in FRAMES:
        errs = check_frame(name, old_dir, new_dir)
        status = "PURE RENAME" if not errs else "FAILED"
        print(f"{name:16s} {status}")
        all_errors += errs
    for e in all_errors:
        print("ERROR:", e)
    print("PASS: all frames are pure renames" if not all_errors
          else f"FAIL: {len(all_errors)} problem(s)")
    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
