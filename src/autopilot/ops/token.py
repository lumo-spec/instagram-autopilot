"""Instagram token renewal with a sealed hand-off (spec section 12).

Three jobs, so no job ever holds both the Instagram token and the GitHub renewal key:

1. ``keys`` (environment ``renewal``, holds ``GH_SECRETS_PAT``): fetch each page environment's
   public key (not secret) and the renewal key's expiry date.
2. ``refresh`` (environment ``page-<name>``, holds the Instagram token): refresh the token,
   mask it, encrypt it to that public key with a libsodium sealed box; only ciphertext leaves.
3. ``store`` (environment ``renewal``): write the ciphertext into the page environment's
   ``IG_ACCESS_TOKEN`` secret. Only GitHub can decrypt it.

If anything fails the old token is kept (it stays valid until its own expiry) and an alert is
raised; the next run tries again.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from autopilot.errors import AutopilotError
from autopilot.ops.github import GitHub

REFRESH_URL = "https://{host}/refresh_access_token"
EXPIRY_HEADER = "github-authentication-token-expiration"
KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")  # base64 of a 32-byte Curve25519 key
KEY_ID_RE = re.compile(r"^[0-9]{1,30}$")


class RenewalError(AutopilotError):
    """A renewal step failed (message holds only an HTTP status / error code)."""


@dataclass(frozen=True)
class PublicKey:
    """A GitHub environment's secret-encryption key."""

    key_id: str
    key: str

    def __post_init__(self) -> None:
        if not KEY_ID_RE.match(self.key_id) or not KEY_RE.match(self.key):
            raise RenewalError("unexpected public key format")


def environment_key(gh: GitHub, env: str) -> PublicKey:
    """GET the environment's public key."""
    data = gh.call("GET", f"environments/{env}/secrets/public-key").data
    return PublicKey(str(data["key_id"]), str(data["key"]))


def pat_expiry(gh: GitHub) -> str:
    """Expiry date of the token ``gh`` uses (from GitHub's response header), or ""."""
    resp = gh.call("GET", "")
    raw = resp.headers.get(EXPIRY_HEADER, "")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else ""


Fetch = Callable[[str], tuple[int, dict[str, object]]]


def urllib_fetch(url: str) -> tuple[int, dict[str, object]]:
    """GET returning (status, JSON); the URL (which carries the token) is never logged."""
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, dict(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode()).get("error", {})
            return exc.code, {"error": {"code": int(err.get("code", 0))}}
        except (ValueError, AttributeError, OSError):
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


@dataclass(frozen=True)
class Refreshed:
    """A new long-lived token and its expiry date."""

    token: str
    expires_on: str

    def __repr__(self) -> str:  # never show the token
        return f"Refreshed(<redacted>, expires_on={self.expires_on})"


def refresh_token(host: str, token: str, today: date, fetch: Fetch = urllib_fetch) -> Refreshed:
    """Exchange a long-lived Instagram token for a fresh one (Meta: token must be 24 h old)."""
    query = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": token})
    status, data = fetch(f"{REFRESH_URL.format(host=host)}?{query}")
    if status != 200 or not isinstance(data.get("access_token"), str):
        err = data.get("error")
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        raise RenewalError(f"token refresh HTTP {status} (error code {code})")
    new = str(data["access_token"])
    if len(new) < 20 or any(c.isspace() for c in new):
        raise RenewalError("token refresh returned an unexpected token")
    seconds = int(str(data.get("expires_in", 0)) or 0)
    days = max(0, seconds // 86400)
    return Refreshed(new, (today + timedelta(days=days)).isoformat())


def seal(public_key: PublicKey, plaintext: str) -> str:
    """Encrypt for GitHub with a libsodium sealed box; returns base64 ciphertext."""
    from nacl import encoding, public

    key = public.PublicKey(public_key.key.encode(), encoding.Base64Encoder)
    sealed = public.SealedBox(key).encrypt(plaintext.encode())
    return base64.b64encode(sealed).decode()


def store_secret(gh: GitHub, env: str, name: str, key_id: str, encrypted: str) -> None:
    """PUT an encrypted environment secret."""
    if not KEY_ID_RE.match(key_id) or not re.match(r"^[A-Za-z0-9+/=]{40,4000}$", encrypted):
        raise RenewalError("invalid sealed token")
    gh.call(
        "PUT",
        f"environments/{env}/secrets/{name}",
        {"encrypted_value": encrypted, "key_id": key_id},
        ok=(201, 204),
    )
