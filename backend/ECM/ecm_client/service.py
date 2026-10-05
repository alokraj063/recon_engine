"""Domain layer: find, download and save documents by SITE and KIND.

Everything site- or kind-specific comes from the catalog (config/*.toml), so a new kind,
site or path is a config edit, not a code change. This is the surface the recon engine
is meant to call later (directly, or through ecm_api).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import Catalog, DocumentKind, Site, load_catalog
from .client import Blob, ECMClient, nxql_literal
from .errors import ECMError, ECMValidationError

_NAME_UNSAFE = re.compile(r"[/\\\x00-\x1f]+")
_FILE_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")
MANIFEST = "manifest.json"


@dataclass
class DocumentSummary:
    uid: str
    title: str | None
    path: str | None
    doc_type: str | None
    kind: str | None
    filename: str | None
    mime_type: str | None
    size: int | None
    created: str | None
    modified: str | None
    state: str | None
    metadata: dict[str, Any] = field(default_factory=dict)   # configured field key -> value

    @classmethod
    def from_entry(cls, doc: dict, catalog: Catalog) -> "DocumentSummary":
        props = doc.get("properties") or {}
        content = props.get("file:content") or {}
        length = content.get("length")
        return cls(
            uid=doc.get("uid"),
            title=doc.get("title") or props.get("dc:title"),
            path=doc.get("path"),
            doc_type=doc.get("type"),
            kind=props.get(catalog.type_property),
            filename=content.get("name"),
            mime_type=content.get("mime-type"),
            size=int(length) if str(length or "").isdigit() else None,
            created=props.get("dc:created"),
            modified=props.get("dc:modified") or doc.get("lastModified"),
            state=doc.get("state"),
            metadata={k: props[f.property] for k, f in catalog.fields.items()
                      if props.get(f.property) not in (None, "", [])},
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PulledFile:
    uid: str
    kind: str | None
    status: str                 # downloaded | skipped (already on disk) | failed
    local_path: str | None
    size: int | None = None
    error: str | None = None


def _local_name(doc: DocumentSummary) -> str:
    """'<original stem>__<uid prefix><ext>': readable, unique, and stable across pulls."""
    base = Path(doc.filename or doc.title or doc.uid)
    stem = _FILE_UNSAFE.sub("_", base.stem).strip(" ._") or "document"
    suffix = _FILE_UNSAFE.sub("", base.suffix) if doc.filename else ""
    return f"{stem[:100]}__{doc.uid[:8]}{suffix}"


class ECMDocumentService:
    def __init__(self, client: ECMClient, catalog: Catalog | None = None):
        self.client = client
        s = client.settings
        self.catalog = catalog or load_catalog(s.environment, s.config_dir)

    @classmethod
    def from_env(cls) -> "ECMDocumentService":
        return cls(ECMClient.from_env())

    @property
    def environment(self) -> str:
        return self.catalog.environment

    # ------------------------------------------------------------------ search
    def _field_clause(self, key: str, value: Any) -> str:
        f = self.catalog.field(key)
        prop = f"{f.property}/*" if f.is_list else f.property
        return f"{prop} = {nxql_literal(f.convert(value))}"

    def build_query(self, *, site: str, kind: str | None = None, location: str | None = None,
                    filters: dict[str, Any] | None = None) -> str:
        """The NXQL a search runs. Mirrors Oceane's queries: kind + site ancestor, then narrowing."""
        cat = self.catalog
        s = cat.site(site)
        k = cat.kind(kind) if kind else None
        filters = {key: v for key, v in (filters or {}).items() if v not in (None, "")}
        for key in filters:
            cat.field(key)
            if k and key not in k.fields:
                raise ECMValidationError(f"{k.code} cannot be filtered by {key!r}; "
                                         f"its fields: {list(k.fields)}")

        clauses = ["ecm:isVersion = 0", "ecm:isTrashed = 0"]
        if k:
            clauses.append(f"{cat.type_property} = {nxql_literal(k.code)}")
            clauses.extend(self._field_clause(key, v) for key, v in k.filters.items())
        else:
            clauses.append(f"ecm:primaryType = {nxql_literal(cat.doc_type)}")

        if s.site_id:
            clauses.append(f"ecm:ancestorId = {nxql_literal(s.site_id)}")
        else:   # no site id configured: any of the site's folders
            roots = sorted({s.resolve(name)[0] for name in s.locations})
            clauses.append("(" + " OR ".join(f"ecm:path STARTSWITH {nxql_literal(r)}" for r in roots) + ")")

        used: set[str] = set()
        if location:
            if k and location not in k.locations:
                raise ECMValidationError(f"{k.code} does not live in {location!r}; "
                                         f"its locations: {list(k.locations)}")
            path, used, _ = s.resolve(location, filters)
            clauses.append(f"ecm:path STARTSWITH {nxql_literal(path)}")
        # a field that already picked the folder is not also required as a property:
        # merged documents in Storage/<invoice_number>/ need not carry the property
        clauses.extend(self._field_clause(key, v) for key, v in filters.items() if key not in used)
        return f"SELECT * FROM Document WHERE {' AND '.join(clauses)} ORDER BY dc:created DESC"

    def find_documents(self, *, site: str, kind: str | None = None, location: str | None = None,
                       filters: dict[str, Any] | None = None, limit: int = 200) -> list[DocumentSummary]:
        nxql = self.build_query(site=site, kind=kind, location=location, filters=filters)
        return [DocumentSummary.from_entry(d, self.catalog) for d in self.client.search(nxql, limit=limit)]

    def get(self, uid: str) -> DocumentSummary:
        return DocumentSummary.from_entry(self.client.get_document(uid), self.catalog)

    # ------------------------------------------------------------------ download
    def download(self, uid: str) -> Blob:
        return self.client.download(uid)

    def download_to(self, uid: str, dest_dir: str | Path, filename: str | None = None) -> Path:
        return self.client.download_to(uid, dest_dir, filename=filename)

    def pull(self, *, site: str, dest_dir: str | Path, kind: str | None = None,
             location: str | None = None, filters: dict[str, Any] | None = None,
             limit: int = 500, overwrite: bool = False) -> list[PulledFile]:
        """Download every matching document to <dest_dir>/<site>/<kind>/.

        Files already on disk are skipped unless overwrite=True, so a pull can be re-run to
        fetch only what is new. Each folder keeps a manifest.json (uid -> metadata + local
        file) that later steps can read instead of calling the ECM again. One failed download
        does not stop the rest; it is reported with status "failed".
        """
        s = self.catalog.site(site)
        docs = self.find_documents(site=site, kind=kind, location=location, filters=filters, limit=limit)
        root = Path(dest_dir).expanduser()
        results: list[PulledFile] = []
        manifests: dict[Path, dict] = {}
        for doc in docs:
            folder = root / s.key / _FILE_UNSAFE.sub("_", doc.kind or kind or "unknown")
            if folder not in manifests:
                manifests[folder] = self._read_manifest(folder)
            local = folder / _local_name(doc)
            if local.exists() and not overwrite:
                results.append(PulledFile(doc.uid, doc.kind, "skipped", str(local), local.stat().st_size))
            else:
                try:
                    self.client.download_to(doc.uid, folder, filename=local.name)
                    results.append(PulledFile(doc.uid, doc.kind, "downloaded", str(local), local.stat().st_size))
                except ECMError as exc:
                    results.append(PulledFile(doc.uid, doc.kind, "failed", None, error=str(exc)))
                    continue
            manifests[folder][doc.uid] = {**doc.to_dict(), "local_file": local.name,
                                          "pulled_at": datetime.now(timezone.utc).isoformat()}
        for folder, entries in manifests.items():
            if entries:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / MANIFEST).write_text(json.dumps(dict(sorted(entries.items())), indent=2,
                                                          ensure_ascii=False, default=str))
        return results

    @staticmethod
    def _read_manifest(folder: Path) -> dict:
        try:
            data = json.loads((folder / MANIFEST).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ save (upload)
    def save_document(self, *, site: str, kind: str, content: bytes, filename: str,
                      title: str | None = None, fields: dict[str, Any] | None = None,
                      invoice_number: str | None = None, tender_number: str | None = None,
                      tender_date: Any = None, mime_type: str | None = None,
                      extra_properties: dict[str, Any] | None = None) -> DocumentSummary:
        cat = self.catalog
        s, k = cat.site(site), cat.kind(kind)
        if not k.upload:
            raise ECMValidationError(f"{k.code} is download-only: its config has no upload rule")
        if not content:
            raise ECMValidationError("the attachment is empty")
        if not filename:
            raise ECMValidationError("filename is required")

        named = {"invoice_number": invoice_number, "tender_number": tender_number,
                 "tender_date": tender_date}
        values = {key: v for key, v in (fields or {}).items() if v not in (None, "")}
        values.update({key: v for key, v in named.items() if v not in (None, "")})
        for key in values:
            if key not in k.fields:
                raise ECMValidationError(f"{k.code} has no field {key!r}; its fields: {list(k.fields)}")
        missing = [f for f in k.upload.required if f not in values]
        if missing:
            raise ECMValidationError(f"{k.code} requires {', '.join(missing)}")
        if k.upload.location not in s.locations:
            raise ECMValidationError(f"site {s.key!r} has no {k.upload.location} location, "
                                     f"so it cannot hold {k.code!r} documents")
        folder, _, complete = s.resolve(k.upload.location, values)
        if not complete:
            raise ECMValidationError(f"the {k.upload.location} path needs a value for each placeholder")

        title = (title or Path(filename).stem).strip()
        props: dict[str, Any] = {"dc:title": title, cat.type_property: k.code}
        for key, v in values.items():
            props[cat.field(key).property] = cat.field(key).convert(v)
        props.update(extra_properties or {})

        doc = self.client.create_document_with_file(
            folder, name=_NAME_UNSAFE.sub("-", title)[:120] or k.code, doc_type=k.doc_type,
            properties=props, content=content, filename=Path(filename).name, mime_type=mime_type)
        return DocumentSummary.from_entry(doc, cat)

    def save_file(self, path: str | Path, **kw) -> DocumentSummary:
        path = Path(path)
        return self.save_document(content=path.read_bytes(), filename=path.name, **kw)

    def health(self) -> dict:
        s = self.client.settings
        return {"environment": self.environment, "domain": s.domain,
                "config": list(self.catalog.files), "token": self.client.tokens.status()}
