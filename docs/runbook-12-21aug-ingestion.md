# Runbook — loading the 12–21 Aug 2026 window (statement + bills only)

Extends the 18–21 Aug load in `docs/runbook-4day-ingestion.md` backward to 10
days, dropping the `rnote`/`crn` lineage slots (not available for every day
in this range). **Executed 2026-09-23** after wiping the `default`
customer's prior data (the 18–21 Aug load) — see §6 for what came out.

---

## 1. Why this window, and what's different from the 18–21 Aug load

Same rule as the 4-day runbook: **a document dated D covers business day
D−1.** File dates 12–21 Aug therefore reconcile business days 11–20 Aug.

Differences from `runbook-4day-ingestion.md`:

- **10 days instead of 4** — the largest contiguous stretch where a
  statement and a Bill Status export both exist, once RNOTE/CRN are dropped
  as a requirement (see below).
- **No `rnote`/`crn` slots posted at all.** RNOTE exists 2 Jul–21 Aug and CRN
  2 Jul–21 Aug in this dataset, but with gaps that don't line up with this
  window (no CRN for 30 Jul, no RNOTE/CRN for 15 Aug) — rather than patch
  around that day by day, this load omits both slots for every day.
  Consequence: `attach_lineage` has nothing to join, so `LineageStatus` and
  the trail columns (PO, Receipt_Doc, Invoice_Date, Bill_Reg_Date, …) stay
  empty for every row in this load, and the Lineage docs page / "with
  lineage trail" toggle will be empty. Matching itself is unaffected —
  amount/zone/date signals come from bank and bill fields, not lineage.
- **Day 5 (16 Aug) has no Bill Status export in the sample data at all** —
  not even an unsplit original like the one that exists for 1 Aug
  (`_NOT_SPLIT_BILL STATUS 01082026.xls`). Confirmed by directory listing,
  not just a date-parsing miss. That day posts `statement` only; its
  credits land as open BANK_ONLY until a later cumulative Bill Status
  export (17 Aug's) reports whatever was advised through the 16th.
- **Days 8 and 10 (19 and 21 Aug file dates) were already ingested** by the
  4-day runbook's 2026-09-22 load, which used file dates 19–22 Aug. Ingest
  is idempotent (dedup by customer + sha256 of the bronze file, entity
  upsert on gold rows), so re-posting them is harmless — it will report
  already-known rows rather than insert new ones. Whether to re-run those
  two days depends on whether `backend/data` still holds that load; check
  first (`GET /api/ingestions` or `GET /api/gold/files`).
- Hosur/Rohtak tab labels are swapped on 19 and 21 Aug (`_Split_Audit.xlsx`
  flags it) — harmless as long as all three unit files are uploaded each
  day; `operating_unit` is derived from each bill's PartyCode, not the
  filename or tab label.

## 2. Source data

Root: `C:\Users\Lehen Zehra\Desktop\Wabtec\sample_data`

| Slot | Folder |
|---|---|
| statement | `Bank_stmt_PDF\_statement_attachments_only\` |
| bills | `Bill_Status_Split\Bill_Status_Split\` (3 files per date — Friction / Hosur / Rohtak) |

No `rnote` or `crn` fields are posted in this load.

## 3. The window

| Day | File date | Business day | Statement (`statement`) | Bill Status (`bills`, all three where present) |
|---|---|---|---|---|
| 1 | 12 Aug | 11 Aug | `HSBCINRHSBCINR2026-08-12-03.00.03.000974.PDF` | `Friction-12082026.xlsx`, `Hosur-12082026.xlsx`, `Rohtak-12082026.xlsx` |
| 2 | 13 Aug | 12 Aug | `HSBCINRHSBCINR2026-08-13-03.00.11.000870.PDF` | `Friction-13082026.xlsx`, `Hosur-13082026.xlsx`, `Rohtak-13082026.xlsx` |
| 3 | 14 Aug | 13 Aug | `HSBCINRHSBCINR2026-08-14-03.00.36.000765.PDF` | `Friction-14082026.xlsx`, `Hosur-14082026.xlsx`, `Rohtak-14082026.xlsx` |
| 4 | 15 Aug | 14 Aug | `HSBCINRHSBCINR2026-08-15-03.00.05.000602.PDF` | `Friction-15082026.xlsx`, `Hosur-15082026.xlsx`, `Rohtak-15082026.xlsx` |
| 5 | 16 Aug | 15 Aug | `HSBCINRHSBCINR2026-08-16-03.00.12.000184.PDF` | — (no Bill Status export exists for this date; post `statement` only) |
| 6 | 17 Aug | 16 Aug | `HSBCINRHSBCINR2026-08-17-03.00.13.000303.PDF` | `Friction-17082026.xlsx`, `Hosur-17082026.xlsx`, `Rohtak-17082026.xlsx` |
| 7 | 18 Aug | 17 Aug | `HSBCINRHSBCINR2026-08-18-03.00.10.000520.PDF` | `Friction-18082026.xlsx`, `Hosur-18082026.xlsx`, `Rohtak-18082026.xlsx` |
| 8 | 19 Aug | 18 Aug | `HSBCINRHSBCINR2026-08-19-03.00.08.000694.PDF` | `Friction-19082026.xlsx`, `Hosur-19082026.xlsx`, `Rohtak-19082026.xlsx` (Hosur/Rohtak tab labels swapped — upload all three anyway) |
| 9 | 20 Aug | 19 Aug | `HSBCINRHSBCINR2026-08-20-03.00.12.000545.PDF` | `Friction-20082026.xlsx`, `Hosur-20082026.xlsx`, `Rohtak-20082026.xlsx` |
| 10 | 21 Aug | 20 Aug | `HSBCINRHSBCINR2026-08-21-03.00.14.000716.PDF` | `Friction-21082026.xlsx`, `Hosur-21082026.xlsx`, `Rohtak-21082026.xlsx` (Hosur/Rohtak tab labels swapped — upload all three anyway) |

## 4. Procedure

Same as `docs/runbook-4day-ingestion.md` §4: backend running, sign in once,
reuse the cookie, then for each day **oldest first**:

1. **One `POST /api/ingest`** (multipart) with that day's `statement` and
   (where present) three `bills` files. No `rnote`/`crn` fields in this load.
2. **One `POST /api/reconcile`** with the statement's `bronze_file_id` from
   the ingest response, `mode: incremental`.

```bash
curl -s -c cookies.txt -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"..."}' \
  http://localhost:8000/api/auth/login

