"""Command line, run from backend/ECM/:

    python -m ecm_client catalog [--env PROD]            # what the config defines (no ECM call)
    python -m ecm_client token
    python -m ecm_client search   --site hosur --kind taxInvoice [--location storage] [--filter invoice_number=INV-1]
    python -m ecm_client query    --site hosur --kind taxInvoice     # print the NXQL only
    python -m ecm_client get      <uid>
    python -m ecm_client download <uid> [--to DIR]
    python -m ecm_client pull     --site hosur --kind inspectionCertificate [--filter ...] [--to DIR] [--overwrite]
    python -m ecm_client upload   --site hosur --kind rNoteReports --file r.pdf --field invoice_number=INV-1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

from .catalog import load_catalog
from .config import ECM_DIR
from .errors import ECMError
from .service import ECMDocumentService

DEFAULT_DOWNLOADS = Path.home() / "Downloads" / "ecm"


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def _pairs(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ecm_client", description="ECM (Nuxeo) documents")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("catalog", help="sites, locations, kinds and fields from config (no ECM call)")
    c.add_argument("--env", help="TEST or PROD (default: ECM_ENV)")

    sub.add_parser("token", help="fetch a token and show the cache state (never the token)")

    def search_args(sp):
        sp.add_argument("--site", required=True)
        sp.add_argument("--kind")
        sp.add_argument("--location", help="narrow to one location, e.g. inputs or storage")
        sp.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE",
                        help="field filter, repeatable (field keys: see catalog)")

    se = sub.add_parser("search", help="find documents")
    search_args(se)
    se.add_argument("--limit", type=int, default=200)

    q = sub.add_parser("query", help="print the NXQL a search would run (no ECM call)")
    search_args(q)

    g = sub.add_parser("get", help="one document's metadata")
    g.add_argument("uid")

    d = sub.add_parser("download", help="save one document's attachment")
    d.add_argument("uid")
    d.add_argument("--to", type=Path, default=DEFAULT_DOWNLOADS)
    d.add_argument("--filename")

    pl = sub.add_parser("pull", help="download every matching document to <to>/<site>/<kind>/")
    search_args(pl)
    pl.add_argument("--to", type=Path, default=DEFAULT_DOWNLOADS)
    pl.add_argument("--limit", type=int, default=500)
    pl.add_argument("--overwrite", action="store_true", help="re-download files already on disk")

    up = sub.add_parser("upload", help="save a file as a document (kinds with an upload rule)")
    up.add_argument("--site", required=True)
    up.add_argument("--kind", required=True)
    up.add_argument("--file", required=True, type=Path)
    up.add_argument("--title")
    up.add_argument("--field", action="append", default=[], metavar="FIELD=VALUE")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.cmd == "catalog":
            try:
                from dotenv import load_dotenv
                load_dotenv(ECM_DIR / ".env", override=False)
            except ImportError:
                pass
            env = args.env or os.environ.get("ECM_ENV", "TEST")
            _print(load_catalog(env, os.environ.get("ECM_CONFIG_DIR")).as_dict())
            return 0

        svc = ECMDocumentService.from_env()
        if args.cmd == "token":
            svc.client.tokens.get()
            _print(svc.health())
        elif args.cmd == "query":
            print(svc.build_query(site=args.site, kind=args.kind, location=args.location,
                                  filters=_pairs(args.filter)))
        elif args.cmd == "search":
            docs = svc.find_documents(site=args.site, kind=args.kind, location=args.location,
                                      filters=_pairs(args.filter), limit=args.limit)
            _print([x.to_dict() for x in docs])
            print(f"{len(docs)} documents", file=sys.stderr)
        elif args.cmd == "get":
            _print(svc.get(args.uid).to_dict())
        elif args.cmd == "download":
            print(svc.download_to(args.uid, args.to.expanduser(), filename=args.filename))
        elif args.cmd == "pull":
            results = svc.pull(site=args.site, kind=args.kind, location=args.location,
                               filters=_pairs(args.filter), dest_dir=args.to, limit=args.limit,
                               overwrite=args.overwrite)
            for r in results:
                print(f"{r.status:<10} {r.local_path or r.uid}" + (f"  ({r.error})" if r.error else ""))
            counts = Counter(r.status for r in results)
            print(", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing matched",
                  file=sys.stderr)
            return 1 if counts.get("failed") else 0
        elif args.cmd == "upload":
            doc = svc.save_file(args.file, site=args.site, kind=args.kind, title=args.title,
                                fields=_pairs(args.field))
            _print(doc.to_dict())
    except ECMError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
