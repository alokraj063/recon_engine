"""Generic Nuxeo REST client: one method per endpoint in ECM_API_DOC, nothing site-specific.

    get_token              POST /nuxeo/oauth2/token                (auth.TokenProvider, cached)
    get_document           GET  /nuxeo/api/v1/id/{uid}
    search                 GET  /nuxeo/api/v1/search/lang/NXQL/execute?query=...
    open_batch             POST /nuxeo/api/v1/upload
    upload_to_batch        POST /nuxeo/api/v1/upload/{batch}/{upload_id}
    create_document        POST /nuxeo/api/v1/id/{folder_uid}
    download / download_to GET  /nuxeo/api/v1/repo/default/id/{uid}/@blob/blobholder:0

Domain rules (which site, which folder, which metadata a kind needs) live in service.py.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .auth import TokenProvider
from .config import ECMSettings
from .errors import ECMAuthError, ECMError, ECMNotFound
from .transport import Response, UrllibTransport

log = logging.getLogger("ecm_client")

API = "/nuxeo/api/v1"


def nxql_literal(value: object) -> str:
    """A value as NXQL: booleans as 1/0, numbers bare, anything else quoted and escaped."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def guess_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def filename_from_disposition(disposition: str) -> str | None:
    """Pull the filename out of a Content-Disposition header (RFC 5987 form preferred)."""
    plain = None
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename*="):
            return urllib.parse.unquote(part.split("''", 1)[-1].strip('"'))
        if part.lower().startswith("filename="):
            plain = part.split("=", 1)[1].strip().strip('"')
    return plain or None


@dataclass
class Blob:
    filename: str
    content_type: str
    data: bytes


