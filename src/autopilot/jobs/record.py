"""``record`` job (no secrets): write the slot's outcome, clean up, alert, update the status page.

Runs after every posting run, even when an earlier job failed or the runner died. It reads the
hand-over files that exist (build, hosted, intent, publish) and decides what really happened:
an intent without a publish result is kept as an intent, so the next run reconciles it instead
of posting again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autopilot.config import Config
from autopilot.errors import ValidationError
from autopilot.jobs.build import is_on_demand
from autopilot.jobs.common import RunContext, parse_state, read_json, state_path
from autopilot.manifest import SLOT_KEY_RE, media_names
from autopilot.ops import alerts
from autopilot.ops.alerts import AlertPlan
from autopilot.ops.github import GitHub
from autopilot.ops.gitstore import commit_and_push, open_worktree
from autopilot.ops.issues import sync
from autopilot.ops.status import render_status
from autopilot.state import (
    STATUS_FAILED,
    STATUS_INTENT,
    STATUS_PUBLISHED,
    STATUS_SKIPPED,
    PageState,
    PostRecord,
)

RENEWAL_FILE = "status/renewal.json"


@dataclass
class Outcome:
    """What the run achieved for one slot."""

    record: PostRecord
    plan: AlertPlan
    hosted_names: list[str] = field(default_factory=list)  # files to remove from media
    summary: str = ""


def decide(
    cfg: Config,
    build: dict[str, Any],
    hosted: dict[str, Any] | None,
    intent: dict[str, Any] | None,
    publish: dict[str, Any] | None,
    now: datetime,
    publish_job: str = "",
) -> Outcome:
    """Turn the hand-over files into a state record and alerts.

    ``publish_job`` is the publish job's result; "skipped" with no hand-over files means the
    PUBLISHING_ENABLED safety switch is off (setup not finished): recorded, no alert.
    """
    page = cfg.page(str(build["page"]))
    key = str(build["slot_key"])
    if not SLOT_KEY_RE.match(key) or not key.startswith(page.name + "-"):
        raise ValidationError("invalid slot key in build.json")
    on_demand = is_on_demand(key)
    slot = "on-demand" if on_demand else key.rsplit("-", 1)[1]
    title, slug = page.title, alerts.page_slug(page.title)
    plan = AlertPlan(scopes=[f"approval-changed-{slug}-"])
    for post_id in build.get("changed", []):
        plan.add(alerts.approval_changed(title, str(post_id)))
    stamp = now.astimezone(cfg.timezone).isoformat(timespec="minutes")
    rec = PostRecord(
        key, str(build["date"]), key.rsplit("-", 1)[1], STATUS_FAILED, recorded_at=stamp
    )
    if build.get("status") != "built":
        rec.status, rec.fallback = STATUS_SKIPPED, "skipped"
        reason = str(build.get("reason", "skipped"))
        if reason.startswith("queue empty"):
            plan.add(alerts.queue_empty(title, slot))
        else:
            plan.add(alerts.slot_skipped(title, slot, reason))
        return Outcome(rec, plan, summary=f"Skipped: {reason}. Nothing was posted.")
    m = build["manifest"]
    rec.format, rec.audio_mode, rec.fallback = m["format"], m["audio_mode"], m["fallback"]
    rec.content_id, rec.template, rec.hashtags = m["content_id"], m["template"], list(m["hashtags"])
    rec.slides = len(m["media_urls"]) if m["format"] == "carousel" else 0
    names = media_names(key, m["format"], len(m["media_urls"]))
    queue_left = int(build.get("queue_left", 0))
    if queue_left < page.queue_low_warning:
        plan.add(alerts.queue_low(title, queue_left))
    else:
        plan.resolved.append(f"queue-low-{slug}")
    if hosted is None or hosted.get("status") != "hosted":
        plan.add(alerts.hosting_failed(title, slot))
        return Outcome(rec, plan, summary="Media hosting failed; nothing was posted.")
    plan.resolved.append(f"hosting-failed-{slug}-{slot}")
    status = (publish or {}).get("status", "")
    container = (publish or {}).get("container_id") or (intent or {}).get("container_id")
    rec.container_id = str(container or "")
    if status == "published":
        rec.status = STATUS_PUBLISHED
        rec.media_id = str(publish.get("media_id", "")) if publish else ""
        rec.permalink = str(publish.get("permalink", "")) if publish else ""
        plan.resolved += [
            f"queue-empty-{slug}",
            f"skipped-{slug}-{slot}",
            f"publish-failed-{slug}-{slot}",
        ]
        again = bool(publish and publish.get("reconciled"))
        note = " (it had already gone out; not posted again)" if again else ""
        text = f"Published {m['format']} {m['content_id']}{note}: {rec.permalink}"
        return Outcome(rec, plan, names, text)
    if status == "quota":
        return Outcome(rec, plan, summary="Publishing limit nearly reached; retried next run.")
    if publish_job == "skipped" and publish is None and intent is None:
        rec.status = STATUS_SKIPPED
        text = "Publishing is switched off (PUBLISHING_ENABLED is not true); nothing was posted."
        return Outcome(rec, plan, names, text)
    if intent is not None and status != "failed":
        rec.status = STATUS_INTENT  # the runner died mid-publish: the next attempt reconciles
    plan.add(alerts.publish_failed(title, slot, retried=not on_demand))
    return Outcome(rec, plan, summary="Not published this time; nothing was posted twice.")


def write_status(cfg: Config, root: Path, now: datetime) -> None:
    """Regenerate STATUS.md in a state-branch working copy."""
    states = {p.name: parse_state(_read(root / state_path(p.name)), p.name) for p in cfg.pages}
    renewal = json.loads(_read(root / RENEWAL_FILE) or "{}")
    text = render_status(
        cfg,
        states,
        now,
        bool(cfg.section("autopilot", "schedule_enabled")),
        str(renewal.get("pat_expires_on", "")),
    )
    (root / "STATUS.md").write_text(text, encoding="utf-8")


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def update_state(
    cfg: Config, worktree: Path, page: str, now: datetime, change: Any, message: str
) -> str:
    """Apply ``change(state)`` to a page's state on the state branch and refresh STATUS.md."""

    def apply(root: Path) -> None:
        path = root / state_path(page)
        state = parse_state(_read(path), page)
        change(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.to_json(), encoding="utf-8")
        write_status(cfg, root, now)

    return commit_and_push(worktree, str(cfg.section("github", "state_branch")), message, apply)


