"""Minimal GitHub REST client (standard library only).

Used by the record, maintenance and renewal jobs. It never logs request headers, tokens or
response bodies; errors carry only the HTTP status, so they are safe to print in public logs.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from autopilot.errors import AutopilotError

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


class GitHubError(AutopilotError):
    """A GitHub API call failed (message holds only the HTTP status)."""

    def __init__(self, status: int, what: str) -> None:
        super().__init__(f"GitHub API HTTP {status} ({what})")
        self.status = status


@dataclass
class Response:
    """Status, parsed JSON body and headers (lower-case names)."""

    status: int
    data: Any
    headers: dict[str, str]


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    """Real HTTP transport."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return Response(resp.status, json.loads(raw) if raw else None, hdrs)
    except urllib.error.HTTPError as exc:
        return Response(exc.code, None, {k.lower(): v for k, v in exc.headers.items()})
    except (urllib.error.URLError, TimeoutError, OSError):
        raise GitHubError(0, "network error") from None


class GitHub:
    """Calls ``api.github.com`` for one repository with one token."""

    def __init__(self, token: str, repo: str, transport: Transport = urllib_transport) -> None:
        if not REPO_RE.match(repo):
            raise GitHubError(0, "invalid repository name")
        self._token = token
        self.repo = repo
        self._transport = transport

    def __repr__(self) -> str:  # never show the token
        return f"GitHub({self.repo}, <redacted>)"

    def call(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        ok: tuple[int, ...] = (200, 201, 204),
    ) -> Response:
        """``path`` is relative to the repository unless it starts with ``/``."""
        if path.startswith("/"):
            url = API + path
        else:
            url = f"{API}/repos/{self.repo}" + (f"/{path}" if path else "")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "instagram-autopilot",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        resp = self._transport(method, url, headers, data)
        if resp.status not in ok:
            raise GitHubError(resp.status, f"{method} {path.split('?', maxsplit=1)[0]}")
        return resp