class ECMClient:
    def __init__(self, settings: ECMSettings, *, transport: Any = None,
                 token_provider: TokenProvider | None = None):
        self.settings = settings
        self.transport = transport or UrllibTransport(verify_ssl=settings.verify_ssl,
                                                      timeout=settings.timeout)
        self.tokens = token_provider or TokenProvider(settings, self.transport)
        self._folder_ids: dict[str, str] = {}      # path -> uid; folder uids never change
        self._folder_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "ECMClient":
        return cls(ECMSettings.from_env())

    # ------------------------------------------------------------------ plumbing
    def _call(self, method: str, path: str, *, headers: dict[str, str] | None = None,
              data: bytes | None = None, sink: BinaryIO | None = None) -> Response:
        """Authenticated call. On 401 the cached token is dropped and the call retried once."""
        url = f"{self.settings.domain}{path}"
        for attempt in (1, 2):
            hdrs = {"Authorization": f"Bearer {self.tokens.get()}", **(headers or {})}
            try:
                return self.transport.send(method, url, headers=hdrs, data=data, sink=sink)
            except ECMAuthError as exc:
                if exc.status != 401 or attempt == 2:
                    raise
                log.warning("ecm returned 401, refreshing token and retrying once")
                self.tokens.invalidate()
                if sink is not None:
                    sink.seek(0)
                    sink.truncate()
        raise AssertionError("unreachable")

    def _json(self, method: str, path: str, **kw) -> Any:
        payload = self._call(method, path, **kw).json()
        if not isinstance(payload, dict):
            raise ECMError(f"{method} {path}: unexpected response {payload!r}")
        return payload

    # ------------------------------------------------------------------ read
    def get_document(self, uid: str, *, properties: str = "*") -> dict:
        return self._json("GET", f"{API}/id/{uid}", headers={"Properties": properties})

    def search(self, nxql: str, *, page_size: int = 100, limit: int | None = None,
               properties: str = "*") -> list[dict]:
        """Run an NXQL query, following pages until exhausted or `limit` entries are collected."""
        results: list[dict] = []
        page = 0
        while True:
            q = urllib.parse.urlencode({"query": nxql, "pageSize": page_size, "currentPageIndex": page})
            payload = self._json("GET", f"{API}/search/lang/NXQL/execute?{q}",
                                 headers={"Properties": properties})
            entries = payload.get("entries") or []
            results.extend(entries)
            if limit is not None and len(results) >= limit:
                return results[:limit]
            if not entries or not payload.get("isNextPageAvailable"):
                return results
            page += 1

    def resolve_folder_id(self, path: str) -> str:
        """Folder PATH -> uid (the create endpoint takes a uid). Cached for the process lifetime."""
        with self._folder_lock:
            if path in self._folder_ids:
                return self._folder_ids[path]
        entries = self.search(f"SELECT * FROM Document WHERE ecm:path = {nxql_literal(path)}",
                              limit=1, properties="dublincore")
        if not entries:
            raise ECMNotFound(f"no folder at {path!r} (check the path and the client's permissions)")
        uid = entries[0]["uid"]
        with self._folder_lock:
            self._folder_ids[path] = uid
        return uid

    def list_children(self, folder_path: str, *, limit: int | None = 500) -> list[dict]:
        uid = self.resolve_folder_id(folder_path)
        return self.search(f"SELECT * FROM Document WHERE ecm:parentId = {nxql_literal(uid)} "
                           "AND ecm:isVersion = 0 AND ecm:isTrashed = 0 ORDER BY dc:created DESC",
                           limit=limit)

    # ------------------------------------------------------------------ write
    def open_batch(self) -> str:
        payload = self._json("POST", f"{API}/upload", data=b"")
        if not payload.get("batchId"):
            raise ECMError(f"upload batch response had no batchId: {payload!r}")
        return payload["batchId"]

    def upload_to_batch(self, batch_id: str, upload_id: str, content: bytes, filename: str,
                        mime_type: str | None = None) -> dict:
        mime = mime_type or guess_mime(filename)
        return self._json("POST", f"{API}/upload/{batch_id}/{upload_id}", data=content, headers={
            "X-File-Name": urllib.parse.quote(filename),   # header values must be ASCII
            "X-File-Type": mime,
            "Content-Type": mime,
        })

    def create_document(self, parent_uid: str, *, name: str, doc_type: str,
                        properties: dict[str, Any]) -> dict:
        body = {"entity-type": "document", "name": name, "type": doc_type, "properties": properties}
        return self._json("POST", f"{API}/id/{parent_uid}", data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json"})

    def create_document_with_file(self, parent_path: str, *, name: str, doc_type: str,
                                  properties: dict[str, Any], content: bytes, filename: str,
                                  mime_type: str | None = None) -> dict:
        """Batch upload + create, the "Create a document with a file" flow from the doc."""
        parent_uid = self.resolve_folder_id(parent_path)
        batch_id = self.open_batch()
        self.upload_to_batch(batch_id, "0", content, filename, mime_type)
        props = {**properties, "file:content": {"upload-batch": batch_id, "upload-fileId": "0"}}
        doc = self.create_document(parent_uid, name=name, doc_type=doc_type, properties=props)
        log.info("ecm document created uid=%s type=%s bytes=%d", doc.get("uid"), doc_type, len(content))
        return doc

    # ------------------------------------------------------------------ download
    def _blob_path(self, uid: str, xpath: str) -> str:
        return f"{API}/repo/default/id/{uid}/@blob/{xpath}"

    def download(self, uid: str, *, xpath: str = "blobholder:0") -> Blob:
        resp = self._call("GET", self._blob_path(uid, xpath))
        return Blob(filename=filename_from_disposition(resp.header("content-disposition")) or uid,
                    content_type=resp.header("content-type") or "application/octet-stream",
                    data=resp.body)

    def download_to(self, uid: str, dest_dir: str | Path, *, filename: str | None = None,
                    xpath: str = "blobholder:0") -> Path:
        """Stream the attachment to dest_dir. Uses the ECM's filename unless one is given."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False, suffix=".part") as tmp:
            try:
                resp = self._call("GET", self._blob_path(uid, xpath), sink=tmp)
            except BaseException:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise
        name = filename or filename_from_disposition(resp.header("content-disposition")) or uid
        out = dest_dir / Path(name).name          # never let a server filename escape dest_dir
        Path(tmp.name).replace(out)
        return out
