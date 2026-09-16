# Runbook — loading the 18–21 Aug 2026 window

Exactly what was ingested into the `default` customer, in what order, and what
came out. First loaded 2026-09-15/16; **wiped and re-ingested 2026-09-16 ~17:00**
after the duplicate-bill fix (§5 shows the re-ingest). Hand this to another agent
to reproduce or extend it.

---

## 1. The rule that decides which files go together

**Every document dated D covers business day D−1.** A statement delivered at
03:00 on D carries the credits that settled on D−1, and the IREPS Bill Status
export dated D carries payment advices up to D−1. So the files of one **file
date** belong together, and the business day they describe is the day before.

Verified on the bank side:

| Statement file date | Credits | Value date of those credits |
|---|---|---|
| 2026-08-18 | 1 | 17 Aug |
| 2026-08-19 | 80 | 18 Aug |
| 2026-08-20 | 52 | 19 Aug |
| 2026-08-21 | 65 | 20 Aug |

## 2. Source data

Root: `C:\Users\Lehen Zehra\Desktop\Wabtec\sample_data`

| Slot | Folder | Notes |
|---|---|---|
| statement | `Bank_stmt_PDF\_statement_attachments_only\` | 58 files, dated in the name |
| bills | `Bill_Status_Split\Bill_Status_Split\` | 3 files per date (Friction / Hosur / Rohtak) |
| rnote, crn | `RNOTE & CRN files\` | one each per date, `.xls` or `.xlsx` |

`Bank_stmt_PDF\*.pdf` (75 "HSBCnet Automated File Delivery" files) were **not**
used: their names carry no date and they were never scanned.

Coverage: Bill Status exists 22 Jul – 21 Aug; statements 1 Jul – 6 Sep;
RNOTE/CRN 2 Jul – 21 Aug. Missing statement dates: 6, 12, 13, 19, 20, 27 Jul and
1, 3, 10, 24, 31 Aug — mostly Monday files (covering Sunday). No CRN for 30 Jul;
no RNOTE/CRN for 15 Aug.

## 3. The window that was loaded

File dates **19, 20, 21, 22 Aug 2026** → business days **18, 19, 20, 21 Aug**.
Day 4 is a statement only, because Bill Status stops at 21 Aug.

| Day | Statement (`statement`) | Bill Status (`bills`, all three) | `rnote` | `crn` |
|---|---|---|---|---|
| 1 | `HSBCINRHSBCINR2026-08-19-03.00.08.000694.PDF` | `Friction-19082026.xlsx`, `Hosur-19082026.xlsx`, `Rohtak-19082026.xlsx` | `RNOTE IREPS 19082026.xls` | `CRN IREPS 19082026.xlsx` |
| 2 | `HSBCINRHSBCINR2026-08-20-03.00.12.000545.PDF` | `…-20082026.xlsx` ×3 | `RNOTE IREPS 20082026.xls` | `CRN IREPS 200862026.xls` |
| 3 | `HSBCINRHSBCINR2026-08-21-03.00.14.000716.PDF` | `…-21082026.xlsx` ×3 | `RNOTE IREPS 21082026.xls` | `CRN IREPS 21082026.xlsx` |
| 4 | `HSBCINRHSBCINR2026-08-22-03.00.06.000113.PDF` | — | — | — |

Two traps in the source data:

- **`CRN IREPS 200862026.xls`** — a typo in the supplied filename. It is the
  20 Aug CRN.
- **Swapped unit labels on 19 and 21 Aug**: `Hosur-19082026.xlsx` actually holds
  Rohtak's bills and `Rohtak-19082026.xlsx` holds Hosur's (same on 21 Aug).
  `_Split_Audit.xlsx` flags this. Harmless as long as all three files are
  uploaded — the operating unit is derived from each bill's PartyCode, not the
  filename.

## 4. Procedure

Backend must be running (`cd backend && ../.venv/Scripts/uvicorn app.main:app
--reload --port 8000`). For each day, oldest first:

1. **One `POST /api/ingest`** (multipart) with that day's files. The `bills`
   field is repeated three times — one submission, three files.
2. **One `POST /api/reconcile`** with the statement's `bronze_file_id` from the
   ingest response.

```bash
curl -s -X POST http://localhost:8000/api/ingest \
  -F "customer_id=default" \
  -F "statement=@$BK/HSBCINRHSBCINR2026-08-19-03.00.08.000694.PDF" \
  -F "bills=@$BS/Friction-19082026.xlsx" \
  -F "bills=@$BS/Hosur-19082026.xlsx" \
  -F "bills=@$BS/Rohtak-19082026.xlsx" \
  -F "rnote=@$L/RNOTE IREPS 19082026.xls" \
  -F "crn=@$L/CRN IREPS 19082026.xlsx"

