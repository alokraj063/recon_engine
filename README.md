# Receivables recon

Reconciles the HSBC daily statement against the IREPS Bill Status export,
with RNOTE / CRN lineage attached. Both files are sources of truth, so
exceptions run in both directions.

## Layout

    backend/
        recon/              the engine (unchanged package)
            __init__.py         public API
            config.py           ReconConfig - every path and threshold
            parsers/
                bank_hsbc.py    statement PDF -> credits (cell-based table read)
                bill_status.py  IREPS blocks -> one row per bill + recoveries
                lineage.py      RNOTE / CRN loaders and the join to bills
            matching/
                scoring.py      how good is one pairing (pure functions)
                matcher.py      the three-pass loop
            engine.py           two-sided reconcile, exception queue, run()
            report.py           Excel output
            cli.py              python -m recon
        app/                FastAPI wrapper
            main.py             app instance, CORS, /api/health
            routes.py           POST /api/runs, GET /api/runs/{id}[/workbook]
            runs.py             in-memory run store (download never recomputes)
            serialize.py        frames -> JSON-safe (NaN/NaT/dict/list columns)
        requirements.txt
    frontend/               Vite + React + TypeScript SPA
        src/                upload form, advanced tunables, summary
                            dashboard, matched table, exception queue with
                            expandable lineage detail, workbook download

Dependencies run one way only: parsers know nothing about matching,
matching knows nothing about the engine, the engine knows nothing about
Excel, and the FastAPI app only moves files in and frames out.

## Run the web UI

    # one-time
    .venv/bin/pip install -r backend/requirements.txt
    cd frontend && npm install        # Node >= 18

    # terminal 1 - backend
    cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

    # terminal 2 - frontend (proxies /api to :8000)
    cd frontend && npm run dev        # open http://localhost:5173

Ingesting and reconciling are two steps. On **Ingest files**, attach a
document to each slot you want in this ingestion — an ingestion is
exactly the files you attach, nothing is ever substituted for an empty
slot — then go to **Run reconciliation**, pick the statement, and run.
Download the formatted workbook from the result header.

Besides the result tabs, a "source data" tab group shows what the parsers
extracted: the full bank statement table (debits included, recon rows
marked), the raw Bill Status parse, the lineage-enriched bills (each row
expands to its PO -> receipt -> CO6 -> CO7 -> advice history) and the
recovery detail. These load on demand from /api/runs/{id}/frames/{name}.

## CLI / library use

Run from `backend/`:

    cd backend
    ../.venv/bin/python -m recon --statement stmt.PDF --bills bills.xlsx \
                                 --rnote rnote.xlsx --crn crn.xlsx -o out.xlsx

Or:

    from recon import ReconConfig, run, write_workbook

    cfg = ReconConfig(
        statement_pdf="Daily_statement_18Mar2026.PDF",
        bill_status="BILL STATUS 20032026.xlsx",
        rnote="RNOTE IREPS 31032026.xlsx",
        crn="CRN IREPS 31032026.xlsx",
        output_xlsx="Recon_18Mar2026.xlsx",
    )
    out = run(cfg)
    write_workbook(out, cfg.output_xlsx)
    out["summary"]

`run()` returns a dict: matched, bank_only, bill_only, queue, summary,
bank, bills, recoveries, bills_enriched.

See `parameters.md` for a per-parameter guide to all six tunables — what
each governs, when to adapt it, and what widening it costs.

## The one setting that matters

`window_days` decides which bills could have been paid in this statement.
Money cannot arrive before IREPS advises the bank, so the default is 0:
the advice date must fall inside the statement's own dates. Widen it for
a multi-day statement. Widen it too far and bills that settled in an
earlier statement get reported as false shortfalls.

## Failing loudly

`run()` raises if the parsed credit count and total do not tie to the
figures HSBC prints on the last page of the statement. Everything
downstream depends on that parse, so it should stop rather than produce
a plausible-looking wrong answer. The API surfaces this as a 422
`BANK_SELFCHECK_FAILED` and the UI shows the stated vs parsed figures.
