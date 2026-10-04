"""Single-account login for the Web Console: scrypt password hash + HMAC-signed cookie.

Standard library only -- no extra dependency for one operator account. The
hash format avoids `$` so it can sit unquoted in `.env` without Docker Compose
treating it as variable interpolation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass, field

HASH_SCHEME = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

SESSION_COOKIE_NAME = "tt_console_session"


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        plain.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_BYTES
    )
    return f"{HASH_SCHEME}:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{salt.hex()}:{derived.hex()}"


def verify_password(plain: str, encoded: str) -> bool:
    """Constant-time check; a malformed stored hash verifies as False, never raises."""
    try:
        scheme, n, r, p, salt_hex, key_hex = encoded.split(":")
        if scheme != HASH_SCHEME:
            return False
        expected = bytes.fromhex(key_hex)
        derived = hashlib.scrypt(
            plain.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(derived, expected)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def issue_session_token(username: str, secret: str, *, ttl_seconds: float, now: float) -> str:
    body = _b64encode(
        json.dumps({"u": username, "exp": int(now + ttl_seconds)}, separators=(",", ":")).encode()
    )
    return f"{body}.{_sign(body, secret)}"


def read_session_token(token: str, secret: str, *, now: float) -> str | None:
    """Return the username of a valid, unexpired token, else None."""
    body, sep, signature = token.partition(".")
    if not sep or not hmac.compare_digest(signature, _sign(body, secret)):
        return None
    try:
        payload = json.loads(_b64decode(body))
        username = payload["u"]
        expires_at = payload["exp"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(username, str) or not isinstance(expires_at, int) or expires_at <= now:
        return None
    return username


@dataclass
class LoginThrottle:
    """Locks login for a while after repeated failures. In-memory: one process, one account."""

    max_failures: int = 5
    lockout_seconds: float = 300.0
    _failures: int = 0
    _locked_until: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def is_locked(self, now: float) -> bool:
        with self._lock:
            return now < self._locked_until

    def record_failure(self, now: float) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._locked_until = now + self.lockout_seconds
                self._failures = 0

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
