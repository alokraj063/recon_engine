"""
Loads the 12-21 Aug 2026 window (docs/runbook-12-21aug-ingestion.md) into
the LOCAL database, in-process — the runbook §4 "without a password"
technique: a TestClient over app.main.app with require_user overridden by
a fixed user (id 0, as tests/conftest.py does). Same routes, same
backend/data, nothing written to `users`, no server needed.

sample_data layout (the runbook's §2, Linux folder names):
  <root>/Bank_statement/_statement_attachments_only/HSBCINR...PDF
  <root>/Bill_status/Bill_Status_Split/{Friction,Hosur,Rohtak}-DDMM2026.xlsx
RNOTE/CRN are deliberately not posted (runbook §1).

Per day, oldest first: POST /api/ingest (statement + bills where present),
then POST /api/reconcile mode=incremental. Stops at the first error.

  cd backend && ../.venv/bin/python ../scripts/load_12_21aug.py --dry-run
  cd backend && ../.venv/bin/python ../scripts/load_12_21aug.py
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO.parent / "sample_data"

STATEMENTS = {
    "12": "HSBCINRHSBCINR2026-08-12-03.00.03.000974.PDF",
    "13": "HSBCINRHSBCINR2026-08-13-03.00.11.000870.PDF",
    "14": "HSBCINRHSBCINR2026-08-14-03.00.36.000765.PDF",
    "15": "HSBCINRHSBCINR2026-08-15-03.00.05.000602.PDF",
    "16": "HSBCINRHSBCINR2026-08-16-03.00.12.000184.PDF",
    "17": "HSBCINRHSBCINR2026-08-17-03.00.13.000303.PDF",
    "18": "HSBCINRHSBCINR2026-08-18-03.00.10.000520.PDF",
    "19": "HSBCINRHSBCINR2026-08-19-03.00.08.000694.PDF",
    "20": "HSBCINRHSBCINR2026-08-20-03.00.12.000545.PDF",
    "21": "HSBCINRHSBCINR2026-08-21-03.00.14.000716.PDF",
}
NO_BILLS = {"16"}  # no Bill Status export exists for 16 Aug


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--customer", default="default")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bk = args.root / "Bank_statement" / "_statement_attachments_only"
    bs = args.root / "Bill_status" / "Bill_Status_Split"
    # resolve + check every file up front so a typo fails before anything is posted
    plan = []
    for d, name in STATEMENTS.items():
        stmt = bk / name
        bills = [] if d in NO_BILLS else [
            bs / f"{unit}-{d}082026.xlsx" for unit in ("Friction", "Hosur", "Rohtak")]
        for f in [stmt, *bills]:
            if not f.exists():
                raise SystemExit(f"missing file: {f}")
        plan.append((d, stmt, bills))
    print(f"{len(plan)} days, all files present.")
    for d, stmt, bills in plan:
        print(f"  {d} Aug: 1 statement + {len(bills)} bills")
    if args.dry_run:
        return

    sys.path.insert(0, str(REPO / "backend"))
    from fastapi.testclient import TestClient
    from app.auth import AuthUser, require_user
    from app.main import app
    from db.audit import set_actor

    async def _fixed_user():
        set_actor(0)
        return AuthUser(id=0, email="loader@local", name="Runbook loader")
    app.dependency_overrides[require_user] = _fixed_user

    results = []
    with TestClient(app) as c:
        for d, stmt, bills in plan:
            print(f"\n== {d} Aug ==")
            files = [("statement", (stmt.name, stmt.read_bytes()))]
            files += [("bills", (b.name, b.read_bytes())) for b in bills]
            r = c.post("/api/ingest", data={"customer_id": args.customer},
                       files=files)
            if r.status_code >= 400:
                raise SystemExit(f"ingest {d} Aug: HTTP {r.status_code} {r.text}")
            ing = r.json()
            sid = next(f["bronze_file_id"] for f in ing["files"]
                       if f["field"] == "statement")
            print(f"  ingested: statement bronze id {sid}")
            r = c.post("/api/reconcile", json={
                "customer_id": args.customer, "statement_bronze_id": sid,
                "mode": "incremental"})
            if r.status_code >= 400:
                raise SystemExit(f"reconcile {d} Aug: HTTP {r.status_code} {r.text}")
            rec = r.json()
            print(f"  reconciled: run {rec.get('run_id')}  "
                  f"ledger: {json.dumps(rec['meta'].get('ledger'))}")
            results.append({"day": d, "ingest": ing, "reconcile": rec["meta"]})

    out = Path(__file__).with_name("load_12_21aug_results_local.json")
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nDone. Full per-day JSON: {out}")


if __name__ == "__main__":
    main()
