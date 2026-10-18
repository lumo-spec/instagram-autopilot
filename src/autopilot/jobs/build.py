"""``build`` job (no secrets, read-only token): pick the next approved post, render, gate.

Writes ``build.json`` plus the rendered files in ``media/`` for the ``host`` job. Media URLs are
left as ``pending://`` placeholders: only the host job (which holds the write token, but none of
this job's large dependency tree) puts files online.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from autopilot.config import Config, Page
from autopilot.content.queue import POST_ID_RE
from autopilot.errors import ValidationError
from autopilot.jobs.common import parse_state, state_path, write_json
from autopilot.manifest import SLOT_KEY_RE, media_names
from autopilot.ops.gitstore import read_branch_file
from autopilot.state import STATUS_FAILED, STATUS_INTENT, PageState

ON_DEMAND_RE = re.compile(r"-now\d{6}$")
FORMAT_CHOICES = {
    "auto": "any",
    "any": "any",
    "image": "image",
    "carousel": "carousel",
    "reel": "reel",
}


def on_demand_key(cfg: Config, page: Page, now: datetime) -> str:
    """Slot key for a Post now run, e.g. ``simplestorymode-2026-10-18-now191205``."""
    local = now.astimezone(cfg.timezone)
    return f"{page.name}-{local.date().isoformat()}-now{local.strftime('%H%M%S')}"


def is_on_demand(slot_key: str) -> bool:
    """True for Post now slot keys."""
    return bool(ON_DEMAND_RE.search(slot_key))


def check_request(
    cfg: Config, page_name: str, slot_key: str, fmt: str, post_id: str
) -> tuple[Page, str, date]:
    """Re-validate what the plan job or the Post now form handed over."""
    page = cfg.page(page_name)
    if not page.enabled or not page.is_story:
        raise ValidationError(f"page {page_name} is not an enabled story page")
    if fmt not in FORMAT_CHOICES:
        raise ValidationError(f"format must be one of {sorted(FORMAT_CHOICES)}")
    if post_id and not POST_ID_RE.match(post_id):
        raise ValidationError("post id may only use a-z, 0-9 and dashes")
    if not SLOT_KEY_RE.match(slot_key) or not slot_key.startswith(page.name + "-"):
        raise ValidationError("invalid slot key")
    m = re.match(r"^\d{4}-\d{2}-\d{2}", slot_key[len(page.name) + 1 :])
    if m is None:
        raise ValidationError("invalid slot key date")
    return page, FORMAT_CHOICES[fmt], date.fromisoformat(m.group(0))


def previous_container(state: PageState, slot_key: str) -> str | None:
    """Container id of an earlier attempt at this slot ("" = attempt without one, None = none)."""
    prev = state.get(slot_key)
    if prev is None or prev.status not in (STATUS_INTENT, STATUS_FAILED):
        return None
    return prev.container_id


def run_build(
    cfg: Config,
    repo_dir: Path,
    work: Path,
    page_name: str,
    slot_key: str,
    fmt: str,
    post_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one slot; returns (and writes) the build.json hand-over."""
    from autopilot.pipeline import Request, StoryBuilder
    from autopilot.publish.hosting import DeferredHosting

    now = now or datetime.now(UTC)
    if not slot_key:
        slot_key = on_demand_key(cfg, cfg.page(page_name), now)
    page, kind, local_date = check_request(cfg, page_name, slot_key, fmt, post_id)
    branch = str(cfg.section("github", "state_branch"))
    state = parse_state(read_branch_file(repo_dir, branch, state_path(page.name)), page.name)
    builder = StoryBuilder(cfg, page, work / "render", DeferredHosting(work / "media"))
    result = builder.build(Request(slot_key, local_date, kind, post_id), state)
    data: dict[str, Any] = {
        "page": page.name,
        "slot_key": slot_key,
        "slot": slot_key.rsplit("-", 1)[1],
        "date": local_date.isoformat(),
        "on_demand": is_on_demand(slot_key),
        "status": "built" if result.manifest else "skipped",
        "reason": result.skipped_reason,
        "post_id": result.post.id if result.post else "",
        "format": result.manifest.format if result.manifest else "",
        "manifest": None,
        "media": [],
        "previous_container": previous_container(state, slot_key),
        "queue_left": result.queue_left,
        "changed": result.changed,
        "gates": [f"{label}: {report.summary()}" for label, report in result.gates],
    }
    if result.manifest is not None:
        m = result.manifest
        data["manifest"] = {
            "page": m.page,
            "slot_key": m.slot_key,
            "content_id": m.content_id,
            "format": m.format,
            "audio_mode": m.audio_mode,
            "fallback": m.fallback,
            "template": m.template,
            "caption": m.caption,
            "hashtags": list(m.hashtags),
            "media_urls": list(m.media_urls),
            "ai_generated": m.ai_generated,
        }
        data["media"] = media_names(slot_key, m.format, len(m.media_urls))
    write_json(work / "build.json", data)
    return data
