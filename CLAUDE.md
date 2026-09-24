# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Multi-customer receivables reconciliation engine built on a medallion data architecture: raw source files (bronze) are parsed by per-source **adapters** into their native shape (silver), transformed into a **common gold schema**, and reconciled by a rule-driven matcher. The first sources are an HSBC daily-statement PDF and the IREPS "Bill Status" Excel export, with RNOTE / CRN reports attaching upstream document lineage so each payment traces back to its PO. Output is an Excel workbook plus a JSON API: matched rows, a two-sided exception queue, and a summary. A FastAPI backend (`backend/app/`) and React SPA (`frontend/`) wrap the same engine for browser use. Everything persists to a database — SQLite locally, AWS RDS Postgres in production — via one `DATABASE_URL`.

## Commands

```bash
.venv/bin/pip install -r backend/requirements.txt       # venv at repo root (Python 3.13)
.venv/bin/pip install -r backend/requirements-dev.txt   # + pytest for the golden gate
cd frontend && npm install                               # Node >= 18

# CLI, run from backend/ (sample inputs live in "../Receipt_reconciliation_and_IDR _ Requested_sample_documents/")
cd backend && ../.venv/bin/python -m recon --statement <stmt.PDF> --bills <bills.xlsx> \
                --rnote <rnote.xlsx> --crn <crn.xlsx> -o out.xlsx
# The CLI is DB-free: parses + reconciles + writes the workbook, nothing persisted.

# Web UI: two terminals (backend runs single-worker; SQLite + threadpool assume it)
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000   # startup runs alembic upgrade + seeds
cd frontend && npm run dev          # http://localhost:5173, proxies /api to :8000

# Tests — the golden-master gate is the backbone of every refactor
# RECON_DATA_DIR matters: the DB-backed tests run against whatever data dir
# db/base.py resolves, which DEFAULTS TO THE DEV DATABASE (backend/data/).
# They create throwaway customers and delete them afterwards, but a failed or
# interrupted run leaves rows behind in real data — always point them at a
# scratch dir instead.
cd backend && RECON_DATA_DIR=$(mktemp -d) ../.venv/bin/python -m pytest tests/ -q
cd backend && ../.venv/bin/python scripts/make_golden.py   # regenerate snapshots ONLY on intended behaviour change

# Database
# DATABASE_URL env var switches the store (default sqlite:///backend/data/app.db;
# point at RDS Postgres for production — schema auto-initializes on startup).
# One URL either way: locally that's 4 ATTACHed sqlite files (app/bronze/silver/gold.db,
# see db/base.py), on Postgres it's 4 real schemas inside the one database.
cd backend && ../.venv/bin/python -m alembic revision --autogenerate -m "..."   # after model changes; REVIEW the output
cd backend && ../.venv/bin/python -m alembic upgrade head

# Logins (every /api route except /api/health and /api/auth/* needs one)
cd backend && ../.venv/bin/python scripts/create_user.py -e you@example.com -n "You"
cd backend && ../.venv/bin/python scripts/create_user.py --list
# a blank database has NO users — nobody can sign in until one is made
# (or ADMIN_EMAIL/ADMIN_PASSWORD are set for an unattended first boot)

cd frontend && npx tsc -b           # typecheck
cd frontend && npm run build        # production build
```

The sample documents folder contains real input files (gitignored, as are the golden CSVs derived from them; filenames contain spaces; `~$...` files are Excel lock files — ignore them). `backend/tests/golden/*.csv` must exist locally for the tests — run `scripts/make_golden.py` once after cloning. KNOWN STATE since 2026-09-07: the local sample `BILL STATUS 20032026.xlsx` is the retired multi-sheet block format, so the golden gate, `test_incremental_scenario`, `test_snapshot_from_gold_matches_legacy_pipeline` and `test_real_export_smoke` (9 items) fail with `BillStatusFormatError` on every branch until a current single-table export replaces it and the snapshots are regenerated; the ledger/overview tests build synthetic customers instead and do not depend on it.

## Architecture

Layered, with one-way dependencies — parsers know nothing about matching, matching knows nothing about the engine, the engine knows nothing about Excel or the database, `db/` imports `recon` but never the reverse, and `backend/app/` wires it all to HTTP. Nothing imports upward. Keep it that way when adding code.

