"""
One-shot proof for the Phase-2 lineage canonicalization re-baseline.

Compares the NEW engine output on the sample documents against the
COMMITTED golden CSVs (pre-canonicalization) and asserts the change is
exactly what the plan promises:

  - bills_enriched / queue: the raw RN_* / CR_* source columns disappear,
    the trail gains Invoice_Date / Bill_Reg_Date; every SHARED column is
    value-identical row for row.
  - matched: gains Invoice_Date / Bill_Reg_Date; shared columns identical.
  - summary / bank / recoveries: byte-identical.

Run from backend/ BEFORE regenerating the goldens:
    ../.venv/bin/python scripts/verify_lineage_canonicalization.py
"""

import io
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd  # noqa: E402

import make_golden  # noqa: E402

# legacy raw lineage columns that leave bills_enriched / queue
LEGACY_LINEAGE_RE = re.compile(r"^(RN_|CR_|RNote|CRN[ND])")
ADDED = {"Invoice_Date", "Bill_Reg_Date"}


def load_csv(text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def compare(name: str, old: pd.DataFrame, new: pd.DataFrame) -> list:
    problems = []
    old_cols, new_cols = list(old.columns), list(new.columns)
    dropped = [c for c in old_cols if c not in new_cols]
    added = [c for c in new_cols if c not in old_cols]

    bad_drops = [c for c in dropped
                 if not (LEGACY_LINEAGE_RE.match(c)
                         and not c.endswith("_MatchedVia"))]
    bad_adds = [c for c in added if c not in ADDED]
    if bad_drops:
        problems.append(f"{name}: unexpected dropped columns {bad_drops}")
    if bad_adds:
        problems.append(f"{name}: unexpected added columns {bad_adds}")
    if len(old) != len(new):
        problems.append(f"{name}: row count {len(old)} -> {len(new)}")
        return problems

    shared = [c for c in old_cols if c in new_cols]
    for c in shared:
        if c == "Receipt_Qty":
            # legacy accident: the old per-type join upcast CRN's integer
            # quantities to float ("2.0"); the unified frame keeps them
            # int ("2") — numerically identical, and consistent with the
            # gold-sourced paths (receipt_qty is a String column there)
            def qty(s):
                as_num = pd.to_numeric(s, errors="coerce")
                return s.where(as_num.isna(), as_num.astype(str))
            if qty(old[c]).equals(qty(new[c])):
                continue
        if not old[c].equals(new[c]):
            diff = (old[c] != new[c])
            idx = list(old.index[diff])[:5]
            problems.append(
                f"{name}.{c}: {int(diff.sum())} differing rows, e.g. rows "
                f"{idx}: old={list(old.loc[idx, c])} "
                f"new={list(new.loc[idx, c])}")
    return problems


def main():
    frames = make_golden.build_frames()
    problems = []
    for name in make_golden.FRAMES:
        old_path = make_golden.GOLDEN_DIR / f"{name}.csv"
        if not old_path.exists():
            problems.append(f"{name}: missing committed golden CSV")
            continue
        old = load_csv(old_path.read_text(encoding="utf-8"))
        new = load_csv(make_golden.normalize(frames[name]))
        if name in ("summary", "bank", "recoveries"):
            if make_golden.normalize(frames[name]) != \
                    old_path.read_text(encoding="utf-8"):
                problems.append(f"{name}: expected byte-identical, differs")
            continue
        problems.extend(compare(name, old, new))

    if problems:
        print("VERIFICATION FAILED:")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("verified: pure column drop/add re-baseline — shared values "
          "identical, summary/bank/recoveries byte-identical")


if __name__ == "__main__":
    main()
