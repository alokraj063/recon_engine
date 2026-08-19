# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Receivables reconciliation engine: matches credits parsed from an HSBC daily-statement PDF against the IREPS "Bill Status" Excel export, attaching upstream document lineage (RNOTE / CRN reports) so each payment traces back to its PO. Output is an Excel workbook with matched rows, a two-sided exception queue, and a summary. A FastAPI backend (`backend/app/`) and React SPA (`frontend/`) wrap the same engine for browser use.

## Commands

```bash
.venv/bin/pip install -r backend/requirements.txt   # venv lives at repo root (Python 3.9 — no `X | None` syntax at runtime)
cd frontend && npm install                          # Node >= 18

# CLI, run from backend/ (sample inputs live in "../Receipt_reconciliation_and_IDR _ Requested_sample_documents/")
cd backend && ../.venv/bin/python -m recon --statement <stmt.PDF> --bills <bills.xlsx> \
                --rnote <rnote.xlsx> --crn <crn.xlsx> -o out.xlsx

# Web UI: two terminals
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev          # http://localhost:5173, proxies /api to :8000

cd frontend && npx tsc -b           # typecheck
cd frontend && npm run build        # production build
```

There are no tests, no linter config, and this is not a git repository. The sample documents folder contains real input files for manual verification (note: filenames contain spaces; the `~$...` files are Excel lock files, ignore them — a live `~$Recon_*.xlsx` lock means that output workbook is open in Excel and writes to it will fail).

## Architecture

Layered, with one-way dependencies — parsers know nothing about matching, matching knows nothing about the engine, the engine knows nothing about Excel, and `backend/app/` only moves files in and frames out. Nothing imports upward. Keep it that way when adding code.

```
backend/recon/   the engine package (imports are all relative; runs with backend/ as cwd)
  config.py    ReconConfig — every path and threshold; nothing else hardcodes a tunable
  parsers/     source document -> plain DataFrame
    bank_hsbc.py     PDF table extraction (pdfplumber, cell-based) + zone-from-narrative regex + bank_selfcheck
    bill_status.py   IREPS block-format sheets -> one row per bill + long-format recoveries frame
    lineage.py       RNOTE/CRN loaders; attach_lineage joins them to bills via InvoiceNo -> CO6No -> CO7No fallback
  matching/
    scoring.py       pure per-pair functions: score_pair, confidence_label, weights (advice date 4 > zone 2 > CO7 date 1)
    matcher.py       three-pass loop: score all pairs -> assign best-first globally -> subset-sum batched pass
  engine.py    reconcile() (two-sided exceptions), exception_queue(), run() orchestrator
  report.py    write_workbook — formatting only, decides nothing
  cli.py       argparse entry point for python -m recon
backend/app/     FastAPI wrapper
  routes.py    POST /api/runs (multipart upload + tunables -> full JSON result;
               any file not uploaded falls back to the repo sample documents —
               GET /api/defaults lists them, `~$` lock files skipped),
               GET /api/runs/{id}, GET /api/runs/{id}/workbook; uploads saved to a
               temp dir because parsers take paths; errors map to
               BANK_SELFCHECK_FAILED (422) / PARSE_FAILED (422) / INVALID_INPUT (400)
  serialize.py frames -> JSON-safe; handles NaN/NaT/pd.NA, numpy scalars, the
               dict-valued Recoveries column and list-valued bill_indices column
  runs.py      in-memory run store keyed by run_id so workbook download never
               recomputes; also holds the source frames (bank/bills/
               bills_enriched/recoveries) served lazily by
               GET /api/runs/{id}/frames/{name} with per-frame serialization cache
frontend/        Vite + React + TS; @tanstack/react-table v8 (v9 renamed the API — keep the ^8 pin)
  src/api.ts, types.ts, format.ts (en-IN ₹ formatting), components/
               (Sidebar drives the layout: left nav with Run setup /
                Reconciliation result / Source data groups, App renders one
                view at a time full-width; UploadForm, AdvancedPanel,
                SummaryDashboard, DataTable, MatchedTable, ExceptionQueue,
                SourceTable, BillTrailDetail shared by queue + enriched bills,
                ErrorBanner)
```

