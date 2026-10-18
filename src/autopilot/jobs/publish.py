"""``publish`` job (environment ``page-<name>``; the only job with the Instagram token).

Runs in two phases so a crash can never cause a double post:

* ``prepare``: re-validate everything, check the quota, reconcile any earlier attempt (container
  status and recent media), else create the container(s). Writes ``intent.json`` (container id),
  which the workflow uploads immediately, before phase 2 starts.
* ``finish``: wait for the container, publish once, fetch the permalink -> ``publish.json``.

Logs say only what happened, never IDs, tokens or API responses.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autopilot.config import Config
from autopilot.errors import PublishError, ValidationError
from autopilot.jobs.common import read_json, write_json
from autopilot.manifest import Manifest, validate_manifest
from autopilot.publish.hosting import CONTENT_TYPES
from autopilot.publish.instagram import (
    GraphClient,
    InstagramClient,
    finish_publish,
    prepare_container,
)

CONTAINER_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")  # Meta ids are digits; path-safe either way
USER_ID_RE = re.compile(r"^\d{1,32}$")


def head_status(url: str) -> tuple[int, str]:
    """HTTP status and content type of a hosted file."""
    try:
        req = urllib.request.Request(url, method="HEAD")  # noqa: S310
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return resp.status, resp.headers.get("Content-Type", "").split(";")[0].strip()
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def client_from_env(cfg: Config) -> GraphClient:
    """Instagram client from the environment secrets (validated, never printed)."""
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    user = os.environ.get("IG_USER_ID", "")
    if not token or not USER_ID_RE.match(user):
        raise ValidationError("Instagram secrets are not set for this page (see README step 5)")
    return GraphClient(
        str(cfg.section("graph_api_host")), str(cfg.section("graph_api_version")), user, token
    )


def load_manifest(cfg: Config, work: Path, repo: str) -> tuple[dict[str, Any], Manifest] | None:
    """The build and the final manifest, re-validated (None when there is nothing to publish)."""
    build = read_json(work / "build.json")
    hosted = read_json(work / "hosted.json")
    if build is None or hosted is None or hosted.get("status") != "hosted":
        return None
    limits = cfg.section("limits")
    manifest = validate_manifest(
        hosted["manifest"], repo, int(limits["caption_max_chars"]), int(limits["max_hashtags"])
    )
    if manifest.slot_key != build.get("slot_key") or manifest.content_id != build.get("post_id"):
        raise ValidationError("hosted manifest does not match the build")
    prev = build.get("previous_container")
    if prev is not None and (not isinstance(prev, str) or (prev and not CONTAINER_RE.match(prev))):
        raise ValidationError("invalid previous container")
    return build, manifest


def _result(work: Path, slot_key: str, status: str, **extra: Any) -> dict[str, Any]:
    data = {"slot_key": slot_key, "status": status, **extra}
    write_json(work / "publish.json", data)
    print(f"publish: {status}")
    return data


def run_prepare(
    cfg: Config,
    work: Path,
    repo: str,
    client: InstagramClient | None = None,
    probe: Callable[[str], tuple[int, str]] = head_status,
) -> dict[str, Any]:
    """Phase 1. Returns the publish.json data when the slot is settled here, else intent.json."""
    loaded = load_manifest(cfg, work, repo)
    if loaded is None:
        return _result(work, "", "not_run", reason="nothing was built or hosted")
    build, manifest = loaded
    for url in manifest.media_urls:
        status, ctype = probe(url)
        if status != 200 or ctype != CONTENT_TYPES["." + url.rsplit(".", 1)[1]]:
            return _result(work, manifest.slot_key, "failed", error="hosted media not reachable")
    client = client or client_from_env(cfg)
    pub = cfg.section("publish")
    delays = [float(d) for d in pub["container_poll_seconds"]]
    try:
        if client.publishing_usage() >= int(cfg.section("limits", "quota_stop_at")):
            return _result(work, manifest.slot_key, "quota")
        # Always look at recent media when no container is known: captions are unique in the
        # queue, so a match can only be this very post (e.g. a run whose record step was lost).
        previous = build["previous_container"] or ""
        prepared = prepare_container(
            client, manifest, previous, delays, int(pub["publish_retries"])
        )
    except PublishError as exc:
        return _result(work, manifest.slot_key, "failed", error=str(exc))
    if prepared.outcome is not None:
        o = prepared.outcome
        return _result(
            work,
            manifest.slot_key,
            "published",
            container_id=o.container_id,
            media_id=o.media_id,
            permalink=o.permalink,
            reconciled=True,
        )
    intent = {"slot_key": manifest.slot_key, "container_id": prepared.container_id}
    write_json(work / "intent.json", intent)
    print("publish: container ready; intent recorded")
    return intent


def run_finish(
    cfg: Config, work: Path, repo: str, client: InstagramClient | None = None
) -> dict[str, Any]:
    """Phase 2: publish the container named in intent.json."""
    done = read_json(work / "publish.json")
    if done is not None:
        return done  # settled in phase 1
    intent = read_json(work / "intent.json")
    loaded = load_manifest(cfg, work, repo)
    if intent is None or loaded is None:
        return _result(work, "", "not_run", reason="no intent")
    _, manifest = loaded
    container = str(intent.get("container_id", ""))
    if intent.get("slot_key") != manifest.slot_key or not CONTAINER_RE.match(container):
        raise ValidationError("intent.json does not match the manifest")
    client = client or client_from_env(cfg)
    pub = cfg.section("publish")
    delays = [float(d) for d in pub["container_poll_seconds"]]
    try:
        o = finish_publish(client, manifest, container, delays, int(pub["publish_retries"]))
    except PublishError as exc:
        return _result(work, manifest.slot_key, "failed", container_id=container, error=str(exc))
    return _result(
        work,
        manifest.slot_key,
        "published",
        container_id=o.container_id,
        media_id=o.media_id,
        permalink=o.permalink,
        reconciled=False,
    )
