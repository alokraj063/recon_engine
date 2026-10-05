"""Settings, read from the environment (plus an optional ECM/.env file).

    ECM_ENV              TEST | PROD (default TEST). Picks the site layout in catalog.py;
                         the two environments have different paths, site ids and credentials.
    ECM_DOMAIN           e.g. http://host:8080   (trailing slash is stripped)
    ECM_CLIENT_ID
    ECM_CLIENT_SECRET
    ECM_VERIFY_SSL       true/false (default false, matching the `curl -k` in the API doc)
    ECM_TIMEOUT_SECONDS  per-request timeout (default 120)
    ECM_CONFIG_DIR       folder holding document_kinds.toml + sites.<env>.toml
                         (default ecm_client/config/)

Real environment variables always win over the .env file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ECMConfigError

ECM_DIR = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ("TEST", "PROD")


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ECMSettings:
    domain: str
    client_id: str
    client_secret: str = field(repr=False)
    environment: str = "TEST"
    verify_ssl: bool = False
    timeout: float = 120.0
    config_dir: str | None = None
    # A token is reused until (expires_in - refresh_margin) seconds after it was issued.
    # expires_in comes from the token response; token_ttl_fallback is used when it is absent.
    token_ttl_fallback: int = 3600
    token_refresh_margin: int = 60

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", self.domain.rstrip("/"))
        object.__setattr__(self, "environment", self.environment.upper())
        if self.environment not in ENVIRONMENTS:
            raise ECMConfigError(f"ECM_ENV must be one of {ENVIRONMENTS}, got {self.environment!r}")
        missing = [n for n, v in (("ECM_DOMAIN", self.domain), ("ECM_CLIENT_ID", self.client_id),
                                  ("ECM_CLIENT_SECRET", self.client_secret)) if not v]
        if missing:
            raise ECMConfigError(f"missing settings: {', '.join(missing)} "
                                 f"(export them or put them in {ECM_DIR / '.env'})")

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "ECMSettings":
        try:
            from dotenv import load_dotenv
        except ImportError:  # python-dotenv is optional
            pass
        else:
            load_dotenv(env_file or ECM_DIR / ".env", override=False)
        return cls(
            domain=os.environ.get("ECM_DOMAIN", ""),
            client_id=os.environ.get("ECM_CLIENT_ID", ""),
            client_secret=os.environ.get("ECM_CLIENT_SECRET", ""),
            environment=os.environ.get("ECM_ENV", "TEST"),
            verify_ssl=_truthy(os.environ.get("ECM_VERIFY_SSL"), default=False),
            timeout=float(os.environ.get("ECM_TIMEOUT_SECONDS") or 120),
            config_dir=os.environ.get("ECM_CONFIG_DIR") or None,
        )
