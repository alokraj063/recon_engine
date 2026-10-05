"""A small in-process imitation of the Nuxeo endpoints in ECM_API_DOC, for tests."""
from __future__ import annotations

import json
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FOLDERS = {
    "/FT/workspaces/Sites/HSR/Invoice Certificate - TS/Inputs": "folder-hsr-inputs",
    "/FT/workspaces/Sites/HSR/Invoice Certificate - TS/Tenders": "folder-hsr-tenders",
    "/FT/workspaces/Sites/BRH - FT India (HPD) - R/Invoice Certificate/Inputs": "folder-brh-inputs",
}


class FakeNuxeo:
    def __init__(self, expires_in: int | None = 3600):
        self.expires_in = expires_in
        self.tokens_issued: list[str] = []
        self.revoked: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.queries: list[str] = []
        self.batches: dict[str, dict[str, dict]] = {}
        self.created: list[dict] = []          # the JSON bodies POSTed to /id/{folder}
        self.docs: dict[str, dict] = {}
        self.blobs: dict[str, tuple[str, str, bytes]] = {}
        self.search_results: list[dict] | None = None   # override for search tests
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", extra=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _authed(self):
                auth = self.headers.get("Authorization", "")
                tok = auth.removeprefix("Bearer ")
                if tok not in fake.tokens_issued or tok in fake.revoked:
                    self._send(401, {"status": 401, "message": "Unauthorized"})
                    return False
                return True

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.calls.append(("POST", u.path))
                if u.path == "/nuxeo/oauth2/token":
                    q = dict(urllib.parse.parse_qsl(u.query))
                    if q.get("client_secret") != "sec":
                        return self._send(400, {"error": "invalid_client"})
                    tok = f"tok-{len(fake.tokens_issued) + 1}"
                    fake.tokens_issued.append(tok)
                    payload = {"access_token": tok, "token_type": "bearer"}
                    if fake.expires_in is not None:
                        payload["expires_in"] = fake.expires_in
                    return self._send(200, payload)
                if not self._authed():
                    return
                if u.path == "/nuxeo/api/v1/upload":
                    bid = f"batch-{len(fake.batches) + 1}"
                    fake.batches[bid] = {}
                    return self._send(201, {"batchId": bid})
                if u.path.startswith("/nuxeo/api/v1/upload/"):
                    _, bid, fid = u.path.rsplit("/", 2)
                    fake.batches[bid][fid] = {
                        "name": urllib.parse.unquote(self.headers.get("X-File-Name", "")),
                        "type": self.headers.get("X-File-Type"), "data": body}
                    return self._send(201, {"uploaded": "true", "batchId": bid, "fileIdx": fid})
                if u.path.startswith("/nuxeo/api/v1/id/"):
                    folder = u.path.rsplit("/", 1)[1]
                    doc_in = json.loads(body)
                    fake.created.append({"folder": folder, **doc_in})
                    props = dict(doc_in["properties"])
                    ref = props.pop("file:content")
                    up = fake.batches[ref["upload-batch"]][ref["upload-fileId"]]
                    uid = str(uuid.uuid4())
                    props["file:content"] = {"name": up["name"], "mime-type": up["type"],
                                             "length": str(len(up["data"]))}
                    doc = {"entity-type": "document", "uid": uid, "type": doc_in["type"],
                           "path": f"/{folder}/{doc_in['name']}", "title": props["dc:title"],
                           "state": "project", "properties": props}
                    fake.docs[uid] = doc
                    fake.blobs[uid] = (up["name"], up["type"], up["data"])
                    return self._send(201, doc)
                return self._send(404, {"message": "no route"})

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                fake.calls.append(("GET", u.path))
                if not self._authed():
                    return
                if u.path == "/nuxeo/api/v1/search/lang/NXQL/execute":
                    q = dict(urllib.parse.parse_qsl(u.query))
                    nxql = q["query"]
                    fake.queries.append(nxql)
                    for path, uid in FOLDERS.items():
                        if f"ecm:path = '{path}'" in nxql:
                            return self._send(200, {"entries": [{"uid": uid, "path": path}],
                                                    "isNextPageAvailable": False})
                    if "ecm:path = " in nxql:
                        return self._send(200, {"entries": [], "isNextPageAvailable": False})
                    entries = fake.search_results if fake.search_results is not None else list(fake.docs.values())
                    size, page = int(q.get("pageSize", 100)), int(q.get("currentPageIndex", 0))
                    chunk = entries[page * size:(page + 1) * size]
                    return self._send(200, {"entries": chunk,
                                            "isNextPageAvailable": (page + 1) * size < len(entries)})
                if u.path.startswith("/nuxeo/api/v1/id/"):
                    uid = u.path.rsplit("/", 1)[1]
                    if uid not in fake.docs:
                        return self._send(404, {"message": "not found"})
                    return self._send(200, fake.docs[uid])
                if u.path.startswith("/nuxeo/api/v1/repo/default/id/") and "/@blob/" in u.path:
                    uid = u.path.split("/id/", 1)[1].split("/", 1)[0]
                    if uid not in fake.blobs:
                        return self._send(404, {"message": "not found"})
                    name, ctype, data = fake.blobs[uid]
                    return self._send(200, data, ctype, {"Content-Disposition":
                                      f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})
                return self._send(404, {"message": "no route"})

        return H
