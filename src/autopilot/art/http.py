"""Tiny HTTP layer for the art APIs (standard library only, injectable for tests).

Errors carry only the HTTP status: response bodies may echo the request and are never shown.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autopilot.errors import ArtError

RETRY_STATUSES = (0, 429, 500, 502, 503, 504)  # 0 = network trouble
HINTS = {
    0: "network error",
    400: "request refused (check the model name in config.yaml)",
    401: "key not accepted",
    403: "key not allowed (is billing switched on?)",
    404: "model not found (check the model name in config.yaml)",
    429: "rate limit or quota reached",
}


@dataclass
class HttpResponse:
    """Status and raw body."""

    status: int
    body: bytes

    def json(self) -> Any:
        """Parsed JSON body (ArtError when it is not JSON)."""
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            raise ArtError("the art API returned something that is not JSON") from None


Transport = Callable[[str, str, dict[str, str], bytes | None], HttpResponse]


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> HttpResponse:
    """Real HTTPS transport (image generation can take a minute or two)."""
    if not url.startswith("https://"):
        raise ArtError("art API calls must use https")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
            return HttpResponse(resp.status, resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, b"")
    except (urllib.error.URLError, TimeoutError, OSError):
        return HttpResponse(0, b"")


def call(
    transport: Transport,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    what: str,
    waits: tuple[float, ...] = (5.0, 20.0, 60.0),
    sleep: Callable[[float], None] = time.sleep,
) -> HttpResponse:
    """One request, retried on rate limits, server errors and network trouble."""
    resp = HttpResponse(0, b"")
    for wait in (*waits, None):
        resp = transport(method, url, headers, body)
        if resp.status == 200:
            return resp
        if resp.status not in RETRY_STATUSES or wait is None:
            break
        sleep(wait)
    raise ArtError(f"{what}: HTTP {resp.status} ({HINTS.get(resp.status, 'server error')})")
