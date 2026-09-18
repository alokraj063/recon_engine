"""
Password hashing, as a top-level sibling of recon/db/app — the same
placement and reason as logging_setup.py: db/seeds.py, app/auth.py and
backend/scripts/ all need it, and db/ must never import app/.

bcrypt with a per-password salt, cost left at the library default. The
72-byte ceiling is bcrypt's own: it hashes the first 72 bytes and
silently ignores the rest, so a 100-character passphrase would have a
28-character tail that does nothing. Refusing is honest; truncating is
not.

Nothing here touches the database or the session — it turns a string
into a hash and back into a yes/no.
"""

import bcrypt

# bcrypt's hard limit, in BYTES: a non-ASCII character costs more than one
MAX_PASSWORD_BYTES = 72
# not a policy engine, just a floor that keeps "1234" out
MIN_PASSWORD_CHARS = 8


class PasswordError(ValueError):
    """A password that cannot be hashed as given (too long, too short)."""


def _password_bytes(raw: str) -> bytes:
    data = raw.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        raise PasswordError(
            f"password must be at most {MAX_PASSWORD_BYTES} bytes "
            f"(got {len(data)}); bcrypt ignores everything beyond that")
    return data


def validate_password(raw: str) -> str:
    """Check a NEW password before hashing it. Returns it unchanged so
    callers can inline the check."""
    if len(raw) < MIN_PASSWORD_CHARS:
        raise PasswordError(
            f"password must be at least {MIN_PASSWORD_CHARS} characters")
    _password_bytes(raw)
    return raw


def hash_password(raw: str) -> str:
    """Hash a password for storage. Raises PasswordError if it cannot be
    hashed as given — never silently truncates."""
    validate_password(raw)
    return bcrypt.hashpw(_password_bytes(raw), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    """Constant-time check of a candidate password against a stored hash.

    Returns False rather than raising for every reject path — an
    over-long candidate, a stored value that isn't a bcrypt hash at all —
    so a caller can never mistake a malformed input for a match.
    """
    try:
        return bcrypt.checkpw(_password_bytes(raw), hashed.encode("utf-8"))
    except (PasswordError, ValueError, TypeError):
        return False


def normalize_email(email: str) -> str:
    """The one spelling rule for the users.email unique index."""
    return email.strip().lower()
