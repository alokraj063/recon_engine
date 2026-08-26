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
cd backend && ../.venv/bin/python -m pytest tests/ -q
cd backend && ../.venv/bin/python scripts/make_golden.py   # regenerate snapshots ONLY on intended behaviour change

# Database
# DATABASE_URL env var switches the store (default sqlite:///backend/data/app.db;
# point at RDS Postgres for production — schema auto-initializes on startup).
# One URL either way: locally that's 4 ATTACHed sqlite files (app/bronze/silver/gold.db,
# see db/base.py), on Postgres it's 4 real schemas inside the one database.
cd backend && ../.venv/bin/python -m alembic revision --autogenerate -m "..."   # after model changes; REVIEW the output
cd backend && ../.venv/bin/python -m alembic upgrade head

cd frontend && npx tsc -b           # typecheck
cd frontend && npm run build        # production build
```

The sample documents folder contains real input files (gitignored, as are the golden CSVs derived from them; filenames contain spaces; `~$...` files are Excel lock files — ignore them). `backend/tests/golden/*.csv` must exist locally for the tests — run `scripts/make_golden.py` once after cloning.

## Architecture

Layered, with one-way dependencies — parsers know nothing about matching, matching knows nothing about the engine, the engine knows nothing about Excel or the database, `db/` imports `recon` but never the reverse, and `backend/app/` wires it all to HTTP. Nothing imports upward. Keep it that way when adding code.

```
backend/recon/   the engine package (pure, DB-free; runs with backend/ as cwd)
  config.py    ReconConfig — CLI-facing config; builds a MatchRuleSet internally
  rules.py     MatchRuleSet — the "delta layer": tolerances, paid statuses, signal
               weights, and FieldMapping (which GOLD fields drive matching: amount
               pair, date primary/fallback pair, exact-signal pairs with weights,
               eligibility field + fallback-due statuses). Resolution: dataclass
               defaults <- customer DB rule set <- API tunables IF the caller
               sends them (explicit values win; the web UI deliberately sends
               NONE — /api/reconcile tunables are Optional and the saved
               matching config is the single source of truth for UI runs;
               field_map is customer-level ONLY, never a per-run override)
  parsers/     source document -> plain DataFrame (bank_hsbc, bill_status, lineage).
               Parser output keeps SOURCE-NATIVE names (IREPS PascalCase) — that is
               the silver vocabulary, persisted as-is to silver.records.
  sources/     adapter layer: (source_type, adapter_key) registry. Each adapter:
               parse() -> silver, to_gold() -> gold frames (+row_seq, ensure_schema),
               optional selfcheck() raising SelfCheckError (fail-loud control totals).
               to_gold() owns the silver->canonical rename map (ireps_bills.py
               BILLS_TO_GOLD/RECOVERIES_TO_GOLD) — THE seam a new bank/ERP source
               plugs into: one ~40-line adapter + its map onto the same canonical
               columns + a source_configs row. tests/test_adapters.py guards
               map<->schema drift (ensure_schema would otherwise silently add
               all-NA columns while unrenamed ones leak into extras).
  gold/        schemas.py — ONE canonical source-agnostic snake_case schema per
               input kind, used END-TO-END: engine frames, DB columns, API
               payloads, frontend, workbook. IREPS's CO6 -> submission_ref/date
               (also the bill entity-upsert identity), CO7 -> payment_order_ref/
               date (the fallback due-date signal), amounts are gross/approved/
               deduction/net_payable_amount. The docstring lists reserved future
               columns (tds/gst/utr/...) and the scope line: engine-DERIVED
               run-artifact names (TRAIL, LineageStatus, ExpectedBasis,
               Settled_*, Attempts, Candidates, gap_type, RN_*/CR_* internals)
               are NOT gold schema and keep their names.
  matching/    scoring.py (per-pair signals, weights overridable), matcher.py
               (three-pass loop — score all pairs, assign best-first, subset-sum batch)
  engine.py    reconcile() (two-sided exceptions), exception_queue(),
               run(cfg) = thin wrapper over pipeline with the HSBC/IREPS adapters
  pipeline.py  run_pipeline(inputs, adapters, params, rules, sinks): adapters ->
               gold -> engine, gold flows in memory, persistence via optional sinks
  report.py    write_workbook — formatting only, decides nothing
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
  models.py    all tables, snake_case, customer_id everywhere: customers,
               source_configs, match_rule_sets, bronze.files, silver.records,
               gold.bank_txns/bills/recoveries/lineage_docs, runs, run_frames,
               run_match_bills, match_ledger(+bills), exception_ledger, ingest_conflicts,
               audit_log (general-purpose event stream, app schema — NOT a
               replacement for match_ledger/exception_ledger/ingest_conflicts,
               which stay the detailed domain-specific trails for their own concerns)
  storage.py   LocalStorage (data/bronze/ content-addressed by sha256, data/runs/) —
               S3-swappable seam. Note: data/bronze/ (blob dir) and data/bronze.db
               (bronze-schema DB file) are unrelated, don't confuse them.
  audit.py     record_event(session, logger, ...) — the ONLY place a log line and
               its audit_log row get written; never commits, rides the caller's
               existing transaction, so a rollback erases both together
  bronze.py    register_file — dedup by (customer, sha256)
  silver.py    one JSON row per parsed source row, deduped per bronze file
  gold.py      persist + frame_from_gold (rebuild engine frames for the pool).
               Since the gold canonicalization the frame<->DB maps are IDENTITY
               (kept as dicts: they decide which columns are typed DB columns vs
               extras JSON); only RNOTE_MAP/CRN_MAP still rename (engine-internal
               RN_*/CR_* -> unified lineage_docs). FRAME_DATE_COLS must track any
               column rename in lockstep or rebuilt dates silently come back as
               datetime.date, breaking matcher arithmetic.
  ingest.py    idempotent gold ingestion: file-level dedup + entity-level upsert
               (bills by (bill_number, co6_no) — daily IREPS exports are different
               FILES with the same BILLS); LOCKED bills never mutate -> ingest_conflicts
  incremental.py  Phase-6 runs: pool = new credits + open exceptions vs all
               unconsumed bills; UNCHANGED matcher; match_ledger (HIGH auto-LOCKs),
               exception lifecycle OPEN -> RESOLVED; one running run per customer
               (partial unique index -> 409 RUN_IN_PROGRESS)
  runs_store.py  persisted runs (payload/frames/workbook survive restarts)
  seeds.py     idempotent default customer wired to hsbc/ireps adapters
