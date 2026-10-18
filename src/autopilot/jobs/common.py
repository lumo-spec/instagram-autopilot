"""Small helpers shared by the job entry points: run context, JSON hand-over files, outputs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopilot.config import Config
from autopilot.errors import ValidationError
from autopilot.ops.github import REPO_RE
from autopilot.state import PageState

MAX_HANDOVER_BYTES = 200_000


@dataclass(frozen=True)
class RunContext:
    """Facts about the current workflow run (all public)."""

    repo: str
    run_url: str
    summary_path: Path | None
    output_path: Path | None

    @classmethod
    def from_env(cls, cfg: Config) -> RunContext:
        """Read the GitHub Actions environment (falls back to config for local runs)."""
        repo = os.environ.get("GITHUB_REPOSITORY") or str(cfg.section("github", "repo"))
        if not REPO_RE.match(repo):
            raise ValidationError("invalid repository name")
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id.isdigit() else ""
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        output = os.environ.get("GITHUB_OUTPUT")
        return cls(
            repo, run_url, Path(summary) if summary else None, Path(output) if output else None
        )

    def output(self, name: str, value: str) -> None:
        """Set a step output (single-line values only)."""
        if "\n" in value:
            raise ValidationError("step outputs must be one line")
        if self.output_path:
            with self.output_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{name}={value}\n")

    def summary(self, markdown: str) -> None:
        """Append to the run page summary (public)."""
        if self.summary_path:
            with self.summary_path.open("a", encoding="utf-8") as fh:
                fh.write(markdown.rstrip() + "\n\n")


def read_json(path: Path) -> dict[str, Any] | None:
    """A hand-over file from another job (None when absent); size-limited and must be an object."""
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_HANDOVER_BYTES:
        raise ValidationError(f"{path.name} is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError(f"{path.name} must hold a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a hand-over file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def state_path(page: str) -> str:
    """Path of a page's state file on the state branch."""
    return f"pages/{page}/state.json"


def parse_state(text: str | None, page: str) -> PageState:
    """State from the state branch (empty when the page has none yet)."""
    if not text:
        return PageState(page)
    state = PageState.from_dict(json.loads(text))
    if state.page != page:
        raise ValidationError("state file belongs to another page")
    return state
