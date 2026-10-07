"""
Backfill gold recovery lines the old ingest rule dropped (a bill first
exported before its deductions kept no per-head lines). Replays each
bill-status file's silver rows, each bill from the newest file that
reported it (rules: db/recovery_backfill.py).

Stop the backend and back up data/*.db before --apply.

    python scripts/backfill_recovery_lines.py [--customer KEY]          # dry run
    python scripts/backfill_recovery_lines.py [--customer KEY] --apply  # write
"""

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from db import SessionLocal, init_db  # noqa: E402
from db.models import Customer  # noqa: E402
from db.recovery_backfill import backfill_recovery_lines  # noqa: E402

COUNTS = ("files_replayed", "files_skipped", "bills_refreshed", "locked_filled",
          "lines_inserted", "lines_deleted")


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
            report = backfill_recovery_lines(s, c.id)
            print(f"customer {c.key}: " + ", ".join(
                f"{k} {report[k]}" for k in COUNTS))
            for sk in report["skipped"]:
                print(f"  SKIPPED {sk['file']}: {sk['reason']}")
        if args.apply:
            s.commit()
            print("applied")
        else:
            s.rollback()
            print("dry run — nothing written (add --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
