"""``pages/<page>/inbox.yaml`` -> typed settings; the folder path comes from ``.env``."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from autopilot.art.keys import read_env_file
from autopilot.config import ROOT
from autopilot.errors import InboxError

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
KINDS = ("short", "long")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Destination:
    """Where videos go and when (local times, optionally on certain weekdays only)."""

    name: str
    label: str
    enabled: bool
    kinds: tuple[str, ...]
    times: tuple[str, ...]
    weekdays: tuple[int, ...]  # 0 = Monday; empty = every day

    def runs_on(self, weekday: int) -> bool:
        """True when this destination posts on ``weekday``."""
        return not self.weekdays or weekday in self.weekdays


@dataclass(frozen=True)
class InboxConfig:
    """Settings of one video page."""

    page: str
    title: str
    tz: ZoneInfo
    root_env: str
    inbox: str
    pause_file: str
    cover: str
    copy: str
    settle_minutes: float
    short_max_seconds: float
    catchup_minutes: float
    max_attempts: int
    low_stock_days: int
    ai_generated: bool
    destinations: tuple[Destination, ...]

    def destination(self, name: str) -> Destination:
        """The destination called ``name``."""
        for d in self.destinations:
            if d.name == name:
                return d
        raise InboxError(f"unknown destination {name!r} (known: {self.names()})")

    def names(self) -> str:
        """Destination names, comma separated."""
        return ", ".join(d.name for d in self.destinations)

    @property
    def enabled(self) -> tuple[Destination, ...]:
        """Destinations that are switched on."""
        return tuple(d for d in self.destinations if d.enabled)


def _destination(name: str, raw: dict[str, Any]) -> Destination:
    kinds = tuple(str(k) for k in raw.get("kinds", ["short"]))
    times = tuple(str(t) for t in raw.get("times", []))
    days = [str(d).lower()[:3] for d in raw.get("weekdays", [])]
    if not times or any(not _TIME.match(t) for t in times) or list(times) != sorted(times):
        raise InboxError(f"{name}: times must be HH:MM values in time order")
    if not kinds or any(k not in KINDS for k in kinds):
        raise InboxError(f"{name}: kinds must be among {KINDS}")
    if any(d not in WEEKDAYS for d in days):
        raise InboxError(f"{name}: weekdays must be among {WEEKDAYS}")
    return Destination(
        name=name,
        label=str(raw.get("label", name)),
        enabled=bool(raw.get("enabled", False)),
        kinds=kinds,
        times=times,
        weekdays=tuple(WEEKDAYS.index(d) for d in days),
    )


def load_inbox_config(page: str, pages_dir: Path | None = None) -> InboxConfig:
    """Read and check ``pages/<page>/inbox.yaml``."""
    path = (pages_dir or ROOT / "pages") / page / "inbox.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InboxError(f"cannot read {path.name} for {page}: {exc}") from None
    if not isinstance(raw, dict):
        raise InboxError(f"{path.name} for {page} is not a mapping")
    dests = tuple(_destination(str(n), dict(v)) for n, v in (raw.get("destinations") or {}).items())
    if not dests:
        raise InboxError(f"{page}: no destinations")
    return InboxConfig(
        page=page,
        title=str(raw.get("title", page)),
        tz=ZoneInfo(str(raw.get("timezone", "Europe/London"))),
        root_env=str(raw.get("root_env", f"{page.upper()}_DIR")),
        inbox=str(raw.get("inbox", "postthem")),
        pause_file=str(raw.get("pause_file", "PAUSE.txt")),
        cover=str(raw.get("cover", "cover.png")),
        copy=str(raw.get("copy", "publication-copy.txt")),
        settle_minutes=float(raw.get("settle_minutes", 10)),
        short_max_seconds=float(raw.get("short_max_seconds", 180)),
        catchup_minutes=float(raw.get("catchup_minutes", 90)),
        max_attempts=int(raw.get("max_attempts", 3)),
        low_stock_days=int(raw.get("low_stock_days", 3)),
        ai_generated=bool(raw.get("ai_generated", True)),
        destinations=dests,
    )


def resolve_root(cfg: InboxConfig, env_file: Path | None = None) -> Path:
    """The page's folder on this PC (environment first, then ``.env``)."""
    value = os.environ.get(cfg.root_env, "").strip() or read_env_file(
        env_file or ROOT / ".env"
    ).get(cfg.root_env, "")
    if not value:
        raise InboxError(
            f"{cfg.root_env} is not set: add a line {cfg.root_env}=<folder> to the .env file "
            "in the project folder"
        )
    root = Path(value)
    if not (root / cfg.inbox).is_dir():
        raise InboxError(f"the {cfg.inbox} folder was not found inside {cfg.root_env}")
    return root
