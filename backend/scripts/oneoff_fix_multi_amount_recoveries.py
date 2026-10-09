"""
ONE-OFF (2026-10-07): repair bills whose recovery lines lost amounts to the
multi-amount-head parser bug ('<head>: 5900, 5900' kept only the first
amount; fixed in recon/sources/ireps_bills.py).

The routine backfill cannot do this — it never touches the lines of a
LOCKED bill that already has some, and a locked bill's stored
recovery_sum/recovery_check were written from the same bad parse. This
script is the deliberate, narrow exception: for each bill whose lines do
not cover its deduction it re-parses the bill's NEWEST silver
RecoveryDetails with the fixed parser and replaces the lines + the bill's
recovery_count/recovery_sum/recovery_check/recoveries ONLY when the new
lines total the bill's deduction_amount (±1). Anything else is reported
and left alone. Matches, amounts paid and every other bill field are not
touched.

Stop the backend and back up data/*.db before --apply.

    python scripts/oneoff_fix_multi_amount_recoveries.py --customer KEY          # dry run
    python scripts/oneoff_fix_multi_amount_recoveries.py --customer KEY --apply  # write
"""

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402

from db import SessionLocal, init_db  # noqa: E402
from db.audit import record_event  # noqa: E402
from db.ingest import _record_sightings  # noqa: E402
from db.models import (Customer, GoldBill, GoldFileRow, GoldRecovery,  # noqa: E402
                       SilverRecord)
from logging_setup import get_logger  # noqa: E402
from recon.sources.ireps_bills import _parse_recovery_details, _to_amount  # noqa: E402

logger = get_logger(__name__)


def newest_details(session, customer_id: int) -> dict:
    """{(CO6No, BillNumber): (RecoveryDetails, bronze_file_id)} from the
    newest silver row that carries a Recovery Details cell."""
    out = {}
    rows = session.execute(
        select(SilverRecord).where(SilverRecord.customer_id == customer_id,
                                   SilverRecord.frame_name == "bills")
        .order_by(SilverRecord.bronze_file_id, SilverRecord.row_seq)).scalars()
    for r in rows:
        p = r.payload or {}
        if p.get("RecoveryDetails"):
            out[(str(p.get("CO6No")), str(p.get("BillNumber")))] = (
                p["RecoveryDetails"], r.bronze_file_id)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[1])
    ap.add_argument("--customer", required=True, help="customer key")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    init_db()
    with SessionLocal() as s:
        c = s.execute(select(Customer).where(Customer.key == args.customer)
                      ).scalar_one_or_none()
        if c is None:
            print(f"no customer {args.customer!r}")
            return 1
        details = newest_details(s, c.id)
        fixed, skipped, lines_in, lines_out = [], [], 0, 0
        for bill in s.execute(select(GoldBill).where(
                GoldBill.customer_id == c.id, GoldBill.deduction_amount > 0)).scalars():
            old = list(s.execute(select(GoldRecovery).where(
                GoldRecovery.gold_bill_id == bill.id)).scalars())
            covered = sum(l.recovery_amt or 0.0 for l in old)
            if abs(bill.deduction_amount - covered) < 1.0:
                continue
            ref = f"{bill.bill_number}/{bill.submission_ref}"
            hit = details.get((str(bill.submission_ref), str(bill.bill_number)))
            if hit is None:
                skipped.append((ref, "no silver Recovery Details"))
                continue
            text, file_id = hit
            items = _parse_recovery_details(text)
            total = sum(_to_amount(a) or 0.0 for _h, a in items)
            if abs(total - bill.deduction_amount) >= 1.0:
                skipped.append((ref, f"new lines total {total:.2f} != deduction "
                                     f"{bill.deduction_amount:.2f}"))
                continue
            print(f"{ref}: {len(old)} lines ({covered:.2f}) -> {len(items)} lines "
                  f"({total:.2f}), deduction {bill.deduction_amount:.2f}")
            s.execute(GoldFileRow.__table__.delete().where(
                GoldFileRow.frame == "recoveries",
                GoldFileRow.gold_row_id.in_([l.id for l in old])))
            for l in old:
                s.delete(l)
            s.flush()
            seq = (s.execute(select(func.max(GoldRecovery.row_seq)).where(
                GoldRecovery.bronze_file_id == file_id)).scalar() or -1) + 1
            sightings = {}
            for head, amt in items:
                row = GoldRecovery(
                    customer_id=c.id, bronze_file_id=file_id, row_seq=seq,
                    gold_bill_id=bill.id, bill_number=bill.bill_number,
                    submission_ref=bill.submission_ref,
                    operating_unit=bill.operating_unit, recovery_head=head,
                    recovery_amt=_to_amount(amt), recovery_text=amt)
                s.add(row)
                s.flush()
                sightings[seq] = row.id
                seq += 1
            _record_sightings(s, c.id, "recoveries", file_id, sightings)
            bill.recovery_count = len(items)
            bill.recovery_sum = total
            bill.recovery_check = True
            ex = dict(bill.extras or {})
            ex["recoveries"] = {h: _to_amount(a) for h, a in items}
            bill.extras = ex
            fixed.append(ref)
            lines_in += len(items)
            lines_out += len(old)
        print(f"\ncustomer {c.key}: bills_fixed {len(fixed)}, lines_inserted "
              f"{lines_in}, lines_deleted {lines_out}, skipped {len(skipped)}")
        for ref, why in skipped:
            print(f"  SKIPPED {ref}: {why}")
        if args.apply:
            if fixed:
                record_event(s, logger, event_type="gold.recoveries_backfilled",
                             customer_id=c.id,
                             details={"oneoff": "multi_amount_heads",
                                      "bills_refreshed": len(fixed),
                                      "lines_inserted": lines_in,
                                      "lines_deleted": lines_out})
            s.commit()
            print("applied")
        else:
            s.rollback()
            print("dry run — nothing written (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