curl -s -X POST http://localhost:8000/api/reconcile \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"default","statement_bronze_ids":[<id>],"mode":"incremental"}'
```

**Order matters.** Bill Status must be ingested in date order: gold bills are
entity-upserted, so a later export updates a bill's status and an older one
ingested afterwards would roll it back.

**Mode matters.** Only `incremental` feeds the match ledger, which is what
Command Center, the Analyst queue and AR read. `snapshot` leaves them empty.

## 5. What came out

Re-ingest of 2026-09-16, with the duplicate-bill fix (a bill numbered `-` now
matches on its CO6, see §6.4):

| Day | Ingest (new rows) | Run result |
|---|---|---|
| 1 | bank 139, bills 2350, recoveries 3913, rnote 2068, crn 2326 | 80 credits, 17 matched, 63 bank-only, 1844 bill-only |
| 2 | bank +87, bills +20 (2287 updated, 17 conflicts), recoveries +3, rnote +10, crn +7 | pool 115, 14 matched |
| 3 | bank +141, bills +37 (2188 updated, 31 conflicts), recoveries +20, rnote +10, crn +14 | pool 166, 16 matched (3 for review) |
| 4 | bank +230 | pool 187, 1 matched (for review) |

End state: **48 matches** (44 auto-locked HIGH, 4 open for review), **2,407
gold bills**, 3,936 recovery lines, 186 open BANK_ONLY, **1,839 open BILL_ONLY**,
10 exceptions closed by later runs. No duplicate `-` bills.

Command Center, 18–22 Aug (every credit loaded falls in it): credits 234 = 170
other receipts + 64 IREPS; IREPS = 6 awaiting status + 6 awaiting bill data + 52
recognised; recognised = 44 settled + 4 in review + 4 unmatched, **match rate
84.6%**. Open exceptions **18** (16 bank only, 2 bill only), ₹8,36,89,409; Gold
pool 78 bills.

The first load (old code) differed only on the bill side: bills +39/+56 on days
2/3, recoveries +55/+77, 2,445 gold bills, 1,867 open BILL_ONLY, 9 resolved.

## 6. Things that will otherwise waste an hour

1. **Dashboard shows nothing** — check three things: the customer (the UI opens
   the one in localStorage), the date filter (defaults to "This month"; August
   data needs **All time**), and the run mode (snapshot writes no ledger).
2. **Bill-only stays near 1,800 all-time.** The Command Center hides this unless
   the date filter is "All time" — with a window it counts only bills due in
   it. Each Bill Status export carries ~3 months of already-paid bills while
   only 4 days of statements are loaded. It falls only as older statements are
   added (1 Jul onwards exist).
3. **Ingest "conflicts" (48 across days 2–3) are benign**: locked bills whose
   `data_row` (row position in the file) or CO7 number changed in the next
   export. No amounts or statuses moved.
4. **Bills with bill number `-` used to duplicate on every export** — FIXED
   2026-09-16. The ingest key was (bill_number, CO6) and a `-` never matched, so
   each daily export stored another copy with its own open BILL_ONLY exception
   (19 bills became 57 rows; 28 phantom open exceptions; two ₹90,42,153 SCoR
   rows topped "Largest open exceptions"). `db/ingest._bill_key` now matches
   such a bill on its CO6 alone. A database loaded with the OLD code can be
   repaired without re-ingesting: `cd backend && ../.venv/Scripts/python
   scripts/merge_duplicate_bills.py --customer default` (dry run), then add
   `--apply` with the backend stopped and `data/*.db` backed up.
5. **To start clean**, delete the customer's data rather than the customer:
   child rows first (`run_match_bills`, `run_frames`, `match_ledger_bills`),
   then every table with a `customer_id`, keeping `customers`, `source_configs`
   and `match_rule_sets`; then remove `backend/data/bronze/<key>/` blobs and
   `backend/data/runs/<run_id>/`. `backend/tests/test_ledger_decisions.py` has
   the canonical teardown order. The 2026-09-16 re-ingest did exactly this
   (audit_log included), then followed §4 — with a backend started AFTER the
   last backend code change; an older process ingests with the old rules.
6. **`backend/data` was wiped externally mid-session** (server restart at 15:21
   recreated all four .db files). If figures vanish, check that first.
7. **Don't scan all 133 PDFs in one process** — a single pdfplumber pass was
   killed for memory. Chunk it (~34 files per process) and append results.
8. **node is not on PATH**:
   `C:\Users\Lehen Zehra\AppData\Local\Programs\nodejs\node.exe`. In PowerShell,
   prepend its folder to `$env:Path`, then run `npx --no-install tsc -b` from
   `frontend/` — without `--no-install`, a wrong cwd makes npx fetch the
   unrelated `tsc` npm package.
9. **Tests need a scratch data dir**: `RECON_DATA_DIR=$(mktemp -d)`, or they run
   against `backend/data` and can leave rows behind.
10. **Known-failing tests are unrelated**: 8 errors from the missing sample
    documents and `backend/tests/golden/*.csv` on this machine.

## 7. Related open items

- **Bank self-check produced no result** for all four statements on the
  re-ingest (`selfcheck` null, `selfchecks` [] from both ingest and reconcile):
  the parsed credits were never tied to the statement's printed totals. Not
  investigated.
- **₹5,73,90,881 ECoR credit** (`SBINN52026081881329492`, "ECoR Schedule Main",
  18 Aug) has no bill: only one ECoR bill (₹9.66 lakh) was advised within 5 days.
  Likely a multi-bill schedule payment the batch pass never sees (it only
  considers credits that had a single-amount candidate). Needs the remittance
  advice.
- **89 CO7 refs stored as `…700035.0`** — a float artifact from exports that
  carry CO7 as an Excel number; can break CO7 joins across files.
- **"Other receipts" ₹56 cr is mostly treasury movement**: 3 sweeps of ₹5.74 cr,
  5 deposit withdrawals of ₹3 cr, and 125 "DEPOSIT INTEREST … CLUSTER DEPOSIT
  ROLLED OVER" lines of exactly ₹25,027.40 each (worth one comparison against
  the PDF).
- Today's 16 open IREPS credits = 4 unmatched + 6 awaiting source status + 6
  awaiting bill data (18–22 Aug window).
- (First load) 16 unmatched railway credits were traced: 7 have a bill that IREPS has not
  marked paid (6 PASSED/REGISTERED — now excluded from the match rate — and 1
  RETURNED, deliberately still counted); 9 have no bill of that amount in any
  export from 22 Jul to 21 Aug.
- 4 matches await review: three ₹11,48,156 NER credits against three identical
  bills (any pairing settles the same three), and one ₹97,991 NWR credit whose
  bill has a CO7 but no advice date.