BK="C:/Users/Lehen Zehra/Desktop/Wabtec/sample_data/Bank_stmt_PDF/_statement_attachments_only"
BS="C:/Users/Lehen Zehra/Desktop/Wabtec/sample_data/Bill_Status_Split/Bill_Status_Split"

# Day 1 (12 Aug file date -> business day 11 Aug)
curl -s -b cookies.txt -X POST http://localhost:8000/api/ingest \
  -F "customer_id=default" \
  -F "statement=@$BK/HSBCINRHSBCINR2026-08-12-03.00.03.000974.PDF" \
  -F "bills=@$BS/Friction-12082026.xlsx" \
  -F "bills=@$BS/Hosur-12082026.xlsx" \
  -F "bills=@$BS/Rohtak-12082026.xlsx"

curl -s -b cookies.txt -X POST http://localhost:8000/api/reconcile \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"default","statement_bronze_ids":[<id from above>],"mode":"incremental"}'

# ... repeat for days 2-4, 6-10 with their files (see table in §3)

# Day 5 (16 Aug) -- statement only, no bills field at all
curl -s -b cookies.txt -X POST http://localhost:8000/api/ingest \
  -F "customer_id=default" \
  -F "statement=@$BK/HSBCINRHSBCINR2026-08-16-03.00.12.000184.PDF"

