"""``host`` job (write token, minimal dependencies): put the built media online and check it.

Re-validates ``build.json``, pushes the files to the ``media`` branch under the page's own
folder, waits until every URL serves the right bytes, then writes the final manifest to
``hosted.json`` for the publish job.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from autopilot.config import Config
from autopilot.errors import ValidationError
from autopilot.jobs.common import read_json, write_json
from autopilot.manifest import media_names, validate_manifest
from autopilot.ops.gitstore import open_worktree
from autopilot.publish.hosting import media_url, push_media, verify_url

Verifier = Callable[[str, Path, float], None]


def run_host(
    cfg: Config, repo_dir: Path, work: Path, repo: str, verify: Verifier = verify_url
) -> dict[str, Any]:
    """Host the build's media; returns (and writes) hosted.json."""
    build = read_json(work / "build.json")
    if build is None or build.get("status") != "built":
        data: dict[str, Any] = {"status": "not_needed"}
        write_json(work / "hosted.json", data)
        return data
    draft = build.get("manifest")
    if not isinstance(draft, dict):
        raise ValidationError("build.json has no manifest")
    page, key, fmt = str(draft["page"]), str(draft["slot_key"]), str(draft["format"])
    cfg.page(page)  # must be a configured page
    names = [str(n) for n in build.get("media", [])]
    if names != media_names(key, fmt, len(names)) or len(names) != len(draft["media_urls"]):
        raise ValidationError("build.json media names do not match the slot")
    limits = cfg.section("limits")
    files = []
    for name in names:
        path = work / "media" / name
        cap = int(limits["reel_max_bytes" if name.endswith(".mp4") else "image_max_bytes"])
        if not path.is_file() or not 0 < path.stat().st_size <= cap:
            raise ValidationError(f"{name} is missing or too large")
        files.append((name, path))
    worktree = open_worktree(
        repo_dir, str(cfg.section("github", "media_branch")), work / "media-branch"
    )
    commit = push_media(worktree, page, files, f"host {key}")
    urls = [media_url(repo, commit, page, name) for name in names]
    timeout = float(cfg.section("hosting", "verify_timeout_seconds"))
    for url, (_, path) in zip(urls, files, strict=True):
        verify(url, path, timeout)
    final = {**draft, "media_urls": urls}
    manifest = validate_manifest(
        final, repo, int(limits["caption_max_chars"]), int(limits["max_hashtags"])
    )
    data = {"status": "hosted", "commit": commit, "manifest": final}
    write_json(work / "hosted.json", data)
    print(f"hosted {len(urls)} file(s) for {manifest.slot_key}; every URL checked")
    return data
