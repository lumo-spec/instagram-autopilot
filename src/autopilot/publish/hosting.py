"""Temporary public media hosting on the repository's own ``media`` branch.

Files live at ``<page>/<slot-key>[-NN].<ext>`` so pages never overwrite each other. JPEGs are
served from ``raw.githubusercontent.com`` at the exact commit that added them (immutable, so a
CDN cache can never serve an older file). raw.githubusercontent.com serves MP4 as
``application/octet-stream``, so reels are served by GitHub Pages (built from the same branch),
which sends ``video/mp4``. Before publishing, every URL must return HTTP 200 with the right
content type and length (and, for video, range support).
"""

from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autopilot.errors import AutopilotError
from autopilot.ops.gitstore import commit_and_push, git

CONTENT_TYPES = {".jpg": "image/jpeg", ".mp4": "video/mp4"}


class HostingError(AutopilotError):
    """Media could not be hosted or is not reachable."""


class DeferredHosting:
    """Build-job hosting: collects files for the ``host`` job (which holds the write token)."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    def host(self, page: str, name: str, path: Path) -> str:
        dest = self.out_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return f"pending://{page}/{name}"

    def remove(self, page: str, name: str) -> None:
        (self.out_dir / name).unlink(missing_ok=True)


def media_url(repo: str, commit: str, page: str, name: str) -> str:
    """Public URL of a hosted file."""
    owner, repo_name = repo.split("/", 1)
    if name.endswith(".mp4"):
        return f"https://{owner.lower()}.github.io/{repo_name}/{page}/{name}"
    return f"https://raw.githubusercontent.com/{owner}/{repo_name}/{commit}/{page}/{name}"


def push_media(worktree: Path, page: str, files: list[tuple[str, Path]], message: str) -> str:
    """Add files under ``<page>/`` on the media branch; returns the commit holding them."""

    def apply(root: Path) -> None:
        (root / ".nojekyll").touch()  # GitHub Pages: serve files as they are
        for name, src in files:
            dest = root / page / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    sha = commit_and_push(worktree, "media", message, apply)
    if not sha:  # identical files already hosted (a retry): use the current tip
        sha = git(worktree, "rev-parse", "HEAD").stdout.strip()
    return sha


@dataclass
class Probe:
    """What a URL returned."""

    status: int
    content_type: str
    length: int
    range_status: int


def http_probe(url: str) -> Probe:
    """HEAD the URL, then ask for its first byte (range support)."""
    try:
        head = urllib.request.Request(url, method="HEAD")  # noqa: S310
        with urllib.request.urlopen(head, timeout=20) as resp:  # noqa: S310
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
            length = int(resp.headers.get("Content-Length", "-1"))
            status = resp.status
        rng = urllib.request.Request(url, headers={"Range": "bytes=0-0"})  # noqa: S310
        with urllib.request.urlopen(rng, timeout=20) as resp:  # noqa: S310
            range_status = resp.status
    except urllib.error.HTTPError as exc:
        return Probe(exc.code, "", -1, 0)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return Probe(0, "", -1, 0)
    return Probe(status, ctype, length, range_status)


def verify_url(
    url: str,
    path: Path,
    timeout: float,
    probe: Callable[[str], Probe] = http_probe,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until ``url`` serves ``path`` correctly, or raise HostingError."""
    want_type = CONTENT_TYPES[path.suffix]
    size = path.stat().st_size
    deadline = clock() + timeout
    last = "no answer"
    while True:
        p = probe(url)
        if p.status == 200 and p.content_type == want_type and p.length == size:
            if want_type != "video/mp4" or p.range_status == 206:
                return
            last = "no range support"
        else:
            last = f"HTTP {p.status}, {p.content_type or 'no type'}, {p.length} bytes"
        if clock() >= deadline:
            raise HostingError(f"{path.name} not served correctly ({last})")
        sleep(10)
