"""The one place HTTP happens. Standard library only, so the package adds no dependency.

Swap it (any object with the same `send`) to change HTTP library or to fake the ECM in tests.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from .errors import ECMAuthError, ECMError, ECMNotFound

CHUNK = 1024 * 1024


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""          # empty when the body was streamed into a sink

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise ECMError(f"expected JSON, got {self.header('content-type')!r}: {self.body[:200]!r}",
                           status=self.status) from exc


class UrllibTransport:
    def __init__(self, *, verify_ssl: bool = False, timeout: float = 120.0):
        self.timeout = timeout
        # The API doc's examples all use `curl -k`; verification is opt-in via ECM_VERIFY_SSL.
        self._context = None if verify_ssl else ssl._create_unverified_context()  # noqa: SLF001

    def send(self, method: str, url: str, *, headers: dict[str, str] | None = None,
             data: bytes | None = None, sink: BinaryIO | None = None) -> Response:
        req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, context=self._context, timeout=self.timeout) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                if sink is not None:
                    while chunk := resp.read(CHUNK):
                        sink.write(chunk)
                    return Response(resp.status, hdrs)
                return Response(resp.status, hdrs, resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:2000]
            cls = ECMAuthError if exc.code in (401, 403) else ECMNotFound if exc.code == 404 else ECMError
            # never echo the URL's query string: the token call carries the client secret there
            raise cls(f"{method} {url.split('?', 1)[0]} -> HTTP {exc.code}: {body}",
                      status=exc.code, body=body) from exc
        except urllib.error.URLError as exc:
            raise ECMError(f"{method} {url.split('?', 1)[0]} -> connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ECMError(f"{method} {url.split('?', 1)[0]} -> timed out after {self.timeout}s") from exc
