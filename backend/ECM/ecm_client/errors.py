"""Exceptions raised by the ECM client. Every one derives from ECMError."""
from __future__ import annotations


class ECMError(RuntimeError):
    """A failed ECM call. `status` is the HTTP status when there was a response."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class ECMConfigError(ECMError):
    """Settings are missing or inconsistent (credentials, site layout)."""


class ECMAuthError(ECMError):
    """The ECM refused the credentials or the bearer token."""


class ECMNotFound(ECMError):
    """The document, folder or path does not exist (or the client cannot see it)."""


class ECMValidationError(ECMError):
    """The request was rejected before reaching the ECM: missing metadata, bad site/kind."""