```
backend/recon/   the engine package (pure, DB-free; runs with backend/ as cwd)
  config.py    ReconConfig — CLI-facing config; builds a MatchRuleSet internally
  rules.py     MatchRuleSet — the "delta layer": tolerances, paid statuses, signal
               weights, FieldMapping (which GOLD fields drive matching: amount
               pair, date primary/fallback pair, exact-signal pairs with weights,
               eligibility field + fallback-due statuses), copy_overrides
               (per-customer advisory TEXT keyed by the frozen codes — see
               engine.DEFAULT_COPY/resolve_copy; COPY_SECTIONS lists the valid
               sections), batch_amount_slack (the once-hardcoded 0.5 batching
               slack), amount_decimals (amount-join rounding, was round(,2)) and
               ar_overdue_days (AR view aging threshold). Resolution: dataclass
               defaults <- customer DB rule set <- API tunables IF the caller
               sends them (explicit values win; the web UI deliberately sends
               NONE — /api/reconcile tunables are Optional and the saved
               matching config is the single source of truth for UI runs;
               field_map/copy are customer-level ONLY, never per-run overrides).
               EVERY MatchRuleSet field the engine reads must be hand-threaded
               through reconcile() at ALL THREE call sites (pipeline,
               db/reconcile_gold.py, db/incremental.py) — the golden gate covers
               only the first; meta.rules_effective is the E2E proof.
  parsers/     source document -> plain DataFrame (bank_hsbc, bill_status, lineage).
               Parser output keeps SOURCE-NATIVE names (IREPS PascalCase /
               RN_*/CR_*) — that is the silver vocabulary, persisted as-is to
               silver.records. bill_status.py reads the CURRENT "Bill
               Status" export — a plain table, one header row (20 IREPS
               columns) + one data row per bill — and locates that header
               by TEXT, scanning every worksheet's first few rows, never
               assuming a fixed row/column/sheet name. The column list is
               treated as a MOVING TARGET (it already moved once): only
               REQUIRED_HEADERS (Bill Number, CO6 No, Status, Net Amt) must
               be present — a known column the export DROPS NA-fills, and a
               column it ADDS is still extracted, under its own header text
               as the silver field name, riding through to_gold's rename
               (which only knows the mapped names) into gold `extras` with
               zero code changes. A workbook missing a required header
               raises BillStatusFormatError naming what it missed. `Recovery
               Details` stays ONE free-text cell in Silver ('<head>: <amt>
               <head>: <amt> ...') — splitting it into structured lines is
               a Gold-stage concern (sources/ireps_bills.py), not the
               parser's. `Sheet`/`DataRow` stay Silver-only bookkeeping;
               since 2026-09-07 the export is ONE worksheet, so the
               worksheet name says nothing about which unit a bill belongs
               to — gold `operating_unit` (Friction | Rohtak | Hosur; was
               `sheet`, migration a9e4d7c2b301 renamed + backfilled) is
               DERIVED in the IREPS bills adapter's to_gold from the
               PartyCode suffix (OPERATING_UNIT_SUFFIXES: ...1065309 =
               Friction, ...60828 = Rohtak, ...833 = Hosur, longest first,
               anything else NA) and rides onto each bill's recovery lines;
               it is not in ingest's BILL_MUTABLE because vendor_code is
               immutable identity. (An older BLOCK-per-bill export format existed
               before 2026-08 and is no longer supported — this parser
               does not read it.) Accepts legacy .xls (BIFF) as well as
               .xlsx/.xlsm — `_open_workbook` dispatches by extension to
               openpyxl or a small xlrd-backed shim (`_XlsWorkbook`/
               `_XlsSheet`) exposing the same `.sheetnames`/`.iter_rows`
               surface, so the header-detection/row-extraction logic
               above is shared, not duplicated, across both containers;
               `_XlsSheet` normalizes BIFF's lack of an int type (every
               number is a float) and its serial-number dates to the same
               plain int/datetime openpyxl already gives. RNOTE/CRN
               (lineage.py, below) get .xls "for free" the same way,
               since they already load via `pandas.read_excel`, which
               picks openpyxl/xlrd by the file itself — only their
               adapters' `file_kinds` needed the literal `.xls` added.
               lineage.py also owns attach_lineage: a generic
               role-aligned join of the CANONICAL unified lineage frame onto
               bills (bill_number->invoice_no, then submission_ref, then
               payment_order_ref), building the trail columns (TRAIL_MAP: PO,
               Receipt_Doc, ..., Invoice_Date, Bill_Reg_Date — frozen artifact
               names), one {doc_type}_MatchedVia column per attached type
               (values InvoiceNo/CO6No/CO7No are FROZEN literals meaning
               "matched via bill number / submission ref / payment order ref")
               and LineageStatus ("+"-joined doc types in LINEAGE_DOC_PRIORITY
               order — RNOTE, CRN, then first-appearance for new types).
  sources/     adapter layer: (source_type, adapter_key) registry + BY_KEY.
               Slots resolve adapters by ROLE (base.role_of: bank_statement |
               bill_status | lineage — any lineage-role adapter fits any
               lineage_* slot, via resolve_adapter). Each adapter:
               parse() -> silver, to_gold() -> gold frames (+row_seq, ensure_schema),
               optional selfcheck() raising SelfCheckError (fail-loud control totals).
               to_gold() owns the silver->canonical rename map (ireps_bills.py
               BILLS_TO_GOLD/RECOVERIES_TO_GOLD; ireps_rnote/ireps_crn.py
               RNOTE_TO_GOLD/CRN_TO_GOLD + their doc_type value) — THE seam a
               new bank/ERP source plugs into: one ~40-line adapter + its map
               onto the same canonical columns + a source_configs row.
               tests/test_adapters.py guards map<->schema drift;
               tests/test_synthetic_source.py is the definition-of-done proof
               (synthetic bank + ERP + a NOVEL "GRN" lineage doc type run
               snapshot AND incremental with ZERO engine edits) — keep it
               green forever.
  gold/        schemas.py — ONE canonical source-agnostic snake_case schema per
               input kind, used END-TO-END: engine frames, DB columns, API
               payloads, frontend, workbook. IREPS's CO6 -> submission_ref/date
               (also the DEFAULT bill entity-upsert identity), CO7 ->
               payment_order_ref/date (the fallback due-date signal), amounts
               are gross/approved/deduction/net_payable_amount. Lineage is ONE
               unified lineage_docs schema (doc_type discriminator) — canonical
               end-to-end since the 2026-08-31 canonicalization; RN_*/CR_*
               names survive only in silver and as extras keys on old gold
               rows. The docstring lists reserved future columns (tds/gst/
               utr/...) and the scope line: engine-DERIVED run-artifact names
               (trail columns, LineageStatus, ExpectedBasis, Settled_*,
               Attempts, Candidates, gap_type) are NOT gold schema and keep
               their names.
  matching/    scoring.py (per-pair signals, weights overridable), matcher.py
               (three-pass loop — score all pairs, assign best-first, subset-sum
               batch; amount rounding follows amount_decimals, the batch pass's
               slack is max(amount_tolerance, batch_amount_slack); NOTE the
               batch pass only sees credits that HAD an amount candidate but
               lost it — a credit with no single-amount candidate goes straight
               to BANK_ONLY, historical behaviour)
  engine.py    reconcile(bank, bills, lineage_df, ...) (two-sided exceptions;
               advisory `action` text from resolve_copy(copy_overrides) over
               DEFAULT_COPY — codes frozen, text per-customer),
               exception_queue(), run(cfg) = thin wrapper over pipeline with
               the HSBC/IREPS adapters
  pipeline.py  run_pipeline(inputs, adapters, params, rules, sinks): adapters ->
               gold -> engine, gold flows in memory, persistence via optional
               sinks. Slots process in _slot_order (bank, bills, then lineage
               slots by name); lineage frames key by SLOT and concat into one
               frame for the engine. check_signal_coverage() WARNs (log +
               on_selfcheck sink + meta.signal_coverage in run payloads) when a
               mapped exact-signal column is missing/all-NA — the silent
               LOW/AMOUNT_ONLY degradation a misconfigured new adapter causes
  report.py    write_workbook — formatting only, decides nothing;
               append_ledger_sheets(path_in, frames, path_out) and
               write_ledger_workbook(frames, path) take plain DataFrames
               built by db/ledger_export.py (recon never imports db)
backend/db/      persistence — imports recon, never the reverse. Real per-layer schema
               separation, not a naming convention: every model declares
               schema="bronze"|"silver"|"gold" (app/control-plane tables omit it).
               On Postgres that's a native CREATE SCHEMA. SQLite has no schema
               concept, so register_sqlite_attach() (base.py) fakes it with
               ATTACH DATABASE — locally each layer really is a separate file:
               data/app.db (main), data/bronze.db, data/silver.db, data/gold.db.
               FK target strings must be schema-qualified whenever the TARGET is
               bronze/silver/gold (e.g. ForeignKey("gold.bills.id")) — required
               even gold->gold, unrelated to the referencing table's own schema;
               bare strings (ForeignKey("runs.id")) are correct when the target
               is app-layer, from any referencer. SQLite can't enforce FKs across
               ATTACHed files (harmless: no relationship(), all joins explicit) —
               Postgres enforces the same edges for the first time, an accepted
               asymmetry, not a bug.
  base.py      DATABASE_URL + session factory + register_sqlite_attach();
               init_db() = alembic upgrade head + seed
  models.py    all tables, snake_case, customer_id everywhere: users (the login
               gate — app schema, NOT tied to a customer: authentication only,
               `customer_id` is still a request field; is_active is re-read on
               EVERY request, which is the only way to withdraw a stateless
               cookie), customers,
               source_configs (source_type doubles as the SLOT KEY, unique per
               customer; `role` = bank_statement | bill_status | lineage —
               lineage slots are 0..N per customer, named lineage_<key>, the
               seeded pair keeps lineage_rnote/lineage_crn; params carries
               per-slot config incl. the optional "entity_key" natural-key
               override), match_rule_sets (+ field_map, copy_overrides,
               batch_amount_slack, amount_decimals, ar_overdue_days — the
               nullable columns mean "dataclass default"), bronze.files,
               silver.records,
               gold.bank_txns/bills/recoveries/lineage_docs,
               gold.file_rows (which gold rows each ingested file REPORTED —
               the many-to-many an entity upsert would otherwise erase),
               runs, run_frames,
               run_match_bills, match_ledger(+bills — `seq` is the durable
               per-customer match number, UI "M-{n}"; the engine's match_id
               m0/m1/… restarts per run and stays run-internal; run_id is
               NULLABLE since b2d7e9f4c1a8 — a confidence=MANUAL match is
               created by a user, not a run, match_id "manual", born
               LOCKED/USER, optional `note` ≤500 chars; every join on
               MatchLedger.run_id must tolerate NULL),
               exception_ledger (`resolved_by` = RUN | USER_ACCEPT |
               USER_MANUAL | USER_REOPEN | USER_NON_IREPS +
               `resolved_by_match_id`; first_seen_run_id nullable for the
               same reason; USER_NON_IREPS is a classification, never
               counted as a resolved exception),
               credit_sources (app schema, one row per credit: an
               analyst's IREPS | NON_IREPS decision from the Analyst
               queue's Non-IREPS tab — see "Non-IREPS receipts" below),
               ingest_conflicts,
               audit_log (general-purpose event stream, app schema — NOT a
               replacement for match_ledger/exception_ledger/ingest_conflicts,
               which stay the detailed domain-specific trails for their own concerns;
               `actor_user_id` = the signed-in user behind the event, NULL =
               system or pre-d8a4f2c6e1b9). WHO DECIDED (d8a4f2c6e1b9):
               match_ledger.decided_by_user_id/decided_at/decision_note (the
               LATEST user decision + its optional note — `note` stays the
               MANUAL match's creation note), exception_ledger.
               resolved_by_user_id (USER_* resolutions only), credit_sources.
               decided_by_user_id/note. All soft references to users.id (no
               FK: an FK would make SQLite batch-recreate the ledger tables
               and drop their cross-schema edges; users are deactivated,
               never deleted)
  storage.py   LocalStorage (data/bronze/ content-addressed by sha256, data/runs/) —
               S3-swappable seam. Note: data/bronze/ (blob dir) and data/bronze.db
               (bronze-schema DB file) are unrelated, don't confuse them.
  audit.py     record_event(session, logger, ...) — the ONLY place a log line and
               its audit_log row get written; never commits, rides the caller's
               existing transaction, so a rollback erases both together. The
               ACTOR is ambient: `current_actor` ContextVar, set by app/auth.py
               require_user — which is ASYNC for exactly this reason (a sync
               dependency runs in a worker thread whose context never reaches
               the route; an async one sets it in the request task, and anyio
               copies it into a sync route's thread). record_event stamps it;
               db/incremental reads actor_id() for the ledger's who-columns.
               Nothing threads a user parameter. tests/conftest.py's override
               is async + set_actor too, so API tests stamp TEST_USER_ID (0)
  bronze.py    register_file — dedup by (customer, sha256)
  silver.py    one JSON row per parsed source row, deduped per bronze file.
               WRITE-ONLY on purpose: silver is the audit trail of what each
               parser read, and consumers rebuild frames from GOLD — there is
               no silver browse API and no silver UI tab (both existed briefly
               and were removed; don't re-add one without a real consumer)
  gold.py      persist + frame_from_gold (rebuild engine frames for the pool).
               Since the gold + lineage canonicalizations EVERY frame<->DB map
               is IDENTITY — BANK_MAP/BILLS_MAP/RECOVERIES_MAP/LINEAGE_MAP
               (kept as dicts: they decide which columns are typed DB columns vs
               extras JSON). lineage_frame(session, customer) rebuilds the ONE
               unified lineage frame in attach-priority order (RNOTE, CRN,
               others; (bronze_file_id, row_seq) within a type) — used by both
               reconcile_gold and incremental. FRAME_DATE_COLS must track any
               column rename in lockstep or rebuilt dates silently come back as
               datetime.date, breaking matcher arithmetic.
               reported_by_file(session, model, bronze_file_id, customer_id) is
               THE way to ask "which rows did this upload bring": gold rows are
               entity-upserted, so a file OWNS only what it first inserted but
               REPORTS everything it carried (gold.file_rows). Never filter a
               user-facing "this ingestion" view on bronze_file_id directly.
  ingest.py    idempotent gold ingestion: file-level dedup + entity-level upsert
               (bills by (bill_number, submission_ref) BY DEFAULT — daily IREPS
               exports are different FILES with the same BILLS; the key is
               per-customer config via source_configs.params["entity_key"],
               threaded as ingest_gold_frames(entity_keys=...) for an ERP with
               no CO6-like ref; bank txns likewise). Under the DEFAULT bill key
               a blank bill_number ('-', IREPS works contracts) matches on the
               CO6 ALONE (_bill_key) — before 2026-09-16 it never matched, so
               every daily export stored one more copy with its own BILL_ONLY
               exception; a custom entity_key keeps the strict all-columns
               rule. db/bill_merge.py + scripts/merge_duplicate_bills.py
               (dry run by default, --apply) repair a database loaded with the
               old rule: keep the matched (else earliest) copy, move links,
               DELETE the phantom exceptions and duplicate recovery lines,
               audit gold.bills_merged. Lineage ingest is one
               generic path for ANY lineage frame (doc_type rides in the data,
               append-only keyed (doc_type, doc_no)); LOCKED bills never
               mutate -> ingest_conflicts. Every ingest also records what the
               file REPORTED into gold.file_rows (_record_sightings, one row per
               gold row carried, inserted/updated/unchanged alike) and returns
               stats["rows_reported"] — without that an export whose rows all
               already exist leaves no trace anywhere in gold. stats["by_frame"]
               breaks the same counters down per gold frame (bank_txns | bills |
               recoveries | lineage_<slot>, each reported/inserted/updated/
               unchanged/conflicts) — what the UI renders as "Total bills / New
               bills / Duplicate bills (not added)" vs "New transactions"
               (frontend IngestStatsSummary, the one place that vocabulary
               lives); the flat keys stay for compat (tests + every persisted
               ingestion.completed audit row), old rows lacking by_frame fall
               back to the flat line
  incremental.py  Phase-6 runs: pool = new credits + open exceptions vs all
               unconsumed bills, minus approved non-IREPS receipts; the
               pool's other non-IREPS credits (no first-exact-signal value,
               not marked IREPS — unmatchable_positions) ride along but are
               never scored (reconcile(unmatchable=...), default None = no
               change for every other caller); UNCHANGED matcher otherwise; match_ledger (HIGH auto-LOCKs),
               exception lifecycle OPEN -> RESOLVED; one running run per customer
               (partial unique index -> 409 RUN_IN_PROGRESS). User decisions
               also move the exception ledger: accept_match / reopen_match
               RESOLVE the OPEN BANK_ONLY + BILL_ONLY rows behind the match
               (_resolve_exceptions_for_match, stamped USER_ACCEPT /
               USER_REOPEN); unlock never re-opens them (an OPEN match still
               claims both sides); reject re-opens the credit, and for a
               MANUAL match the bills too (no run is coming to re-report
               them). create_manual_match(customer, txn, bill_ids, note):
               both sides must be unconsumed (else ValueError -> 409
               ALREADY_CONSUMED naming the holder), NO amount tolerance —
               the response carries `variance` — and the pool machinery
               excludes it from every later run via _consumed with zero
               engine change.
               rescore_provisional(session, customer, run_id, rules) runs
               BEFORE run_matching (both routes call it, stats merged into
               the run's `ledger` stats): an OPEN review match (AMBIGUOUS |
               LOW | AMOUNT_ONLY | BATCHED) with run_id set and NO analyst
               decision on it (decided_by_user_id NULL) is released and its
               credit re-scored, through the same matcher (_engine_reconcile
               is the ONE incremental engine call), against every bill no
               other OPEN/LOCKED match holds. Only a HIGH result acts: the
               old row goes REJECTED (locked_by AUTO_SUPERSEDED, decision_note
               "Superseded by M-n"), the new one is written like any HIGH
               (auto-LOCKED, run_id = this run, match_id "s{k}") and the
               bills' OPEN BILL_ONLY rows RESOLVE by RUN. Never swaps one
               review for another (no churn), never touches a match an
               analyst has decided, and skips (provisional_kept_conflict) a
               HIGH that would take a bill another still-standing weak match
               holds. Why it exists: IREPS lists a bill days after its
               money, and an OPEN match used to claim its credit + bills
               forever, so a weak pairing made while the right bill was
               missing (M-81: an NER credit on an SR bill of the same
               amount) could never be replaced. The superseded match's own
               bills are simply free again; the run's matching reports them.
               The run's frozen frames do not contain the replacement (it
               happens before matching) — the ledger and audit trail do.
  ledger_export.py  ledger -> DataFrames for Excel, composed at DOWNLOAD
               time (a manual match belongs to no run and decisions happen
               after a run's workbook was written): run_ledger_frames(run)
               = Manual_Matches (touching this run's credits/bills) +
               Decisions (live status of the run's matches), EMPTY for a
               snapshot run so the stored file streams back byte-identical;
               customer_ledger_frames = Matches / Manual_Matches /
               Exceptions for GET /api/ledger/workbook
  runs_store.py  persisted runs (payload/frames/workbook survive restarts)
  seeds.py     idempotent default customer wired to hsbc/ireps adapters;
               DEFAULT_SOURCES rows are (source_type, role, adapter_key, params).
               seed_admin_user() creates the FIRST login from ADMIN_EMAIL/
               ADMIN_PASSWORD and only while the users table is empty — it never
               re-passwords an existing account, so rotating the env var is a
               no-op (a seed that reset a password every restart would be a way
               back in for anyone who once read the task definition)
backend/alembic/ migrations; env.py reads DATABASE_URL, render_as_batch=True.
               ALWAYS review autogenerate output (it has produced bad imports, wrong
               column types, and — against SQLite specifically — spurious "add
               missing FK" diffs for every cross-schema edge; that last one is
               expected noise from SQLite's ATTACH limitation, strip it by hand,
               don't apply it). fileConfig(..., disable_existing_loggers=False) —
               the default (True) permanently disables every logger not listed in
               alembic.ini, including every db.*/app.* logger (Logger objects are
               cached singletons; once disabled, disabled forever in that process).
backend/passwords.py  bcrypt hashing, a top-level sibling of recon/db/app for
               the same reason logging_setup.py is one: app/auth.py, db/seeds.py
               and scripts/create_user.py all need it and db/ must never import
               app/. Refuses a password over bcrypt's 72-BYTE limit instead of
               silently hashing only the first 72 (validate_password /
               hash_password / verify_password / normalize_email — email is
               lowercased + stripped in ONE place, so the users.email unique
               index is the real uniqueness rule).
backend/logging_setup.py  top-level sibling of recon/db/app (not inside any of
               them) so all three can use it with no recon->db dependency.
               get_logger(name), configure_logging() (console text + rotating JSON
               file at data/logs/app.log). configure_logging() must be called AGAIN
               after init_db() (app/main.py's lifespan does this) — init_db() runs
               alembic, whose own fileConfig() call replaces the root logger's
               handlers every time. request_id/customer_id/run_id ride ContextVars
               + a logging.Filter — verified to propagate through run_in_threadpool
               (anyio copies the context into the worker thread) and into a route
               handler from middleware, but NOT back out of a route handler into
               the middleware's own post-call_next log line (Starlette's
               BaseHTTPMiddleware runs call_next's inner app in its own spawned
               task — contextvars propagate forward into new tasks, never
               backward out); routes.py sets request.state.customer_id (a shared
               object attribute, not a contextvar) for that one summary-line case.
               Only called by app/main.py and recon/cli.py — never by db/ or
               tests, which is the entire "no log files during pytest" strategy.
backend/db/reconcile_gold.py  two-step workflow: snapshot-from-gold (sibling of
               incremental's pool machinery with OPPOSITE ledger semantics —
               ALL bills incl. ledger-consumed, no carried exceptions, real
               window_days; verified to reproduce the legacy snapshot's exact
               counts, see tests/test_reconcile_gold.py) + the gold browse
               helpers (gold_frame/gold_files/list_ingestions/
               get_statement_bronze); the lineage browse serves the same
               canonical unified shape the engine consumes (LINEAGE_MAP)
backend/app/     FastAPI wrapper — TWO-STEP flow in the UI: (1) POST /api/ingest
               (multipart, each slot optional, ≥1 required; bronze -> silver ->
               gold standalone; writes one ingestion.completed audit event in
               the same transaction as the gold writes; NO Run row on failure.
               AN INGESTION IS EXACTLY THE FILES POSTED: every uploaded slot is
               processed, an empty slot is simply not part of it, nothing is
               ever substituted for one, no files at all -> 400, and a file
               posted under a field no slot answers to -> 400 rather than being
               dropped in silence. There is NO `slots` form field and no
               bundled-document fallback — both existed to let the seeded
               `default` customer ingest the repo's sample files for an empty
               slot, which made a run silently differ from what the user chose),
               (2) POST /api/reconcile (JSON: customer_id, statement_bronze_id,
               mode; six Optional tunables — omitted = customer config applies;
               both modes source purely from gold — snapshot
               keeps legacy semantics/no ledger, incremental feeds the ledger;
               404 STATEMENT_NOT_FOUND, 422 RECONCILE_FAILED; selfcheck reruns
               the CUSTOMER'S bank adapter's own selfcheck() against the
               stored bronze statement — routes never call a source-specific
               check function — mismatch = passed:false + WARNING, never a
               422). Reads: GET /api/ingestions,
               GET /api/gold/files (feeds statement picker + gold-tab filters),
               GET /api/gold/{frame} for frame in bank|bills|recoveries|lineage
               (whole-frame with 20k cap, {count,total} exposes truncation;
               ?bronze_file_id= filters to what that ingestion REPORTED, so a
               re-export of existing bills still shows its own rows).
  auth.py      THE login gate: signed session cookie + the users table.
               require_user is attached ONCE, as a router-level dependency on
               app/routes.py's /api router, so all 28 routes are gated together
               and a route added later is protected by construction rather than
               by remembering to decorate it. Public because they hang off
               something else: /api/health (registered on `app` in main.py — the
               ALB health check carries no cookie) and /api/auth/* (this module's
               own ungated router: POST login / POST logout / GET me). Cookie not
               bearer token BECAUSE the SPA and API share an origin (frontend.py
               mounts the build at '/', Vite proxies /api in dev), so fetch()
               attaches it by itself and not one of the ~30 call sites in
               frontend/src/api.ts knows a credential exists. The cookie is
               stateless (itsdangerous-signed, carries only a user id), so there
               is no session table and a restart logs nobody out — and the ONLY
               revocation path is require_user reloading the row every request
               and refusing is_active=false. Login answers identically for an
               unknown email, a wrong password and a deactivated account (and
               spends the same bcrypt time on a miss), so it never tells a
               stranger who has an account; failures throttle per email
               (LOCKOUT_AFTER/LOCKOUT_SECONDS, in-process — exact under the
               single-worker uvicorn already assumed elsewhere). SESSION_SECRET /
               SESSION_MAX_AGE / COOKIE_SECURE, all documented in
               docs/configuration.md. AUTHENTICATION ONLY: every signed-in user
               still sees every customer, and customer_id is still a request
               field — binding a user to a tenant is the follow-up.
  routes.py    legacy POST /api/runs (multipart one-shot) KEPT for compat/tests
               but retired from the UI; GET /api/runs/{id}[/frames/{name}|
               /workbook], GET /api/customers, GET /api/runs, GET /api/ledger,
               GET /api/overview (optional ?from=&to=&operating_unit=
               repeatable; "UNASSIGNED" is the bucket for bank-only credits,
               which have no unit — a unit filter without it HIDES them and
               filters_applied.bank_only_unassigned says how many; which
               date each figure windows on is in db/overview.overview's
               docstring — match counts go by the CREDIT's value_date, not
               the match's created_at. The credit funnel: credits = other
               receipts (out_of_scope, UNRECOGNISED_RECEIPT) + IREPS credits
               (in_scope) = awaiting_status + awaiting_bill_data +
               recognised (the match rate's denominator). open_in_scope
               {bank_only, bill_only, count, value, awaiting,
               awaiting_value} is the Open exceptions tile — open
               exceptions WITHOUT other receipts (counted once, as
               out_of_scope) and WITHOUT credits awaiting data (counted
               once, as awaiting_*_credits, named by `awaiting`), so
               Settled's "of N" and the tile read one definition;
               top_exceptions excludes both too;
               open_exceptions/open_value stay all-inclusive),
               GET /api/ledger also hands each OPEN BANK_ONLY exception its
               read-time gap_detail (db/overview.gap_details — AWAITING_STATUS
               / AWAITING_BILL_DATA / MARKED_IREPS / BILL_RETURNED are
               never stored) + gap_label/gap_action resolved
               through the customer's copy_overrides, and each bill its
               due_date (advice -> order -> submission); GET /api/gold/bank
               adds a read-time credit_scope column (db/overview.
               credit_scopes: RECOGNISED | AWAITING_STATUS |
               AWAITING_BILL_DATA | UNRECOGNISED_RECEIPT) so funnel figures
               can open the table filtered to their own credits, and a
               read-time `source` (IREPS | NON_IREPS | DEBIT for
               used_in_recon=false rows, db/overview.
               credit_sources — read off credit_scope, zone rule for a
               credit no run has seen; the page's "Source" column, derived
               from zone in run scope) + `gold_bank_txn_id`/`source_decided`,
               POST /api/exceptions/{id}/
               non-ireps/approve|reject|undo (404 EXCEPTION_NOT_FOUND,
               409 EXCEPTION_CONFLICT), PUT /api/bank/{gold_bank_txn_id}/
               source {customer_id, source: IREPS|NON_IREPS|null} — the
               SAME decision made from the credit (Bank Transactions'
               editable Source cell, Current scope only; incremental.
               set_credit_source): NON_IREPS = approve (a credit no run has
               seen gets a RESOLVED USER_NON_IREPS row at once so every
               count agrees; 409 CREDIT_MATCHED while a live match holds
               it), IREPS = reject (an approval re-opens), null = auto;
               debits 400,
               GET /api/customers/{key}/operating-units (distinct gold
               bills units + counts), GET /api/ar, GET /api/audit,
               GET /api/ledger/workbook (customer ledger export),
               POST /api/matches/{id}/accept|reject|unlock|reopen (each takes
               an optional JSON {note} ≤500 chars — incremental.clean_note,
               blank -> None, longer -> 400; accept also gold_bill_id),
               POST /api/matches/manual (customer_id, gold_bank_txn_id,
               gold_bill_ids[], note -> 409 ALREADY_CONSUMED / 400
               INVALID_INPUT); GET /api/runs/{id}/workbook appends the
               ledger sheets on the fly (stored file never rewritten);
               errors map to
               BANK_SELFCHECK_FAILED (422) / PARSE_FAILED (422) / INVALID_INPUT (400)
               / RUN_IN_PROGRESS (409); /api/runs requires a real statement +
               bills upload (400 otherwise) — there is no GET /api/defaults and
               no sample substitution anywhere in the API; run
               payloads' meta.selfcheck comes from each adapter's selfcheck()
               captured via the pipeline's on_selfcheck sink (never re-checked
               in routes); upload extension checks derive from adapter
               file_kinds (union per source_type over the registry — no
               hardcoded ALLOWED list; empty file_kinds = unrestricted).
               Configuration endpoints: GET /api/adapters (registry with
               labels, system, file_kinds, role — the UI's accept attrs follow
               the selected adapter's file_kinds; lineage-role adapters fit any
               lineage slot), GET /api/gold/schema (field/date/numeric lists
               feeding the config UI dropdowns — declared BEFORE
               /api/gold/{frame}, route order matters),
               GET/PUT /api/customers/{key}/config (effective merged rules incl.
               field_map + copy: GET returns copy_overrides (sparse, what's
               stored) AND copy_effective (fully resolved text); PUT validates
               fields against GOLD_COLUMNS and copy sections/codes against
               COPY_SECTIONS/DEFAULT_COPY -> 400, stores copy SPARSELY — only
               entries differing from defaults — normalizes field_map via
               FieldMapping.from_dict, audits config.rules_updated with
               details.changes = [{field, from, to}] per setting the save
               actually changed (routes.config_changes over flattened dotted
               paths, e.g. weights.zone; a save that changes nothing logs
               NOTHING) — settings are not financial content, so values are
               logged; PUT /sources logs config.sources_updated the same way,
               per slot <slot>.adapter / <slot>.params.<k>),
               PUT /api/customers/{key}/sources (adapter per slot; lineage
               slots are 0..N — name a new lineage_<key> slot to ADD it, null
               to REMOVE (deactivate; singletons refuse); optional per-slot
               `params` validated (entity_key ⊆ gold columns); params kept when
               adapter unchanged, reset when changed; audits
               config.sources_updated), POST /api/customers (key
               ^[a-z0-9_-]{1,64}$, 409 CUSTOMER_EXISTS, clones default sources +
               rule set, audits customer.created). /api/ingest accepts extra
               lineage slots as form fields named by slot key (discovered from
               the customer's source_configs). Every run payload echoes
               meta.rules_effective (field_map/paid_statuses/weights/
               copy_overridden/batch_amount_slack/amount_decimals) — the E2E
               proof a run used the customer's config — plus
               meta.signal_coverage (WARN when a mapped signal column is
               missing/all-NA; null = healthy).
               Shared helpers (_load_customer_context/_register_inputs/
               _build_adapters/_effective_rules/_persist_side_effects) are used
               by BOTH the legacy and two-step paths — behavior changes there
               affect both.
  main.py      configure_logging() at import; SessionMiddleware (same_site=lax,
               https_only=COOKIE_SECURE, httponly always — Starlette sets it and
               takes no parameter for it) + the ungated auth router mounted
               BEFORE the gated one; CORS carries allow_credentials so the
               belt-and-braces direct-to-:8000 origin can still send the cookie;
               @app.middleware("http") logs one
               http.request line per call (method/path/status/duration_ms) after
               call_next, catching every request incl. 404s; @app.exception_handler
               (Exception) logs full stack traces for anything that escapes every
               try/except already in routes.py, without shadowing FastAPI's own
               HTTPException/RequestValidationError handling (verified)
  serialize.py frames -> JSON-safe (NaN/NaT/pd.NA, numpy, dict/list columns)

## Logging / audit event taxonomy

One vocabulary shared between log `event_type` and `audit_log.event_type` (see
`db/audit.py:record_event`, called at the point each event happens, never
after the fact): `bronze.file_registered`/`bronze.file_deduped`,
`silver.rows_persisted`, `gold.rows_persisted` (snapshot), `gold.ingest_completed`
(incremental summary), `gold.ingest_conflict` (WARNING — a newer export tried
to change a LOCKED bill), `gold.bills_merged` (scripts/merge_duplicate_bills.py —
counts + {kept id: [deleted ids]}), `run.started`/`run.start_conflict`/`run.succeeded`/
`run.failed`/`run.selfcheck_failed`/`run.parse_failed`, `ledger.finalized`
(summary, not per-match), `ledger.match_accepted`/`ledger.match_rejected`/
`ledger.match_unlocked` (LOCKED -> OPEN undo; details carry was_locked_by),
`ledger.match_superseded` (rescore_provisional replaced an untouched review match
with a HIGH one; details was_confidence/superseded_by_seq/exceptions_resolved),
`ledger.non_ireps_approved`/`ledger.non_ireps_rejected`/`ledger.credit_source_undone`/
`ledger.credit_source_set` (Bank Transactions edit; details was/source/via),
`http.request`, `http.unhandled_exception`, `pipeline.selfcheck`,
`auth.login_succeeded`/`auth.login_failed` (WARNING; `details.reason` is a code —
`NO_SUCH_USER`/`INACTIVE`/`BAD_PASSWORD` — and an email address NEVER appears in a
log line or an audit row, it is PII like any other)/`auth.logout`,
`auth.admin_seeded`/`auth.admin_seed_rejected`/`auth.ephemeral_session_secret`
(WARNING — no SESSION_SECRET set). PLATFORM SETTINGS ARE FULLY AUDITED:
`config.rules_updated`/`config.sources_updated` carry `details.changes`
[{field, from, to}], compared in FULL and only shortened (160 chars) for the
record; `customer.created` carries its starting config the same way (from
null); account changes — `auth.admin_seeded` (now an audit row too, via
"seed") and scripts/create_user.py's `user.created`/`user.updated`
(`changed_fields` NAMES only: password, name, is_active — a display name can
be the email)/`user.activated`/`user.deactivated` (via "cli", no-op toggles
log nothing) — have NULL customer_id and entity `user`, and
db/overview.audit_events shows them in EVERY customer's feed, since they
change who can reach every customer. Decision events (`ledger.match_*`,
`ledger.non_ireps_*`, `ledger.credit_source_*`) carry the analyst's optional
`note` — user-written free text, deliberately kept because the Audit trail
shows it; source events carry `was` + `was_auto` (the engine's reading when
no analyst had decided — before 2026-09-23 that was logged as null). High-volume
operations log one aggregate summary (the same `stats` dict already returned
to API callers), never one line per row/match. No PII/financial content in
log lines or `details` — counts, ids, field *names* only (e.g. `gold.ingest_conflict`
logs `changed_field_names`, never the before/after values, which stay in the
existing `ingest_conflicts.changed_fields` column; `pipeline.selfcheck` omits
HSBC's `stated_total`/`parsed_total`, logging only `passed`/counts).
backend/tests/conftest.py  patches FastAPI.__init__ so EVERY app built during a
               test run starts with require_user overridden by a fixed user — the
               eight API test files predate authentication and needed no edits.
               It patches the constructor rather than using a fixture because
               those apps are built at module-import and module-fixture time,
               before a function-scoped fixture could reach them, and it imports
               app.auth lazily so an engine-only run still never touches the web
               layer. tests/test_auth.py CLEARS that override on its own app and
               is the only place the gate is exercised for real — without it an
               accidentally ungated router would still look green.
backend/scripts/make_golden.py + backend/tests/  golden-master gate (byte-exact CSV
               diff of summary/matched/queue/bills_enriched/bank/recoveries on the
               sample docs) + the incremental scenario test
frontend/        Vite + React + TS; auth.tsx's <AuthGate> wraps <App/> in main.tsx
               rather than living inside it — App's mount effects all call the
               API, so the tree must not exist before there is a session; it asks
               GET /api/auth/me once on load (the cookie is httponly, so only the
               server can answer "am I signed in", and NOTHING about the session
               is cached in localStorage where it could disagree), renders
               components/LoginView.tsx otherwise, and exposes useAuth() for the
               sidebar's name + Sign out. api.ts announces any 401 ONCE as a
               window event (onSessionEnded) instead of teaching ~30 call sites
               about sessions — a failed sign-in is excluded, that 401 answers a
               question the form asked. @tanstack/react-table v8 (keep the ^8 pin)
               + lucide-react (nav/button icons — professional stroke set,
               tree-shaken per import; the only other runtime dep);
               ONE VISUAL LANGUAGE (since 2026-09-21, feat/ui-unify): the
               Command Center's redesign was promoted to a shared kit —
               components/ui.tsx (Page / PageHeader / Card / Notice /
               EmptyState / Stat + StatStrip / PartitionBar /
               CustomerSelect / RefreshButton / TextLink / ToolSep / Dot)
               over the `ui-*` classes in styles.css (the "UI kit"
               sections; `cc-*` is left only for Command-Center-only
               pieces: ring, received figure, attention rows, board).
               Every page is PageHeader (title + a quiet context line;
               tools right: filters, refresh, separator, actions, primary
               last) then cards; a table's active filters show in a
               `ui-filterbar` chip row and "N of M · Show all" sits in its
               `ui-tabbar`; tables inside a card (`.ui-card .ledger`,
               `.ui-card table.data`) take the Command Center's table look
               automatically. Number/date display helpers (n, pct, plural,
               fmtDay, ageDays, inrCompact) live in format.ts. The
               pre-kit shells (intake, result-head, view-card, cc-panel,
               tiles, slot rows) were deleted from styles.css — build new
               UI from the kit, never from those names. UI COPY IS
               ENTERPRISE-TERSE (2026-09-21): no explanatory paragraphs or
               narrative card footers; page context lines are the customer
               and scope only; card sub-lines and empty states are a few
               words plus an action; guidance that still matters goes in a
               tooltip or a one-line field hint; no internal jargon (gold /
               bronze / silver, payload, durable) and no "you/YOUR" or
               rhetorical asides in user-visible text.
               ONE SCROLL CONTAINER (2026-09-23): `.content` is the only
               scroller (height 100vh, overflow-y auto) — the window never
               scrolls. A page whose job is one big table wears
               `ui-page is-fill`, its table card `ui-card is-fill`, and the
               scroller inside it `dt is-fill` (DataTable's `fill` prop) or
               `ledger-wrap is-fill`: header, figures, tabs and filters stay
               put while only rows scroll, and a taller window shows more
               rows with no code change (min-height 260px on the card means
               a very short window scrolls the page instead of crushing the
               table). Analyst queue / AR / Audit / Data pages / Matched /
               Exception queue are fill pages; Command Center, Ingest,
               Reconcile, Summary and Architecture scroll normally.
               Density: a match row is ONE line and uses the SAME spine
               as the exception tables (2026-09-23) — Match · Status ·
               Reference · Zone · Date (the CREDIT's value date: sort +
               range filter; the run time is on hover) · Amount (once; ⚠
               only when the bills do not total the credit) · Bill (a
               number-less works-contract bill shows "CO6 <ref>") ·
               Confidence · Decided by · Run (only when rows differ), a ledger table's last column
               is sticky-right so a decision button never hides behind a
               horizontal scroll, the Run column shows only when the rows
               differ on it, and `FramePreset.clamp` (bank `narrative`)
               clamps a long free-text cell to two lines with the full
               value on hover — one narrative used to make a row 8 lines
               tall. Big tables draw
               progressively (ui.tsx useProgressiveRows + MoreRows: 150
               rows, then the next batch as the table's foot scrolls into
               view; sort/filter/counts still see every row) — DataTable,
               AR, both Analyst queue tabs and the Audit feed use it; a
               deep link to row N calls reveal(N) first (Analyst focusId);
               IA: "Operate" group — Command Center (default landing;
               redesigned 2026-09-17 as a 7/5 board where EVERY FIGURE
               APPEARS ONCE — header (customer · data-through date; date
               filter, customer picker only when >1, refresh, Ingest /
               Reconcile), then: Reconciliation health (rate ring + "N
               settled of M recognised" + auto/accepted/manual, and IREPS
               credits received with a bar partitioning them into Settled /
               In review / Unmatched / Awaiting data — legend shows shares,
               counts on hover — plus other receipts, credits in window and
               the Gold pool in its foot) | Needs attention (Matches to
               review, Open exceptions "N credits · M bills" + resolved,
               Awaiting data status/bill-data — stretched-button rows) |
               Largest open exceptions (Age from data_as_of) | Recent
               activity (components/RecentActivity.tsx: the human-meaningful
               slice of GET /api/audit — ingestions, runs enriched from
               ledger.finalized, match decisions, config; bronze/silver/
               conflict rows are left to the Audit trail; absolutely
               positioned so it never stretches the row). The KPI tiles,
               partition strip, Match performance funnel table, donut and
               Pipeline panel are gone. All figures from GET /api/overview;
               every figure, legend item and row opens where it lives,
               ALREADY FILTERED to it — a
               LedgerIntent (Analyst queue: status/type/gap/IREPS-scope
               filters + the page's date window as from/to, always sent
               through CommandCenter.openQueue) or a GoldIntent (Data pages,
               Current scope, e.g. credit_scope, plus the window — always
               through openGold; Gold pool opens Bills this way). Every
               preset lands as a
               visible, removable FilterChip — a narrowing the analyst
               cannot see or clear reads as a broken page. The IREPS bar is
               ONE credit partition: IREPS credits = Settled + In review
               (credits, not matches) + Unmatched + Awaiting data (Open
               exceptions' bills are named beside its credits, never summed)
               — guarded server-side in tests/test_awaiting_status.py.
               Largest open exceptions is the work needing an analyst
               (open_in_scope: no other receipts, no credits awaiting
               data, bills included);
               DateFilter — quick picks
               Today / Yesterday / This month (default) / All / custom +
               operating-unit chips incl. "Unassigned", persisted per
               customer in localStorage, sent as /overview query params;
               the "last run · last ingest" strap was removed on request)
               -> Ingest documents (IngestForm; page heading and nav label
               both say "Ingest documents": numbered cards — 1 Bank
               statement, 2 ERP documents, 3 Additional lineage documents
               — with per-slot include toggles, the File-format / ERP
               dropdowns (PUT /sources) in each card head, and dashed drop
               zones listing attached files; a slot shows your upload or
               nothing — there is no default-document prefill, and only
               the files of ticked slots are posted. A sticky "Ready to
               ingest" panel lists what will be posted per slot beside the
               primary "Ingest N files" button; the result is its own card
               with a "Reconcile now" next step. Extra lineage_* slots: add
               ("Add lineage source"), remove (null adapter), upload under
               the slot key. "New customer" and "All ingestions" are head
               buttons — IngestionsView renders as a card) -> Reconcile
               (nav label stays "Reconcile"; ReconcileForm's page heading
               is "Initiate Reconciliation": a Statements card (select
               all, name, credit period, credits) and a Mode card
               (Incremental marked recommended) beside a sticky Run
               summary with the primary button; its "Matching rules" row
               links to Settings;
               MatchingConfigPanel lives on the SETTINGS page (Platform ›
               Settings, SettingsView: tabs Matching config | Source setup
               — a read-out of each slot's format, changed on Ingest; moved
               off Reconcile 2026-09-23) (sticky save bar naming
               unsaved changes) — edits the customer's full rule set incl.
               field_map, the new scalar knobs and the "Terminology &
               guidance" copy editors (edits accumulate in copy_overrides;
               the server stores only diffs from defaults) via GET/PUT
               /config, dropdowns fed by /api/gold/schema; there is NO
               per-run tunables panel — the UI sends no tunables so the
               saved config governs) -> results. The "Reconciliation
               result" nav items and the Data pages' run scope are NEVER
               disabled: opened with nothing loaded, App auto-loads the
               customer's latest succeeded run (one attempt per run id,
               never while a hash-named selection is still restoring), and
               the header's RunPicker (mode + run-date range + checkbox
               list, at least one run always selected) switches runs; with
               no runs at all the empty state guides to Ingest/Reconcile
               (on a Data page the head with its scope switch still renders
               above that card, so "Current" is one click away).
               Every page heading is PageHeader's `<h2 className="page-title">`
               (--fs-h1 token); card headings are --fs-h4. The run
               Summary opens on clickable figures (bank credits, matched
               + share of matchable, credits with no bill, bills with no
               credit, matches to review — each opens its run table), then
               the Breakdown table beside Parse checks / Ledger changes /
               Data used cards. The Data pages' scope switch is a `ui-seg`
               in the head; DataTable has a tool bar (page filters, search,
               row count, Columns menu), a sticky header and real empty
               states that clear whatever emptied them.
               The run-scoped Exception queue KEEPS its MATCH_REVIEW rows
               and decides them in place: components/MatchDecision.tsx
               (accept incl. pick-list / reject / unlock / reopen +
               useMatchDecision) is shared with the Analyst queue, both
               hitting the same /api/matches/{id}/* routes on the same
               match_ledger row; the frozen frame rows are overlaid with
               the LIVE ledger row (App fetches /api/ledger, keyed by
               match_ledger_id) into a "Ledger status" spine column, and
               any decision bumps ledgerEpoch so every ledger-reading view
               refetches — that is how a decision in one page is the state
               of the other. Rows without match_ledger_id (snapshot runs)
               keep the "no durable match to decide" note. OPEN BANK_ONLY /
               BILL_ONLY rows offer "Match to bill… / Match to credit…"
               (ManualMatchPicker: open exceptions of the other side, sorted
               by |amount difference|, multi-select for batches, running
               variance, note optional with a WARNING — not a block — when
               |variance| > amount_tolerance) -> POST /api/matches/manual.
               "Workspace": Analyst queue (LedgerView renamed in UI
               ONLY — /api/ledger and DB names unchanged; opens on four
               quick views — To review / Open exceptions (needs action) /
               Awaiting data / Settled / Non-IREPS — each an ordinary LedgerIntent
               applied through the same applyIntent as a Command Center
               arrival, so it lands as chips; the figures count within the
               Command Center's date window, so they equal the Command
               Center's. That window is the queue's DEFAULT on every
               visit, whether arriving by a link or the sidebar: LedgerView
               reads the filter the Command Center saved for the customer
               (DateFilter.loadSavedDateFilter, localStorage
               recon.cc.filter.<customer>) and applies it to every tab as
               the removable Credit date / Date chip; a link's intent
               overrides it, and clearing the chip lasts for that visit; then ONE card with Matches |
               To review | Exceptions | Awaiting data | Non-IREPS receipts
               tabs (intent.section picks the tab; with none, Matches —
               the first tab). To review = OPEN matches, Matches
               = the decided ones (LOCKED / REJECTED): a split by status,
               sharing one match filter set; a tab switched by hand drops
               the Status filter, and a focused match opens the tab its
               status puts it in. The three exception tabs
               are LedgerView.excBucket's partition — Exceptions = IREPS
               work (every BILL_ONLY + unmatched IREPS credits, equal to
               open_in_scope), Awaiting data = AWAITING_*, Non-IREPS =
               UNRECOGNISED_RECEIPT or an approval; an analyst's IREPS
               decision always wins. They share one filter set; a tab
               switched by hand drops the gap filter. Non-IREPS rows offer
               Approve / Reject (Undo after approve; a rejected row shows
               "Marked as IREPS · Undo" in its detail) and never offer a
               manual match, nor appear as picker candidates; hosts the same
               MatchDecision + ManualMatchPicker. DECISIONS (2026-09-23):
               Accept / Reject / Approve stay ONE click; a "+ note" link
               beside them opens ui.tsx DecisionDialog (the app's only
               modal, portalled to <body> — its hosts are sticky cells)
               with the same actions plus an optional note. Unlock and
               Reopen ALWAYS confirm through it ("goes back to To review").
               Under a match's status: who decided + when (DecidedBy;
               "System" for an AUTO_HIGH lock), a note icon, the note in
               the expanded row; "Resolved by" names the user. Ambiguous
               candidates render SIDE BY SIDE (components/CandidateCompare:
               a column per bill, the credit as a reference column, fields
               where the bills DIFFER listed first, highlighted and named
               in a "Differs on" line, identical fields folded, ✓ where a
               bill agrees with the credit, "closest" on the date gap) —
               used by ReviewEvidence and PickList, so the Exception queue
               shows it too. Awaiting data has a "Bill status" column: the
               CURRENT status + number of the same-amount bill behind an
               AWAITING_STATUS reading (db/overview.gap_bills, served as
               gap_bills on /api/ledger; same predicate as
               same_amount_bill_clause), "No bill yet" for
               AWAITING_BILL_DATA. The queue also shows MANUAL matches as
               "Matched by user" with their note, exceptions carry a
               "Resolved by" column — the Exceptions table uses the Command
               Center's "Largest open exceptions" spine, one column each
               (Type · Status · Ref · Zone · Date · Amount · Gap · First
               seen · Resolved by), rows expand to the gap_action advice +
               narrative, and its filter row mirrors Matches (chips + "N of
               M"); the Non-IREPS tab shows the credit's
               Narrative where the other tabs show Zone (a non-IREPS
               receipt has no zone by definition); exception filters are Status / Type / Gap (gapOf — keep
               it in step with db/overview.unrecognised_clause) / Date
               (the old Scope "Needs action" filter is gone — the tabs are
               that split) — and "⬇ Export ledger" downloads
               /api/ledger/workbook; a Run column +
               a RunFilter (components/RunFilter.tsx: mode + run-date range
               + ticked runs, EMPTY = every run, the opposite of RunPicker's
               "load these") narrows matches by run_id and exceptions by
               first_seen OR resolved_by run; the old RunsView "Runs"/"Run
               history" inset was REMOVED from this view and the component
               deleted — it duplicated the RunFilter's list and its only
               action, "Open", navigated away to the run's Summary, which
               read as a broken filter) + AR
               Reconciliation (ARReconciliationView over GET /api/ar —
               db/overview.py ar_view: the bill-centric AR working set
               (settled / in-review from the match ledger, outstanding =
               open BILL_ONLY aged from payment_advice/order/submission
               date, OVERDUE > 30d): a Receivables position card
               (Outstanding / Overdue / Received — each opens its bills —
               and one bills-by-status partition), an Aging card whose
               buckets filter the table (outstanding rows only), and the
               bills table with status tabs; NO match rate here since
               2026-09-21 — it lives on the Command Center only, where its
               definition is; every row carries
               run_id (match's run / exception's first-seen run) so the same
               RunFilter narrows the table AND (since 2026-09-07, on
               request) every figure and the aging buckets,
               recomputed client-side over the in-scope rows; the same
               DateFilter (default All) applies to the aging anchor date
               of outstanding rows and the credit value_date of settled
               rows; settled rows cross-link into the Analyst queue
               focus; recon-alpha's milestone tracker deliberately omitted —
               not an IREPS concept) + Audit trail
               (AuditTrailView over GET /api/audit — the real audit_log
               stream with client-side category/actor/window filters and a
               by-record timeline; its stat strip's figures set those
               filters, and events show a readable name — EVENT_NAME,
               extend it with the taxonomy below — over the raw code; a
               User column (served `actor`; System / "Not recorded" for
               older human events) with its own filter; the Record column
               speaks business terms — "Match M-42", "Credit · <ref>",
               "Bill · <no>", "File · <name>", "Matching config", "Source
               setup" (ENTITY_NAME + db/overview._audit_entity_context),
               never a table name; details render as labelled pairs and a
               config change as "Field: old → new"). HEADER TOOLTIPS:
               columnHelp.ts COLUMN_HELP (data keys + `ui:` keys) feeds
               DataTable headers and ui.tsx HelpLabel (Analyst queue, AR,
               Audit, comparison) — a described header has a dotted
               underline. AR's settled rows carry "Decided by". "Platform": Settings, Architecture
               (ArchitectureView — the real six-layer stack described in
               frontend/src/architecture.ts with live KPIs from /api/
               overview; keep its statements factually in sync with this
               file when the architecture changes). There is NO silver tab
               (a "Parsed source rows" view existed briefly and was removed —
               silver is a write-only audit layer, see db/silver.py). ONE
               "Data" sidebar group (since 2026-09-07; replaced the twin
               "Run data" + "Gold data" groups): four pages — Bank
               Transactions / Bills / Recoveries / Lineage docs — each with
               a scope switch in its head. "Current" = GoldTable (shared
               presets in framePresets.ts, refetch-on-mount — no cache,
               gold mutates on ingest) browsing the live gold layer with a
               date window on Bank (value_date) and Bills (submission_date,
               else bill_date) — GoldTable.DATE_FIELD, the SAME dates
               db/overview counts those figures with, so a Command Center
               link carrying from/to lands on exactly its rows — and a
               per-ingestion filter (per-file rows come from the file's
               gold.file_rows sightings, so a re-export of known bills
               still filters to its own rows); "As of run" = SourceTable
               (cached — frames are immutable per run) over the frozen run
               frame, with the RunPicker. The pairing lives in
               src/dataPages.ts (DATA_PAGES) over the UNCHANGED internal
               view keys — gold_* IS the Current scope, the run frame name
               IS the run scope — so the hash format, VALID_VIEWS, the
               auto-load gate and every legacy link are untouched.
               bills_enriched is the "With lineage trail" toggle inside
               Bills in run scope (a run artifact, no gold counterpart);
               Lineage docs is Current-only (a run persists no lineage
               frame — routes._frame_records writes exactly four). The
               scope a sidebar click opens is the user's last choice
               (localStorage recon.dataScope); the ACTIVE scope is always
               derived from the view (scopeOf). Row badges show in run
               scope only. UploadForm/runRecon are deleted;
               unchanged by the medallion refactor — same API contract, frame names
               (bank/bills/bills_enriched/recoveries), column names, summary
               Category strings. Do NOT rename any of those server-side silently.
```

