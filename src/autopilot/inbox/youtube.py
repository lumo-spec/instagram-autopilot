"""YouTube Data API v3 client (standard library only) and the one-time login on the owner's PC.

The OAuth client id/secret and the refresh token live in the git-ignored ``.env`` file; the
owner types them (or signs in) in their own terminal. Errors carry only the HTTP status and
Google's short reason word (e.g. ``quotaExceeded``), never response bodies or tokens.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from autopilot.errors import PublishError, TransientPublishError

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (an address, not a secret)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
# Upload + read own channel (for reconciling an interrupted upload).
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TRANSIENT_REASONS = {"backendError", "internalError", "rateLimitExceeded"}


def _reason(exc: urllib.error.HTTPError) -> str:
    try:
        err = json.loads(exc.read().decode()).get("error", {})
        if isinstance(err, str):  # token endpoint: {"error": "invalid_grant"}
            return err if err.replace("_", "").isalpha() else "unknown"
        reasons = [str(e.get("reason", "")) for e in err.get("errors", [])]
        word = next((r for r in reasons if r.isalnum()), "")
        return word or "unknown"
    except (ValueError, AttributeError, OSError):
        return "unknown"


def _open(
    req: urllib.request.Request, what: str, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        reason = _reason(exc)
        msg = f"YouTube {what}: HTTP {exc.code} ({reason})"
        if exc.code >= 500 or exc.code == 429 or reason in TRANSIENT_REASONS:
            raise TransientPublishError(msg) from None
        raise PublishError(msg) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TransientPublishError(f"network error during YouTube {what}") from None


class YouTubeApi:
    """Real client for the owner's channel."""

    def __init__(
        self, client_id: str, client_secret: str, refresh_token: str, timeout: float = 120
    ) -> None:
        self._id = client_id
        self._secret = client_secret
        self._refresh = refresh_token
        self._token = ""
        self._timeout = timeout

    def __repr__(self) -> str:
        return "YouTubeApi(<redacted>)"

    def _access(self) -> str:
        if not self._token:
            body = urllib.parse.urlencode(
                {
                    "client_id": self._id,
                    "client_secret": self._secret,
                    "refresh_token": self._refresh,
                    "grant_type": "refresh_token",
                }
            ).encode()
            req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
            _, _, data = _open(req, "sign-in", 30)
            self._token = str(json.loads(data)["access_token"])
        return self._token

    def _json(self, method: str, url: str, body: dict[str, Any] | None, what: str) -> Any:
        headers = {"Authorization": f"Bearer {self._access()}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
        _, _, raw = _open(req, what, 30)
        return json.loads(raw) if raw else {}

    def upload(self, video: Path, title: str, description: str, synthetic: bool) -> str:
        """Resumable upload in one go; returns the new video id. Published as public."""
        meta = {
            "snippet": {"title": title, "description": description, "categoryId": "27"},
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": synthetic,
            },
        }
        size = video.stat().st_size
        start = urllib.request.Request(  # noqa: S310
            f"{UPLOAD}/videos?uploadType=resumable&part=snippet,status",
            data=json.dumps(meta).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access()}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(size),
            },
        )
        _, headers, _ = _open(start, "upload start", 30)
        location = next((v for k, v in headers.items() if k.lower() == "location"), "")
        if not location.startswith(UPLOAD):
            raise TransientPublishError("YouTube upload start: no upload address returned")
        with video.open("rb") as f:
            put = urllib.request.Request(  # noqa: S310
                location,
                data=f,
                method="PUT",
                headers={
                    "Authorization": f"Bearer {self._access()}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                },
            )
            _, _, raw = _open(put, "upload", self._timeout + size / 500_000)
        return str(json.loads(raw)["id"])

    def recent_uploads(self, limit: int) -> list[dict[str, str]]:
        """Newest uploads of the channel (id and title)."""
        ch = self._json("GET", f"{API}/channels?part=contentDetails&mine=true", None, "channel")
        items = ch.get("items") or []
        if not items:
            raise PublishError("YouTube channel: none found for this sign-in")
        playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        q = urllib.parse.urlencode(
            {"part": "snippet", "playlistId": playlist, "maxResults": min(limit, 50)}
        )
        data = self._json("GET", f"{API}/playlistItems?{q}", None, "uploads list")
        return [
            {"id": str(i["snippet"]["resourceId"]["videoId"]), "title": str(i["snippet"]["title"])}
            for i in data.get("items", [])
        ]

    def set_thumbnail(self, video_id: str, image: Path) -> None:
        """Custom thumbnail (YouTube allows it on verified channels; 2 MB max)."""
        data = image.read_bytes()
        if len(data) > 2_000_000:
            from autopilot.inbox.destinations import cover_jpeg

            data, ctype = cover_jpeg(image), "image/jpeg"
        else:
            ctype = "image/png"
        q = urllib.parse.urlencode({"videoId": video_id})
        req = urllib.request.Request(  # noqa: S310
            f"{UPLOAD}/thumbnails/set?{q}",
            data=data,
            method="POST",
            headers={"Authorization": f"Bearer {self._access()}", "Content-Type": ctype},
        )
        _open(req, "thumbnail", 60)

    def channel_title(self) -> str:
        """The signed-in channel's name (for the owner to confirm the right channel)."""
        ch = self._json("GET", f"{API}/channels?part=snippet&mine=true", None, "channel")
        items = ch.get("items") or []
        return str(items[0]["snippet"]["title"]) if items else ""


def login(client_id: str, client_secret: str, open_browser: bool = True) -> str:
    """Sign in once in the browser (loopback + PKCE); returns the refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and go back to PowerShell.")

        def log_message(self, *args: object) -> None:  # keep the code out of the console
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": SCOPES,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    print("Opening Google sign-in in your browser. If it does not open, paste this address:")
    print(url)
    if open_browser:
        webbrowser.open(url)
    server.timeout = 600

    def serve() -> None:  # stray requests (e.g. favicon) do not end the wait
        for _ in range(10):
            server.handle_request()
            if "code" in got or "error" in got:
                return

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    thread.join(timeout=600)
    server.server_close()
    if "code" not in got:
        raise PublishError(f"YouTube sign-in did not finish ({got.get('error', 'no answer')})")
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": got["code"],
            "code_verifier": verifier,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }
    ).encode()
    _, _, data = _open(urllib.request.Request(TOKEN_URL, data=body, method="POST"), "sign-in", 30)
    token = json.loads(data).get("refresh_token", "")
    if not token:
        raise PublishError("YouTube sign-in: Google returned no refresh token; try again")
    return str(token)
