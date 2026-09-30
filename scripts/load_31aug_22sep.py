"""
Loads the 31 Aug - 22 Sep 2026 window (business days, by credit value date)
from the raw "TWO MONTHS DATA" folder into a NEW customer in the LOCAL
database, incrementally, the same way scripts/load_12_21aug.py loaded the
12-21 Aug window: in-process TestClient over app.main.app with require_user
overridden by a fixed user (id 0), same routes, same backend/data.

What is different from the 12-21 Aug load:
  * Bank statements are read straight out of the Outlook .msg emails in
    <root>/Bank stmt/ (and its subfolders). Many emails are forwards of the
    same delivery, and HSBC sometimes re-sends an earlier day's statement
    under a new delivery date, so a statement is chosen by the credit VALUE
    DATES its parse yields, never by the email or attachment name:
      - PDFs are deduped by content (sha256),
      - a PDF whose credits are identical to an already-chosen one is a
        re-send and is skipped,
      - only PDFs whose every credit value date falls in [--start, --end]
        are loaded (a statement straddling the boundary stops the plan).
    The rolling 30-day "FT Bank statement prior day" .xlsx attachments are
    ignored: the app has no adapter for them, and for this window they hold
    the same credits as the PDFs.
  * Bill Status comes unsplit, one export per date, from <root>/BILL STATUS/
    (the parser derives operating_unit from PartyCode, so no split needed).
    A document dated D covers business day D-1, so the window's bill exports
    are the ones dated --start+1 .. --end+1.
  * Every file date is one step, oldest first: POST /api/ingest with that
    date's statement and/or bill export, then POST /api/reconcile
    mode=incremental when a statement was posted. A date with a bill export
    but no statement is ingested only (it still refreshes bill statuses).
  * RNOTE/CRN are not posted (as in the 12-21 Aug load): lineage does not
    affect matching.
  * The customer is created (POST /api/customers) and its matching config is
    copied from --config-from (default: `default`) so both read the same.
    An existing customer with the same key is refused unless --reuse.

Needs extract-msg (not in requirements.txt):
  ../.venv/bin/pip install extract-msg

  cd backend && ../.venv/bin/python ../scripts/load_31aug_22sep.py --dry-run
  cd backend && ../.venv/bin/python ../scripts/load_31aug_22sep.py
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
DEFAULT_ROOT = REPO.parent / "TWO MONTHS DATA"


def _date(s):
    return dt.date.fromisoformat(s)


def _is_bank_holiday_saturday(d):
    # RBI: 2nd and 4th Saturdays are bank holidays
    return d.weekday() == 5 and (d.day - 1) // 7 in (1, 3)


def expected_business_days(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() != 6 and not _is_bank_holiday_saturday(d):
            out.append(d)
        d += dt.timedelta(1)
    return out


def collect_statements(bank_dir, start, end, warn):
    """-> [(delivery_date, attachment_name, pdf_bytes, value_dates, n, total)]"""
    try:
        import extract_msg
    except ImportError:
        raise SystemExit("extract-msg is not installed: "
                         "../.venv/bin/pip install extract-msg")
    from recon.parsers.bank_hsbc import parse_hsbc_statement

    pdfs = {}   # sha256 -> (name, bytes)
    for msg_path in sorted(bank_dir.rglob("*.msg")):
        if msg_path.name.startswith("~$"):
            continue
        msg = extract_msg.Message(str(msg_path))
        try:
            for att in msg.attachments:
                name = att.longFilename or att.shortFilename or ""
                if name.upper().endswith(".PDF"):
                    data = att.data
                    pdfs.setdefault(hashlib.sha256(data).hexdigest(),
                                    (name, data))
        finally:
            msg.close()

    chosen, seen_credits = [], {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in sorted(pdfs.values()):
            m = re.search(r"(\d{4}-\d\d-\d\d)", name)
            if not m:
                warn(f"bank attachment with no delivery date in its name: {name}")
                continue
            delivered = _date(m.group(1))
            # cheap pre-filter: a statement is delivered the day after its
            # value date (or a few days later over a weekend)
            if not (start <= delivered <= end + dt.timedelta(days=4)):
                continue
            p = Path(tmp) / name
            p.write_bytes(data)
            df = parse_hsbc_statement(str(p))
            if df.empty:
                continue
            vds = sorted(set(df["value_date"].dt.date))
            inside = [d for d in vds if start <= d <= end]
            if not inside:
                continue
            if len(inside) != len(vds):
                raise SystemExit(
                    f"{name} straddles the window: value dates "
                    f"{[str(d) for d in vds]}; move --start/--end")
            key = frozenset(zip(df["bank_ref"].astype(str),
                                df["amount"].round(2), df["value_date"]))
            if key in seen_credits:
                warn(f"{name} is a re-send of {seen_credits[key]} "
                     "(identical credits) - skipped")
                continue
            seen_credits[key] = name
            chosen.append((delivered, name, data, vds, len(df),
                           float(df["amount"].sum())))
    chosen.sort()
    return chosen


def collect_bills(bills_dir, first, last):
    """Bill Status exports dated first..last -> {date: Path}"""
    out = {}
    for p in sorted(bills_dir.iterdir()):
        if p.name.startswith("~$") or p.suffix.lower() not in (".xls", ".xlsx"):
            continue
        m = re.search(r"(\d{8})", p.name)
        if not m:
            continue
        d = dt.datetime.strptime(m.group(1), "%d%m%Y").date()
        if first <= d <= last:
            if d in out:
                raise SystemExit(f"two Bill Status exports for {d}: "
                                 f"{out[d].name}, {p.name}")
            out[d] = p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--start", type=_date, default=_date("2026-08-31"),
                    help="first business (value) date, inclusive")
    ap.add_argument("--end", type=_date, default=_date("2026-09-22"),
                    help="last business (value) date, inclusive")
    ap.add_argument("--customer", default="wabtec_daily")
    ap.add_argument("--name", default="Wabtec (daily feed)")
    ap.add_argument("--config-from", default="default",
                    help="customer whose matching config is copied; '' = none")
    ap.add_argument("--max-pairing-gap-days", type=int, default=None,
                    help="set the new customer's pairing window (days a bill's "
                         "advice date may precede the credit); default: leave "
                         "the copied config's value")
    ap.add_argument("--reuse", action="store_true",
                    help="load into an existing customer with this key")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    warnings = []

    def warn(msg):
        warnings.append(msg)
        print(f"  WARNING: {msg}")

    print(f"Window: value dates {args.start} .. {args.end}")
    stmts = collect_statements(args.root / "Bank stmt", args.start, args.end, warn)
    bills = collect_bills(args.root / "BILL STATUS",
                          args.start + dt.timedelta(1), args.end + dt.timedelta(1))

    covered = {d for s in stmts for d in s[3]}
    for d in expected_business_days(args.start, args.end):
        if d not in covered:
            warn(f"no bank statement credits for business day {d:%a %d-%b}")

    by_date = {}
    for s in stmts:
        if s[0] in by_date and "statement" in by_date[s[0]]:
            raise SystemExit(f"two statements delivered {s[0]}: "
                             f"{by_date[s[0]]['statement'][1]}, {s[1]}")
        by_date.setdefault(s[0], {})["statement"] = s
    for d, p in bills.items():
        by_date.setdefault(d, {})["bills"] = p
    plan = sorted(by_date.items())

    print(f"\n{len(plan)} steps, {len(stmts)} statements, {len(bills)} bill exports:")
    for d, step in plan:
        s, b = step.get("statement"), step.get("bills")
        st = (f"stmt {s[1][14:24]} -> value {','.join(f'{v:%d-%b}' for v in s[3])}"
              f" ({s[4]} credits, Rs {s[5]:,.0f})") if s else "no statement"
        print(f"  {d:%a %d-%b}: {st:<62} | {b.name if b else 'no bill export'}")
    if args.dry_run:
        print(f"\nDry run. {len(warnings)} warning(s).")
        return

    from fastapi.testclient import TestClient
    from app.auth import AuthUser, require_user
    from app.main import app
    from db.audit import set_actor

    async def _fixed_user():
        set_actor(0)
        return AuthUser(id=0, email="loader@local", name="Window loader")
    app.dependency_overrides[require_user] = _fixed_user

    def ok(r, what):
        if r.status_code >= 400:
            raise SystemExit(f"{what}: HTTP {r.status_code} {r.text}")
        return r.json()

    results = []
    with TestClient(app) as c:
        keys = {x["key"] for x in ok(c.get("/api/customers"), "list customers")}
        if args.customer in keys and not args.reuse:
            raise SystemExit(f"customer '{args.customer}' already exists "
                             "(pass --reuse to load into it)")
        if args.customer not in keys:
            ok(c.post("/api/customers",
                      json={"key": args.customer, "name": args.name}),
               "create customer")
            print(f"\ncreated customer '{args.customer}'")
            if args.config_from:
                src = ok(c.get(f"/api/customers/{args.config_from}/config"),
                         "read source config")
                ok(c.put(f"/api/customers/{args.customer}/config",
                         json=src["rules"]), "copy config")
                print(f"copied matching config from '{args.config_from}'")
            if args.max_pairing_gap_days is not None:
                cur = ok(c.get(f"/api/customers/{args.customer}/config"),
                         "read config")["rules"]
                cur["max_pairing_gap_days"] = args.max_pairing_gap_days
                ok(c.put(f"/api/customers/{args.customer}/config", json=cur),
                   "set pairing window")
                print(f"pairing window: {args.max_pairing_gap_days} days")

        for d, step in plan:
            s, b = step.get("statement"), step.get("bills")
            print(f"\n== {d:%a %d-%b} ==")
            files = []
            if s:
                files.append(("statement", (s[1], s[2])))
            if b:
                files.append(("bills", (b.name, b.read_bytes())))
            ing = ok(c.post("/api/ingest", data={"customer_id": args.customer},
                            files=files), f"ingest {d}")
            print(f"  ingested: {json.dumps(ing.get('stats', {}).get('by_frame'))}")
            row = {"date": str(d), "statement": s[1] if s else None,
                   "bills": b.name if b else None, "ingest": ing}
            if s:
                sid = next(f["bronze_file_id"] for f in ing["files"]
                           if f["field"] == "statement")
                rec = ok(c.post("/api/reconcile", json={
                    "customer_id": args.customer, "statement_bronze_id": sid,
                    "mode": "incremental"}), f"reconcile {d}")
                meta = rec["meta"]
                if meta.get("signal_coverage"):
                    warn(f"{d}: signal coverage {meta['signal_coverage']}")
                sc = meta.get("selfcheck")
                if isinstance(sc, dict) and sc.get("passed") is False:
                    warn(f"{d}: bank selfcheck did not pass")
                print(f"  reconciled: run {rec.get('run_id')}  "
                      f"ledger: {json.dumps(meta.get('ledger'))}")
                row["reconcile"] = meta
            results.append(row)

    out = Path(__file__).with_name(f"load_31aug_22sep_results_{args.customer}.json")
    out.write_text(json.dumps({"customer": args.customer, "warnings": warnings,
                               "steps": results}, indent=2, default=str))
    print(f"\nDone. {len(warnings)} warning(s). Full per-step JSON: {out}")


if __name__ == "__main__":
    main()