backend/alembic/ migrations; env.py reads DATABASE_URL, render_as_batch=True.
               ALWAYS review autogenerate output (it has produced bad imports, wrong
               column types, and — against SQLite specifically — spurious "add
               missing FK" diffs for every cross-schema edge; that last one is
               expected noise from SQLite's ATTACH limitation, strip it by hand,
               don't apply it). fileConfig(..., disable_existing_loggers=False) —
               the default (True) permanently disables every logger not listed in
               alembic.ini, including every db.*/app.* logger (Logger objects are
               cached singletons; once disabled, disabled forever in that process).
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
               get_statement_bronze) and LINEAGE_VIEW_MAP (canonical unified
               lineage browse — RN_*/CR_* shapes stay engine-internal)
backend/app/     FastAPI wrapper — TWO-STEP flow in the UI: (1) POST /api/ingest
               (multipart, each slot optional, ≥1 required; bronze -> silver ->
               gold standalone; writes one ingestion.completed audit event in
               the same transaction as the gold writes; NO Run row on failure;
               `slots` form field = comma list of slots to process — enabled set
               is slots ∪ fields-with-uploads, unknown slot -> 400, empty -> 400,
               and the repo-sample fallback runs ONLY for enabled slots AND
               only for the seeded `default` customer — real tenants must
               upload; an empty form no longer silently ingests all four
               samples — the frontend always sends slots explicitly),
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
               (whole-frame with 20k cap, {count,total} exposes truncation).
  routes.py    legacy POST /api/runs (multipart one-shot) KEPT for compat/tests
               but retired from the UI; GET /api/runs/{id}[/frames/{name}|
               /workbook], GET /api/customers, GET /api/runs, GET /api/ledger,
               POST /api/matches/{id}/accept|reject; errors map to
               BANK_SELFCHECK_FAILED (422) / PARSE_FAILED (422) / INVALID_INPUT (400)
               / RUN_IN_PROGRESS (409); sample-file fallback via GET /api/defaults
               (default customer only — ?customer_id= anything else returns all
               nulls, and /api/runs 400s instead of substituting samples); run
               payloads' meta.selfcheck comes from each adapter's selfcheck()
               captured via the pipeline's on_selfcheck sink (never re-checked
               in routes); upload extension checks derive from adapter
               file_kinds (union per source_type over the registry — no
               hardcoded ALLOWED list; empty file_kinds = unrestricted).
               Configuration endpoints: GET /api/adapters (registry with
               labels, system, file_kinds — the UI's accept attrs follow the
               selected adapter's file_kinds),
               GET /api/gold/schema (field/date/numeric lists feeding the config
               UI dropdowns — declared BEFORE /api/gold/{frame}, route order
               matters), GET/PUT /api/customers/{key}/config (effective merged
               rules incl. field_map; PUT validates fields against GOLD_COLUMNS
               -> 400, normalizes via FieldMapping.from_dict, audits
               config.rules_updated), PUT /api/customers/{key}/sources (adapter
               per slot; params kept when adapter unchanged, reset when changed;
               audits config.sources_updated), POST /api/customers (key
               ^[a-z0-9_-]{1,64}$, 409 CUSTOMER_EXISTS, clones default sources +
               rule set, audits customer.created). Every run payload echoes
               meta.rules_effective (field_map/paid_statuses/weights) — the E2E
               proof a run used the customer's mapping.
               Shared helpers (_load_customer_context/_register_inputs/
               _build_adapters/_effective_rules/_persist_side_effects) are used
               by BOTH the legacy and two-step paths — behavior changes there
               affect both.
  main.py      configure_logging() at import; @app.middleware("http") logs one
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
to change a LOCKED bill), `run.started`/`run.start_conflict`/`run.succeeded`/
`run.failed`/`run.selfcheck_failed`/`run.parse_failed`, `ledger.finalized`
(summary, not per-match), `ledger.match_accepted`/`ledger.match_rejected`/
`ledger.match_unlocked` (LOCKED -> OPEN undo; details carry was_locked_by),
`http.request`, `http.unhandled_exception`, `pipeline.selfcheck`. High-volume
operations log one aggregate summary (the same `stats` dict already returned
to API callers), never one line per row/match. No PII/financial content in
log lines or `details` — counts, ids, field *names* only (e.g. `gold.ingest_conflict`
logs `changed_field_names`, never the before/after values, which stay in the
existing `ingest_conflicts.changed_fields` column; `pipeline.selfcheck` omits
HSBC's `stated_total`/`parsed_total`, logging only `passed`/counts).
backend/scripts/make_golden.py + backend/tests/  golden-master gate (byte-exact CSV
               diff of summary/matched/queue/bills_enriched/bank/recoveries on the
               sample docs) + the incremental scenario test
frontend/        Vite + React + TS; @tanstack/react-table v8 (keep the ^8 pin)
               + lucide-react (nav/button icons — professional stroke set,
               tree-shaken per import; the only other runtime dep);
               IA: "Operate" group — Command Center (default landing; real
               KPIs/donut/pipeline from GET /api/overview, top exceptions
               click through to Analyst queue) -> Ingest files (IngestForm:
               per-slot include toggles + File-format dropdowns that PUT
               /sources, "+ new customer" and "⧉ All ingestions" both top
               right — IngestionsView renders inline) -> Reconcile
               (ReconcileForm: statement picker from gold/files + mode;
               MatchingConfigPanel opens inline from the "⚙ Matching config"
               button top right — edits the customer's full rule set incl.
               field_map via GET/PUT /config, dropdowns fed by
               /api/gold/schema; there is NO per-run tunables panel — the UI
               sends no tunables so the saved config governs) -> results.
               "Workspace": Analyst queue (LedgerView renamed in UI
               ONLY — /api/ledger and DB names unchanged; RunsView opens
               inline from its "⧉ Runs" button top right) + Audit trail
               (AuditTrailView over GET /api/audit — the real audit_log
               stream with client-side category/actor/window filters and a
               by-record timeline). "Platform": Architecture
               (ArchitectureView — the real six-layer stack described in
               frontend/src/architecture.ts with live KPIs from /api/
               overview; keep its statements factually in sync with this
               file when the architecture changes). Gold data tabs (GoldTable, shared
               presets in framePresets.ts, refetch-on-mount — no cache, gold
               mutates on ingest) browse the live gold layer with a per-
               ingestion filter; the four per-run frozen frames live under
               Reconciliation result -> Run data (SourceTable, cached — frames
               are immutable per run). UploadForm/runRecon are deleted;
               unchanged by the medallion refactor — same API contract, frame names
               (bank/bills/bills_enriched/recoveries), column names, summary
               Category strings. Do NOT rename any of those server-side silently.
```

## Domain logic that isn't obvious from any single file

- **The golden master is the refactor gate.** `tests/test_golden.py` diffs the engine's output on the sample documents byte-for-byte against committed CSVs. Any engine/parser/adapter change must keep it green, or regenerate the snapshots explicitly and say why.
- **Both sides are sources of truth**, so exceptions run in both directions: `BANK_ONLY` (credit with no bill) and `BILL_ONLY` (advised bill with no credit). The queue deliberately mixes both; bank rows lack bill fields and vice versa by design.
- **Snapshot vs incremental runs.** Snapshot (default) reconciles one statement against one export, results per run. Incremental accumulates: gold rows are **ingestion-owned** (written once per file, entity-upserted across files), the pool carries open exceptions forward, and matches are durable in `match_ledger` — HIGH confidence auto-LOCKs, review confidences stay OPEN until a user accepts/rejects. A LOCKED match's credit and bills never re-enter any pool; rejecting releases both sides. The wide-open expected-window in incremental mode intentionally reports the whole open-bill backlog once — rows persist as OPEN exceptions, not duplicated per run.
- **Amount is a filter, not a signal.** Pairs are only scored if amounts already agree (indexed on `round(net_payable_amount, 2)`); zone and date break ties. Confidence labels are derived from the raw signals, not by reversing the score. Signal weights are per-customer config, but labels stay signal-derived.
- **A credit whose best pairing was claimed by another credit is not allowed to settle for a worse one** — it falls to the exception queue. A missing match you can investigate beats a wrong match you cannot see.
- **The Bills + lineage tab serves the grouped frame** (`engine.group_bill_attempts`): one row per bill, RETURNED resubmissions combined (representative = settled attempt if any, else latest by submission_date asc / data_row desc), full journey in the `Attempts` list column. Matching and the raw Bills tab always use ungrouped frames.
- **Settlement is stamped onto bills_enriched** in `reconcile()` (`SettledInStatement` + `Settled_*` columns, `match_id`/`Settled_MatchId` = `m{n}`); the display gate to HIGH-only lives in the frontend (`SourceTable` injects the `Settled='SETTLED'` token).
- **Weak matches are copied into the queue as `MATCH_REVIEW`** (`AMBIGUOUS`/`LOW`/`AMOUNT_ONLY`/`BATCHED`). They stay in the matched frame — bills remain claimed — with structured `Candidates` (API) and flat `CandidateSummary` (Excel).
- **`window_days` (default 0)** bounds which bills are "expected" in a snapshot statement; money cannot arrive before IREPS advises the bank. Incremental mode ignores it (the open pool replaces the window).
- **`paid_statuses`** default `{"PAYMENT MADE", "CO7 DONE"}` — CO7 DONE counts because the payment order goes out before the export refreshes. Now a real config: `MatchRuleSet.paid_statuses`, per-customer row in `match_rule_sets`, threaded through `reconcile()` to the matcher.
- **Matching fields are per-customer config** (`FieldMapping` in `rules.py`, stored as JSON in `match_rule_sets.field_map`, edited in the UI's Matching config view). Only *signals* are configurable — display columns, gap_type literals, and the legacy-named MatchResult columns (`zone_from_narrative`/`bill_zone`/`zone_check` carry the FIRST exact signal's values; `date_source` stays `"advice"`/`"co7"` meaning primary/fallback) are gold-canonical and hardcoded. Two traps: (1) `engine.reconcile()` is called directly by `db/reconcile_gold.py` and `db/incremental.py` — the golden gate does NOT cover those two call sites, so any new rule knob must be threaded there by hand (`meta.rules_effective` is the guard); (2) with zero exact signals `all([])` is vacuously True — `exact_ok = bool(mapping.exact_signals) and all(checks)` guards it (regression-tested in `tests/test_field_mapping.py`). A NULL/`{}` `field_map` row means defaults; the default mapping is byte-identical to the historical hardcoded behavior under the golden gate.
- **IREPS data is dirty in specific ways** the code already handles — don't regress: four spellings of "nothing here" (`None`, `nan`, `"nan"`, `"----"`) via `scoring.norm_text`; int/str ID drift via `lineage._key`; quantities like `"3 Set"` (why `gold_lineage_docs.receipt_qty` is a String); recovery amounts packed several to a cell (summed); Net Amt rounded to whole rupees (₹1 slack).
- **Bill Status is a block-format sheet, not a table** — label-driven header parsing; unknown labels land in `UnparsedHeader` (stored in gold `extras`, never dropped).
- **Zone extraction** tests longer railway zone codes first so `NER` isn't read as `ER` (`bank_hsbc.ZONE_CODES` order matters).
- **Bank self-check is fail-loud**: the HSBC adapter's `selfcheck` ties parsed credits to the totals printed on the statement's last page and raises `SelfCheckError` on mismatch; everything downstream depends on that parse.
- **Money is Float end-to-end** (golden parity). Moving to Numeric/Decimal is a deliberate future migration that requires re-baselining the golden master — do not change it casually.
- **Known deferrals (by decision, not oversight)**: no auth/tenant isolation yet (customer_id is a form field), runs execute synchronously in a request threadpool (no job queue), single-worker uvicorn assumed, run-file retention keeps last 20 workbooks (DB rows kept forever).
