"""``plan`` job (no secrets, read-only): which page/slot pairs are due right now."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from autopilot.config import Config
from autopilot.jobs.common import RunContext, parse_state, state_path
from autopilot.ops.gitstore import read_branch_file
from autopilot.plan import plan


def run_plan(
    cfg: Config, repo_dir: Path, ctx: RunContext, now: datetime | None = None
) -> list[dict[str, str]]:
    """Due slots as a job matrix (``slots`` output) plus ``any=true|false``."""
    now = now or datetime.now(UTC)
    branch = str(cfg.section("github", "state_branch"))
    states = {
        p.name: parse_state(read_branch_file(repo_dir, branch, state_path(p.name)), p.name)
        for p in cfg.enabled_pages
    }
    due = [
        {"page": d.page, "slot": d.slot, "format": d.format, "slot_key": d.slot_key}
        for d in plan(cfg, now, states)
    ]
    ctx.output("slots", json.dumps(due, separators=(",", ":")))
    ctx.output("any", "true" if due else "false")
    print(f"plan: {len(due)} slot(s) due: {', '.join(d['slot_key'] for d in due) or 'none'}")
    return due