Data flow in `run()`: parse the full statement once (`credits_only=False`; `bank_all` keeps every row with a `UsedInRecon` flag, `bank` is the derived TFR+ frame the matcher sees) → **fail loudly** if the parsed credit count/total doesn't tie to the totals HSBC prints on the last page (everything downstream depends on that parse) → parse bills + lineage → `reconcile()` → dict of frames (`matched`, `bank_only`, `bill_only`, `match_review`, `queue`, `summary`, `bank`, `bank_all`, `bills`, `recoveries`, `bills_enriched`). The API's POST payload serializes only `summary`/`matched`/`queue`; the source frames are served lazily per tab via `/frames/{name}` (the sample export is ~2.2k bills / ~2.7k recovery lines, so eager serialization would bloat the run response).

## Domain logic that isn't obvious from any single file

- **Both sides are sources of truth**, so exceptions run in both directions: `BANK_ONLY` (credit with no bill) and `BILL_ONLY` (advised bill with no credit). The exception queue deliberately mixes both; bank rows lack bill fields and vice versa by design.
- **The Bills + lineage tab serves `bills_grouped`, not `bills_enriched`** — `engine.group_bill_attempts` combines the one-block-per-processing-attempt export into one row per bill (a RETURNED bill gets a fresh CO6 on each resubmission, so it appears 2–7 times raw). Representative row = the settled attempt if any, else the chronologically latest (CO6Date asc, DataRow desc — smaller DataRow is more recent in this export); the full journey rides in the `Attempts` list column. Blank/`"-"` bill numbers are never merged with each other. Matching and the raw Bills tab always use the ungrouped frames.
- **Settlement is stamped onto bills_enriched** in `reconcile()`: every matched bill gets `SettledInStatement` + `Settled_*` columns (bank ref, value date, credit amt, confidence, `Settled_MatchId` = `m{n}` by result position, also on matched rows as `match_id`). All confidences are stamped in the data; the **display gate to HIGH-only lives in the frontend** (`SourceTable` injects a `Settled='SETTLED'` token for bills_enriched rows, which also makes it filterable, and the timeline adds the gold "Credit received at HSBC" closure event).
- **Weak matches are copied into the queue as `MATCH_REVIEW`** (confidence in `engine.REVIEW_CONFIDENCE` = AMBIGUOUS/LOW/AMOUNT_ONLY/BATCHED). They stay in the matched frame — bills remain claimed, totals unchanged — but the queue row carries the evidence: a structured `Candidates` column (list of dicts, one per candidate bill incl. lineage, `Picked` flag; fed by `MatchResult.candidate_indices` from the matcher's tie tracking) for the API/UI, and a flat `CandidateSummary` string for Excel (report.py drops the structured column — openpyxl can't write dicts).
- **Amount is a filter, not a signal.** Pairs are only scored if amounts already agree (indexed on `round(NetAmt, 2)`); zone and date break ties. Confidence labels (`HIGH`/`MEDIUM`/`LOW`/`AMOUNT_ONLY`/`AMBIGUOUS`/`BATCHED`) are derived from the raw signals, not by reversing the score.
- **A credit whose best pairing was claimed by another credit is not allowed to settle for a worse one** — it falls to the exception queue. A missing match you can investigate beats a wrong match you cannot see.
- **`window_days` (default 0)** decides which bills are "expected" in this statement: money cannot arrive before IREPS advises the bank, so the advice date must fall inside the statement's own dates. Widening it too far turns bills settled in earlier statements into false shortfalls.
- **`PAID_STATUSES = {"PAYMENT MADE", "CO7 DONE"}`** — CO7 DONE counts because the payment order goes out before the export refreshes the status. Defined both as the default in `config.ReconConfig.paid_statuses` and as a module constant in `matching/scoring.py`; keep them in sync.
- **IREPS data is dirty in specific ways** the code already handles — don't regress these: four spellings of "nothing here" (`None`, `nan`, `"nan"`, `"----"`) normalised by `scoring.norm_text`; IDs written as int in one export and str in another, normalised by `lineage._key`; non-breaking-space padding; recovery amounts sometimes packed several to a cell (`"1539.9, 2354.05"` — summed); Net Amt rounded to whole rupees (checks allow ₹1 slack).
- **Bill Status is a block-format sheet, not a table**: repeated header line / `CO6 No` column row / data row / optional Recovery Details / optional Reason For Return, starting at column D. Header parsing is label-driven so blocks with or without payment-advice fields both parse, and unknown labels land in `UnparsedHeader` rather than being dropped.
- **Zone extraction** from NEFT narratives tests longer railway zone codes first so `NER` isn't read as `ER` (`bank_hsbc.ZONE_CODES` order matters).