def remove_media(cfg: Config, worktree: Path, page: str, names: list[str], key: str) -> None:
    """Delete a published slot's hosted files (Instagram has its own copy now)."""

    def apply(root: Path) -> None:
        for name in names:
            (root / page / name).unlink(missing_ok=True)

    commit_and_push(worktree, str(cfg.section("github", "media_branch")), f"remove {key}", apply)


def run_record(
    cfg: Config,
    repo_dir: Path,
    work: Path,
    ctx: RunContext,
    gh: GitHub | None,
    now: datetime | None = None,
    publish_job: str = "",
) -> Outcome | None:
    """Record the run; returns None when the build never produced a hand-over."""
    now = now or datetime.now(UTC)
    build = read_json(work / "build.json")
    if build is None:
        print("record: no build.json (the build job failed); nothing to record")
        return None
    outcome = decide(
        cfg,
        build,
        read_json(work / "hosted.json"),
        read_json(work / "intent.json"),
        read_json(work / "publish.json"),
        now,
        publish_job,
    )
    rec = outcome.record
    state_tree = open_worktree(
        repo_dir, str(cfg.section("github", "state_branch")), work / "state-branch"
    )
    update_state(
        cfg, state_tree, build["page"], now, lambda s: s.upsert(rec), f"record {rec.slot_key}"
    )
    if outcome.hosted_names:
        media_tree = open_worktree(
            repo_dir, str(cfg.section("github", "media_branch")), work / "media-branch"
        )
        remove_media(cfg, media_tree, build["page"], outcome.hosted_names, rec.slot_key)
    if gh is not None:
        mention = str(cfg.section("github", "owner_mention"))
        result = sync(gh, outcome.plan, mention, ctx.run_url, now.date().isoformat())
        print(f"alerts: opened {len(result.opened)}, closed {len(result.closed)}")
    ctx.summary(_summary(build, outcome))
    print(f"record: {rec.slot_key} -> {rec.status}")
    return outcome


def _summary(build: dict[str, Any], outcome: Outcome) -> str:
    lines = [f"### {build['slot_key']}", "", outcome.summary, ""]
    hosted_preview = outcome.record.status == STATUS_PUBLISHED and outcome.hosted_names
    if hosted_preview and build.get("manifest"):
        lines.append(f"Post: {build['manifest']['content_id']} ({build['manifest']['format']})")
    for gate in build.get("gates", []):
        lines.append(f"- {gate}")
    alerts_raised = [a.title for a in outcome.plan.raise_]
    if alerts_raised:
        lines += ["", "Alerts:"] + [f"- {t}" for t in alerts_raised]
    return "\n".join(lines)


def page_states(cfg: Config, root: Path) -> dict[str, PageState]:
    """Every page's state from a state-branch working copy."""
    return {p.name: parse_state(_read(root / state_path(p.name)), p.name) for p in cfg.pages}
