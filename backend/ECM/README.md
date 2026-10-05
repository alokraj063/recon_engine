# ECM gateway

Finds, downloads and saves invoice-certificate documents in the Wabtec ECM (a Nuxeo server).
It is standalone: it imports nothing from the recon engine, and the recon engine does not call it yet.

## Layout

```
ecm_client/                 the library; usable from any Python code
  config/                   WHAT the ECM holds: edit these, not the code
    document_kinds.toml     kinds, their metadata fields, locations, filters, upload rules
    sites.test.toml         site ids + folder paths for ECM_ENV=TEST
    sites.prod.toml         site ids + folder paths for ECM_ENV=PROD
  catalog.py                loads and validates the config files
  config.py                 ECMSettings, read from ECM_* env vars or ECM/.env
  transport.py              the only HTTP code (standard library, no new dependency)
  auth.py                   TokenProvider: bearer token cached in memory until expiry
  client.py                 ECMClient: one method per endpoint in ECM_API_DOC
  service.py                ECMDocumentService: search, download, pull, save by site + kind
  __main__.py               command line (python -m ecm_client ...)
ecm_api/                    FastAPI layer over the service
  routes.py                 APIRouter mounted at /api/ecm (mountable into the recon app later)
  main.py                   standalone app
tests/                      run against an in-process fake Nuxeo server
```

The original step-by-step notebook lives in `scratch/ecm/` at the repo root, which git ignores.

## Setup

Copy `.env.example` to `.env` in this folder and fill in the domain and client credentials.
`ECM_ENV` picks which `sites.<env>.toml` is used. The domain and credentials must match it.

## Configuration: adding a kind, a site or a path

Everything about *what* to fetch lives in `ecm_client/config/`. Restart the API (or just rerun
the CLI) after editing. A mistake stops loading with a message naming the file, the key and the
problem, so a bad edit never runs silently.

- **New document kind**: add a `[kinds.<id>]` table to `document_kinds.toml`. The id is the
  value of `InvoiceCertificateSchema:type`.
- **New metadata field**: add it under `[fields]`, then list it in a kind's `fields`.
- **New site, or a new folder in a site**: edit `sites.<env>.toml`. A path may contain a
  `{field}` placeholder, e.g. `.../Storage/{invoice_number}`.
- **Point at a different config folder** (e.g. one managed outside the repo): `ECM_CONFIG_DIR`.

Searches follow Oceane's queries: the kind plus the site's `ecm:ancestorId`. A `location`
narrows to one folder, and field filters narrow by metadata. When a filter fills a path
placeholder (e.g. `invoice_number` in Storage), it picks the folder instead of being matched
as a property, because merged documents need not carry it. Run `query` to see the exact NXQL.

## Downloading (run from backend/ECM/)

```bash
PY=../../.venv/bin/python
$PY -m ecm_client catalog [--env PROD]                     # what the config defines
$PY -m ecm_client query  --site hosur --kind taxInvoice    # the NXQL, without calling the ECM
$PY -m ecm_client search --site hosur --kind inspectionCertificate --filter invoice_number=INV-1
$PY -m ecm_client search --site hosur --kind workCertificate --location storage --filter invoice_number=INV-1
$PY -m ecm_client download <uid> --to ~/Downloads/ecm
$PY -m ecm_client pull   --site hosur --kind billStatus --to ~/Downloads/ecm
```

`pull` saves every match to `<to>/<site>/<kind>/<name>__<uid8>.<ext>` and keeps a
`manifest.json` there (uid, metadata, local file). Rerunning it skips files already on disk,
so it only fetches what is new; `--overwrite` fetches everything again. One failed file is
reported and does not stop the rest.

## HTTP API

```bash
../../.venv/bin/uvicorn ecm_api.main:app --port 8100      # docs at http://127.0.0.1:8100/docs
```

| Method | Path | Purpose |
|---|---|---|
| GET | /api/ecm/health | settings + config files in use, token cache state (never the token) |
| GET | /api/ecm/catalog | sites, locations, kinds and fields from config |
| GET | /api/ecm/documents?site=&kind=&location=&limit=&<field>= | search; any field key is a filter |
| GET | /api/ecm/documents/{uid} | metadata |
| GET | /api/ecm/documents/{uid}/content | the attachment |
| POST | /api/ecm/documents | upload (only kinds with an `upload` rule) |

The standalone app has **no login**. Keep it on 127.0.0.1. When it joins the recon backend,
include `ecm_api.routes.router` in that app's authenticated `/api` router so it inherits the gate.

## Bearer token

Fetched on first use and reused until `expires_in` (one hour) minus a 60-second margin, then
fetched again. A 401 drops the cached token and retries the call once. The cache is per process
and thread-safe; the API shares one client across all requests.

## Tests

```bash
../../.venv/bin/python -m pytest tests -q
```

## Open points to confirm with Oceane

- `sites.prod.toml` is taken from the spreadsheet, whose ids and paths differ from Henri's TEST
  ones, so it is assumed to be production.
- Rohtak's PROD inputs folder is spelled `Input` (singular) in the spreadsheet.
- TEST `storage` paths and Rohtak's tenders folder have not been supplied.
- The spreadsheet's date example `2026-09-15T08-59-08.540Z` uses dashes in the time; uploads
  still send tender dates as `YYYY-MM-DD`.
- Henri's updated API doc (listing Invoice Certificate types) is not in this folder yet.
