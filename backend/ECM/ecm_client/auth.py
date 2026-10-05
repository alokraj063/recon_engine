"""Bearer token, fetched once and kept in memory until it is about to expire.

The ECM issues client-credentials tokens valid for an hour. TokenProvider.get() returns the
cached token while it is fresh and only calls the token endpoint again once it is within
`token_refresh_margin` seconds of expiry, or after invalidate() (the client calls that on a 401,
so a token revoked server-side is replaced instead of failing every call for the rest of the hour).

Thread-safe: concurrent callers share one fetch. The token lives in this process only; it is
never written to disk or logged.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Callable

from .config import ECMSettings
from .errors import ECMAuthError
from .transport import UrllibTransport

log = logging.getLogger("ecm_client.auth")


class TokenProvider:
    def __init__(self, settings: ECMSettings, transport: UrllibTransport,
                 clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0          # clock() value after which the token is not reused
        self._issued_at_wall: datetime | None = None
        self.fetch_count = 0            # how many times the token endpoint was called

    def get(self) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._expires_at:
                self._fetch()
            return self._token  # type: ignore[return-value]

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def status(self) -> dict:
        """Cache state for health checks. Never includes the token itself."""
        with self._lock:
            fresh = self._token is not None and self._clock() < self._expires_at
            return {
                "cached": fresh,
                "seconds_remaining": int(self._expires_at - self._clock()) if fresh else 0,
                "issued_at": self._issued_at_wall.isoformat() if self._issued_at_wall else None,
                "fetch_count": self.fetch_count,
            }

    def _fetch(self) -> None:
        s = self._settings
        query = urllib.parse.urlencode({"grant_type": "client_credentials",
                                        "client_id": s.client_id, "client_secret": s.client_secret})
        resp = self._transport.send("POST", f"{s.domain}/nuxeo/oauth2/token?{query}", data=b"")
        payload = resp.json()
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ECMAuthError("token response had no access_token", status=resp.status)
        try:
            ttl = int(payload.get("expires_in") or s.token_ttl_fallback)
        except (TypeError, ValueError):
            ttl = s.token_ttl_fallback
        self._token = payload["access_token"]
        self._expires_at = self._clock() + max(ttl - s.token_refresh_margin, 0)
        self._issued_at_wall = datetime.now(timezone.utc)
        self.fetch_count += 1
        log.info("ecm token fetched (valid %ss, reused for %ss)", ttl, max(ttl - s.token_refresh_margin, 0))