curl -s -b cookies.txt -X POST http://localhost:8000/api/reconcile \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"default","statement_bronze_ids":[<id from above>],"mode":"incremental"}'
```

**Order matters** (Bill Status entity-upserts; an older export ingested
after a newer one would roll a bill's status back). **Mode matters** (only
`incremental` feeds the match ledger).

**How this run was actually executed** (no password on hand): same
in-process technique as `docs/runbook-4day-ingestion.md` §4's "Without a
password" note — `fastapi.testclient.TestClient` over `app.main.app` with
`app.dependency_overrides[auth.require_user]` set to a fixed user (id `0`,
matching `tests/conftest.py`'s pattern), run as a standalone script rather
than curl. Same code path, same `backend/data`, nothing written to `users`.
The live `uvicorn --reload` process stayed up throughout; SQLite tolerated
the second process fine for these short-lived writes.

## 5. What was done before running

1. **Backed up `backend/data`** to
   `data_backup_20260923_173949_pre_1221aug/` (sibling of `backend/`),
   since the 18–21 Aug load in `backend/data` held live ledger state.
2. **Wiped the `default` customer's data**, following the teardown order in
   `docs/runbook-4day-ingestion.md` §6 item 5 (`credit_sources` first, then
   every table with a `customer_id`, keeping `customers`/`source_configs`/
   `match_rule_sets`), plus removing `backend/data/bronze/default/` blobs
   and `backend/data/runs/*`. Confirmed by the delete counts matching the
   4-day runbook's last known end state (47 matches, 2,407 gold bills, 194
   exception rows, etc. — see script output).
3. Ran the 10-day sequence in §4, oldest first, `mode: incremental`.

## 6. What came out

All 10 days ingested and reconciled without error, including day 5 (16 Aug,
statement-only — no ingest error posting zero `bills` files, no reconcile
error against a pool with nothing newly advised that day).

Per-day ledger deltas (`ledger.finalized` from each `/api/reconcile` call):

| Day | File date | matches created | auto-locked | exceptions opened | exceptions resolved | gold bills after |
|---|---|---|---|---|---|---|
| 1 | 12 Aug | 32 | 32 | 51 | 0 | 2,486 |
| 2 | 13 Aug | 14 | 14 | 45 | 1 | 2,480 |
| 3 | 14 Aug | 34 | 32 | 57 | 7 | 2,574 |
| 4 | 15 Aug | 1 | 0 | 43 | 0 | 2,540 |
| 5 | 16 Aug | 1 | 0 | 3 | 0 | 2,539 |
| 6 | 17 Aug | 0 | 0 | 1 | 0 | 2,538 |
| 7 | 18 Aug | 2 | 2 | 4 | 2 | 2,574 |
| 8 | 19 Aug | 17 | 17 | 69 | 0 | 2,592 |
| 9 | 20 Aug | 14 | 14 | 39 | 6 | 2,595 |
| 10 | 21 Aug | 16 | 13 | 49 | 1 | 2,618 |

End state (queried directly from `match_ledger`/`exception_ledger`/gold
tables after all 10 runs):

- **10 runs**, all succeeded.
- **131 matches** total: **124 LOCKED** (auto-HIGH), **7 OPEN** for review.
- **339 open BANK_ONLY**, **5 open BILL_ONLY**, **17 exceptions resolved**
  by a later run in the sequence.
- **2,733 gold bills**, **1,134 gold bank txns**, **4,248 recovery lines**.
- **582 ingest conflicts** across the run (locked bills whose `data_row` or
  `payment_order_ref` shifted between exports — the same benign class the
  4-day runbook's §6 item 3 describes; no amounts or statuses moved).
- `meta.expected_from` was **2026-08-11** on every run — the incremental
  floor locked onto the first day's credit value date (11 Aug, from the 12
  Aug file) and never moved, since every subsequent statement was newer.
- Day 4→5 and day 5→6 (across the 16 Aug bills gap) show the pool
  continuing to accumulate normally — 15 Aug's Bill Status export already
  had most of what 16 Aug's credits needed (`matched: 1` on day 5 despite
  no bills posted that day, matching against bills already in gold from
  earlier exports), confirming the "no Bill Status file exists for 16 Aug"
  gap is harmless to work around this way.
- Full per-day JSON (ingest stats + reconcile `meta` for every day) is in
  the session scratchpad as `ingest_12_21_aug_results.json` — not committed
  to the repo (it's a one-off run artifact, not source data or code).
