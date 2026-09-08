"""
Backfill gold.bank_txns.zone_guess from each row's narrative with the
CURRENT extraction rules (recon/parsers/bank_hsbc.extract_zone_from_narrative).

Why a backfill: zone extraction happens at parse time, and re-uploading a
statement is deduped by bronze (same bytes -> same row, never re-parsed),
so already-ingested credits would keep their old zone forever. Silver is
untouched (write-only audit of what was parsed); run frames are frozen and
untouched — the NEXT reconciliation sees the new zones.

    python scripts/rezone_bank_txns.py [--customer KEY]          # dry run
    python scripts/rezone_bank_txns.py [--customer KEY] --apply  # write
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from db import SessionLocal, init_db  # noqa: E402
from db.audit import record_event  # noqa: E402
from db.models import Customer, GoldBankTxn  # noqa: E402
from logging_setup import get_logger  # noqa: E402
from recon.parsers.bank_hsbc import extract_zone_from_narrative  # noqa: E402

logger = get_logger("scripts.rezone")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--customer", help="customer key (default: every customer)")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--samples", type=int, default=20)
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
            rows = list(s.execute(
                select(GoldBankTxn).where(GoldBankTxn.customer_id == c.id)).scalars())
            counts = Counter()
            filled = Counter()
            samples = []
            for t in rows:
                new = extract_zone_from_narrative(t.narrative)
                old = t.zone_guess or None
                if new == old:
                    counts["unchanged"] += 1
                    continue
                kind = ("filled" if old is None else
                        "cleared" if new is None else "changed")
                counts[kind] += 1
                if kind == "filled":
                    filled[new] += 1
                if len(samples) < args.samples:
                    samples.append((kind, old, new, (t.narrative or "")[:60]))
                if args.apply:
                    t.zone_guess = new
            print(f"customer {c.key}: {len(rows)} bank txns — "
                  + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
            if filled:
                print("  filled with: " + ", ".join(
                    f"{z} {n}" for z, n in filled.most_common()))
            for kind, old, new, narr in samples:
                print(f"  {kind:9} {old or '-':>5} -> {new or '-':<5} {narr}")
            if args.apply and (counts["filled"] or counts["changed"] or counts["cleared"]):
                record_event(s, logger, event_type="gold.zone_backfilled",
                             customer_id=c.id,
                             details={k: counts[k] for k in ("filled", "changed", "cleared")})
        if args.apply:
            s.commit()
            print("applied")
        else:
            print("dry run — nothing written (add --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