## Domain logic that isn't obvious from any single file

- **The golden master is the refactor gate.** `tests/test_golden.py` diffs the engine's output on the sample documents byte-for-byte against committed CSVs. Any engine/parser/adapter change must keep it green, or regenerate the snapshots explicitly and say why. (Regenerated once on 2026-08-31 for the lineage canonicalization: `bills_enriched`/`queue` dropped the raw RN_*/CR_* columns, `matched`/`queue`/`bills_enriched` gained `Invoice_Date`/`Bill_Reg_Date`, and CRN integer quantities lost a spurious float artifact — verified as a pure drop/add with `scripts/verify_lineage_canonicalization.py`.)
- **Both sides are sources of truth**, so exceptions run in both directions: `BANK_ONLY` (credit with no bill) and `BILL_ONLY` (advised bill with no credit). The queue deliberately mixes both; bank rows lack bill fields and vice versa by design.
- **A read-time gap code can change the WORDING without changing the bucket.** `db/overview.gap_details` hands each OPEN BANK_ONLY row a reading: `AWAITING_STATUS` / `AWAITING_BILL_DATA` (excused from the rate), `MARKED_IREPS` (an analyst's reject) and, since 2026-09-23, `BILL_RETURNED` — a bill of that amount exists but the source RETURNED it. RETURNED is deliberately NOT an in-flight status (money against a rejected bill IS surprising), so such a credit stays RECOGNISED, in the rate and in the Open exceptions tile; only its sentence changes, because "no bill in the export" sent analysts looking for a bill that was sitting there returned. `NOT_A_SCOPE` (MARKED_IREPS, BILL_RETURNED) is what keeps `credit_scopes` from treating a wording as a funnel bucket; `same_amount_bill_clause(statuses)` is shared by the awaiting and returned readings.
- **Every credit is counted exactly once in the Command Center funnel.** Other receipts (`UNRECOGNISED_RECEIPT` — no match signal: interest, sweeps, non-IREPS payers) can never match a bill, so they sit outside the match rate AND outside the Open exceptions tile / Largest open exceptions; they appear once, as "Other receipts". Credits awaiting source status or bill data are IREPS money that could not have matched *yet* — excused from the rate AND left out of the Open exceptions tile / Largest open exceptions (named there as "awaiting data" instead; they stay OPEN rows in the ledger, and re-enter the tile on their own once the awaiting-bill-data cap expires). Received's IREPS credits = settled + in review + unmatched + awaiting data, and Open exceptions = unmatched + bill-only. Every figure's drill-down must select the same rows the server counted: `db/overview` clauses, `gap_details`/`credit_scopes` and the frontend's `gapOf` are one definition, so change them together.
- **Non-IREPS receipts are kept apart from matching.** A credit with no value in the first exact signal's bank field (no zone, under the default mapping) is a non-IREPS receipt: an incremental run never scores it, even when a bill has its amount — it goes straight to the ledger as an OPEN `UNRECOGNISED_RECEIPT`. (Before 2026-09-22 such a credit could still pair on amount alone; the dev ledger had never done so.) The Analyst queue's Non-IREPS tab decides each: **approve** = `credit_sources` NON_IREPS + the exception RESOLVED as `USER_NON_IREPS` (still counted in "Other receipts" via `db/overview.non_ireps_clause`, never as "resolved", never pooled again); **reject** = `credit_sources` IREPS — the row stays OPEN, reads as the read-time gap `MARKED_IREPS`, counts in the rate and Open exceptions, and later runs DO offer it to the matcher; **undo** drops the decision. `unrecognised_clause` applies the decision first, so every figure, `gap_details`, `credit_scopes`/`credit_sources` and the frontend's `excBucket` move together. Snapshot runs are untouched (legacy semantics, golden-gated).
- **Snapshot vs incremental runs.** Snapshot (default) reconciles one statement against one export, results per run. Incremental accumulates: gold rows are **ingestion-owned** (written once per file, entity-upserted across files), the pool carries open exceptions forward, and matches are durable in `match_ledger` — HIGH confidence auto-LOCKs, review confidences stay OPEN until a user accepts/rejects. A LOCKED match's credit and bills never re-enter any pool; rejecting releases both sides. Incremental MATCHING is wide open (any unconsumed bill, however old), but which bills count as EXPECTED (BILL_ONLY) is floored since 2026-09-22 at `incremental.coverage_start` — the earliest credit value date the ledger has handled (a match or exception) or this run's pool; a statement ingested but never reconciled does not count. Before that, the wide-open window reported every advised bill since the first export as unpaid (1,836 of 1,839 open BILL_ONLY on the 18–21 Aug load were due before the first loaded credit, back to May). `reconcile(expected_from=...)` carries the floor (None = unchanged for snapshot/CLI/golden); the run payload echoes it as `meta.expected_from`, and the Summary relabels an incremental run's "Bank credits in statement" / "Bills expected in window" rows at DISPLAY time only (SummaryDashboard.displayCategory — the stored Category strings stay frozen). Rows persist as OPEN exceptions, not duplicated per run; a floor that moves later (older statements reconciled) makes their bills expected then.
- **A gold row is an ENTITY, so "owns" ≠ "reported".** The entity upsert keeps one row per bill/credit/doc, stamped with the bronze file that FIRST inserted it. A later export that re-reports 34 known bills therefore inserts nothing and owns nothing — before `gold.file_rows` it vanished from the UI entirely ("I ingested a file and the Bills page still shows the old data"), and a bank statement whose credits had all arrived on an earlier one could not even be picked to reconcile. Ingest now records one sighting per row carried; anything that means "this upload's rows" (gold browse filter, `gold_files` counts, statement picker, snapshot/incremental bank pools) goes through `db/gold.py reported_by_file`, which falls back to plain ownership for files ingested before the table existed. Recovery LINES are the one deliberate exception: they ride with a NEW bill only, so a re-export contributes no recovery rows and its recoveries tab is legitimately empty.
- **Amount is a filter, not a signal.** Pairs are only scored if amounts already agree (indexed on `round(net_payable_amount, 2)`); zone and date break ties. Confidence labels are derived from the raw signals, not by reversing the score. Signal weights are per-customer config, but labels stay signal-derived.
- **A credit whose best pairing was claimed by another credit is not allowed to settle for a worse one** — it falls to the exception queue. A missing match you can investigate beats a wrong match you cannot see.
- **The Bills + lineage tab serves the grouped frame** (`engine.group_bill_attempts`): one row per bill, RETURNED resubmissions combined (representative = settled attempt if any, else latest by submission_date asc / data_row desc), full journey in the `Attempts` list column. Matching and the raw Bills tab always use ungrouped frames.
- **Settlement is stamped onto bills_enriched** in `reconcile()` (`SettledInStatement` + `Settled_*` columns, `match_id`/`Settled_MatchId` = `m{n}`); the display gate to HIGH-only lives in the frontend (`SourceTable` injects the `Settled='SETTLED'` token).
- **Weak matches are copied into the queue as `MATCH_REVIEW`** (`AMBIGUOUS`/`LOW`/`AMOUNT_ONLY`/`BATCHED`). They stay in the matched frame — bills remain claimed — with structured `Candidates` (API) and flat `CandidateSummary` (Excel).
- **`window_days` (default 0)** bounds which bills are "expected" in a snapshot statement; money cannot arrive before IREPS advises the bank. Incremental mode ignores it (the open pool replaces the window, floored at coverage_start for expected bills — see above).
- **`paid_statuses`** default `{"PAYMENT MADE"}` only. CO7 DONE was in it until 2026-09-22 and was REMOVED on request — a payment order does not guarantee payment, so a CO7 DONE bill can never be matched (migration a7c3e91d5b24 stripped it from every stored `match_rule_sets` row). A credit whose only same-amount bill is CO7 DONE is read as `AWAITING_STATUS` (db/overview `IN_FLIGHT_STATUSES` = PASSED, REGISTERED, CO7 DONE), not as a matching failure. Separate and UNCHANGED: `FieldMapping.fallback_due_statuses` still makes a CO7 DONE bill with a payment order but no advice an EXPECTED bill (`PAYMENT_ORDER_NO_ADVICE`, "monitor only") — that reports it, it never lets it settle. The golden snapshots predate this and must be regenerated once sample documents are available. Now a real config: `MatchRuleSet.paid_statuses`, per-customer row in `match_rule_sets`, threaded through `reconcile()` to the matcher.
- **Matching fields are per-customer config** (`FieldMapping` in `rules.py`, stored as JSON in `match_rule_sets.field_map`, edited in the UI's Matching config view). Only *signals* are configurable — display columns, gap_type literals, and the legacy-named MatchResult columns (`zone_from_narrative`/`bill_zone`/`zone_check` carry the FIRST exact signal's values; `date_source` stays `"advice"`/`"co7"` meaning primary/fallback) are gold-canonical and hardcoded. Two traps: (1) `engine.reconcile()` is called directly by `db/reconcile_gold.py` and `db/incremental.py` — the golden gate does NOT cover those two call sites, so any new rule knob must be threaded there by hand (`meta.rules_effective` is the guard); (2) with zero exact signals `all([])` is vacuously True — `exact_ok = bool(mapping.exact_signals) and all(checks)` guards it (regression-tested in `tests/test_field_mapping.py`). A NULL/`{}` `field_map` row means defaults; the default mapping is byte-identical to the historical hardcoded behavior under the golden gate.
- **Advisory copy is per-customer config, codes are not.** `MatchRuleSet.copy_overrides` swaps the human text mapped onto `gap_type`/`ExpectedBasis`/review-confidence codes (`engine.DEFAULT_COPY` + `resolve_copy`); the codes themselves (`NON_IREPS_OR_UNRECOGNISED`, `CO7_ISSUED_NO_ADVICE`, `"advice"`/`"co7"`, `LineageStatus` values, MatchedVia literals, the confidence labels incl. `MANUAL` — a user-made match, LOCKED from birth, deliberately NOT in `REVIEW_CONFIDENCE`) are FROZEN — they live in golden CSVs, persisted payloads, ledger rows and the frontend. Never rename a code; change its display text (or add a `labels` entry) instead.
- **Display paths read canonical ROLES, not the field_map — by design.** `exception_queue` (`amount`=net_payable_amount, `value_date`=payment_advice_date), `summarise`'s bank-side `amount`, and the AR view's advice→order→submission aging chain are deliberately hardcoded to the canonical columns. A custom `field_map` may only point at columns that still MEAN the canonical role; a new source's adapter must map onto the roles, not around them.
- **IREPS data is dirty in specific ways** the code already handles — don't regress: four spellings of "nothing here" (`None`, `nan`, `"nan"`, `"----"`) via `scoring.norm_text`; int/str ID drift via `lineage._key`; quantities like `"3 Set"` (why `gold_lineage_docs.receipt_qty` is a String); recovery amounts packed several to a cell (summed); Net Amt rounded to whole rupees (₹1 slack).
- **Bill Status is a plain table** (one header row, one row per bill — see `parsers/bill_status.py`), columns located by header TEXT never by position, and the set of columns is assumed to keep changing: only `REQUIRED_HEADERS` (Bill Number, CO6 No, Status, Net Amt) are load-bearing, a dropped known column NA-fills, and an ADDED column is extracted under its own header text and rides to gold `extras` untouched — nothing here knows what a new IREPS column means, and guessing its dtype would be an invention. Silver stays source-native and single-cell: `RecoveryDetails` is the raw `'<head>: <amt> <head>: <amt> ...'` string IREPS writes, never split in the parser. The IREPS bills adapter's `to_gold()` (`sources/ireps_bills.py`) is where that string becomes the structured `recoveries`/`recovery_count`/`recovery_sum`/`net_check`/`recovery_check` gold fields and the long-format `recoveries` gold frame — Silver→Gold derivation belongs in the adapter, not the parser. `----` is IREPS's "CO7 not issued yet" placeholder on `CO7No`/`CO7Date` only; other placeholder tokens (`NA`, `-`, blank) on date columns are absorbed by `pd.to_datetime(errors="coerce")` rather than an enumerated list. Identifier columns (`ContractNo`, `BillNumber`, `CO6No`, `CO7No`) are never coerced to numeric — a bill number is not an amount, and some rows legitimately carry non-numeric placeholders (`BillNumber="-"`) that downstream code already treats as "no bill number" (`engine.group_bill_attempts.UNGROUPABLE_KEYS`, `db/ingest._BLANK_KEYS`) without the parser needing to null them. `Sheet`/`DataRow` are Silver bookkeeping fields (not literal IREPS columns) — `data_row` in particular is a real dependency of `group_bill_attempts`' resubmission tie-break, not decoration. `header_row`/`unparsed_header` (artifacts of a pre-2026-08 block-per-bill export this parser no longer reads) come back NA via `ensure_schema` — nothing feeds them any more. The IREPS bills adapter's `selfcheck()` raises when a workbook yields ZERO bill rows — an upload that isn't this format fails loud instead of ingesting an empty layer.
- **Zone extraction** tests longer railway zone codes first so `NER` isn't read as `ER` (`bank_hsbc.ZONE_CODES` order matters).
- **Bank self-check is fail-loud**: the HSBC adapter's `selfcheck` ties parsed credits to the totals printed on the statement's last page and raises `SelfCheckError` on mismatch; everything downstream depends on that parse.
- **Money is Float end-to-end** (golden parity). Moving to Numeric/Decimal is a deliberate future migration that requires re-baselining the golden master — do not change it casually.
- **Authentication is a gate, not tenant isolation.** Signing in proves WHO you
  are; it does not narrow WHAT you can see. Every signed-in user still reaches
  every customer, because `customer_id` is still a request field — the `users`
  table deliberately has no `customer_id`. Taking the tenant from the session
  instead is the follow-up, and it touches all 28 routes.
- **Known deferrals (by decision, not oversight)**: no tenant isolation yet
  (authentication landed on feat/auth; customer_id is still a request field), runs execute synchronously in a request threadpool (no job queue), single-worker uvicorn assumed, run-file retention keeps last 20 workbooks (DB rows kept forever).
