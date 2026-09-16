"""
Merge duplicate gold bills: a blank bill_number ('-') re-reported by
several daily exports was stored once per export, each copy raising its
own BILL_ONLY exception. db/ingest now matches such a bill on its CO6;
this merges the copies already stored (rules: db/bill_merge.py).

Stop the backend and back up data/*.db before --apply.

    python scripts/merge_duplicate_bills.py [--customer KEY]          # dry run
    python scripts/merge_duplicate_bills.py [--customer KEY] --apply  # write
"""

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from db import SessionLocal, init_db  # noqa: E402
from db.bill_merge import merge_duplicate_bills  # noqa: E402
from db.models import Customer  # noqa: E402

COUNTS = ("groups_found", "groups_merged", "groups_skipped", "bills_deleted",
          "survivors_updated", "exceptions_deleted", "exceptions_carried",
          "match_bills_repointed", "run_match_bills_repointed",
          "ingest_conflicts_repointed", "bill_sightings_repointed",
          "recoveries_deleted")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--customer", help="customer key (default: every customer)")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    init_db()
    with SessionLocal() as s:
        q = select(Customer)
        if args.customer:
            q = q.where(Customer.key == args.customer)
        customers = list(s.execute(q).scalars())
        if not customers:
            print(f"no customer {args.customer!r}")
            return 1
        for c in customers:
            report = merge_duplicate_bills(s, c.id)
            print(f"customer {c.key}: " + ", ".join(
                f"{k} {report.get(k, 0)}" for k in COUNTS))
            for g in report["detail"]:
                print(f"  CO6 {g['submission_ref']}: {g['copies']} copies -> 1 "
                      f"(kept {g['kept']}{', locked' if g['locked'] else ''})")
            for g in report["skipped"]:
                print(f"  SKIPPED CO6 {g['submission_ref']}: {g['reason']}")
        if args.apply:
            s.commit()
            print("applied")
        else:
            s.rollback()
            print("dry run — nothing written (add --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
