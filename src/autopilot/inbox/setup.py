"""Keys for the inbox (git-ignored ``.env``) and building the real publishers from them.

Names in ``.env`` (``<PAGE>`` = upper-case page name, e.g. WELLITSTARTED):
``<PAGE>_IG_TOKEN``, ``<PAGE>_IG_USER_ID``, ``<PAGE>_IG_TOKEN_SET`` (date, for renewal),
``<PAGE>_YT_CLIENT_ID``, ``<PAGE>_YT_CLIENT_SECRET``, ``<PAGE>_YT_REFRESH_TOKEN``.
Values are never printed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from autopilot.art.keys import read_env_file
from autopilot.config import ROOT, load_config
from autopilot.errors import InboxError, PublishError
from autopilot.inbox.config import InboxConfig
from autopilot.inbox.destinations import (
    InstagramReels,
    NoHost,
    Publisher,
    ReelClient,
    VideoHost,
    YouTubeVideos,
)
from autopilot.inbox.youtube import YouTubeApi

RENEW_AFTER_DAYS = 30  # Instagram tokens last 60 days; renew half way


def env_file() -> Path:
    """The project's ``.env`` file."""
    return Path(os.environ.get("AP_ENV_FILE", str(ROOT / ".env")))


def get(name: str) -> str:
    """A value from the environment or ``.env`` ("" when missing)."""
    return os.environ.get(name, "").strip() or read_env_file(env_file()).get(name, "")


def set_values(values: dict[str, str], path: Path | None = None) -> None:
    """Add or replace ``NAME=value`` lines in ``.env``, keeping every other line."""
    path = path or env_file()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    todo = dict(values)
    out = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if "=" in line and name in todo:
            out.append(f"{name}={todo.pop(name)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in todo.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def prefix(cfg: InboxConfig) -> str:
    """``WELLITSTARTED`` for page ``wellitstarted``."""
    return cfg.page.upper().replace("-", "_")


def _graph() -> tuple[str, str]:
    app = load_config()
    return str(app.section("graph_api_host")), str(app.section("graph_api_version"))


def instagram_user_id(token: str) -> str:
    """Look up the account id for ``token`` (kept in .env only, never printed)."""
    host, version = _graph()
    req = urllib.request.Request(
        f"https://{host}/{version}/me?fields=user_id",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https host)
            return str(json.loads(resp.read())["user_id"])
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        raise PublishError("the Instagram token did not work (check it and try again)") from None


def renew_instagram_token(cfg: InboxConfig, today: date) -> bool:
    """Refresh the long-lived token when it is 30+ days old; True when renewed."""
    p = prefix(cfg)
    token, set_on = get(f"{p}_IG_TOKEN"), get(f"{p}_IG_TOKEN_SET")
    if not token or (set_on and (today - date.fromisoformat(set_on)).days < RENEW_AFTER_DAYS):
        return False
    host, _ = _graph()
    q = urllib.parse.urlencode({"grant_type": "ig_refresh_token"})
    req = urllib.request.Request(
        f"https://{host}/refresh_access_token?{q}", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https host)
            new = str(json.loads(resp.read())["access_token"])
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        raise PublishError("Instagram token renewal failed; the old token is kept") from None
    set_values({f"{p}_IG_TOKEN": new, f"{p}_IG_TOKEN_SET": today.isoformat()})
    return True


def build_publishers(cfg: InboxConfig, host: VideoHost | None = None) -> dict[str, Publisher]:
    """Real publishers for the enabled destinations (missing keys -> clear set-up error)."""
    p = prefix(cfg)
    app = load_config()
    out: dict[str, Publisher] = {}
    for dest in cfg.enabled:
        if dest.name.startswith("instagram"):
            token, user = get(f"{p}_IG_TOKEN"), get(f"{p}_IG_USER_ID")
            if not token or not user:
                raise InboxError(
                    f"Instagram is not connected for {cfg.title}: run "
                    f"python -m autopilot inbox instagram-token {cfg.page} in your own window"
                )
            host_name, version = _graph()
            out[dest.name] = InstagramReels(
                cfg.page,
                ReelClient(host_name, version, user, token),
                host or NoHost(),
                cfg.ai_generated,
                [float(x) for x in app.section("publish", "container_poll_seconds")],
                int(app.section("publish", "publish_retries")),
            )
        elif dest.name.startswith("youtube"):
            cid, secret, refresh = (
                get(f"{p}_YT_CLIENT_ID"),
                get(f"{p}_YT_CLIENT_SECRET"),
                get(f"{p}_YT_REFRESH_TOKEN"),
            )
            if not (cid and secret and refresh):
                raise InboxError(
                    f"YouTube is not connected for {cfg.title}: run "
                    f"python -m autopilot inbox youtube-login {cfg.page} in your own window"
                )
            out[dest.name] = YouTubeVideos(
                YouTubeApi(cid, secret, refresh), "long" not in dest.kinds, cfg.ai_generated
            )
        else:
            raise InboxError(f"no publisher for destination {dest.name}")
    return out
