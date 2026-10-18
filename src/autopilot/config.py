"""Load and validate ``config.yaml`` into typed, read-only settings objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from autopilot.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
PAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
SLOT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,20}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._]{1,30}$")
FORMATS = ("image", "reel")
PAGE_TYPES = ("quotes", "facts", "news_digest")
AUDIO_MODES = ("voice+music", "music", "voice")


@dataclass(frozen=True)
class Slot:
    """One daily posting slot of a page."""

    name: str
    time: str
    format: str

    @property
    def minutes(self) -> int:
        """Minutes after local midnight."""
        hh, mm = self.time.split(":")
        return int(hh) * 60 + int(mm)


@dataclass(frozen=True)
class Page:
    """A themed Instagram page (account)."""

    name: str
    enabled: bool
    type: str
    handle: str
    theme: str
    slots: tuple[Slot, ...]

    @property
    def title(self) -> str:
        """Display name used in alerts, e.g. ``Wisdom``."""
        return self.name.replace("_", " ").title()

    @property
    def dir(self) -> Path:
        """Folder holding this page's content files."""
        return ROOT / "pages" / self.name

    @property
    def handle_is_set(self) -> bool:
        """True once the owner has supplied the real handle."""
        return self.handle != "@CHANGE_ME"


@dataclass(frozen=True)
class SafeZones:
    """Fractions of the frame that text must stay clear of / within."""

    reel_top: float
    reel_bottom: float
    reel_sides: float
    image_width: float
    image_height: float


@dataclass(frozen=True)
class Gates:
    """Thresholds for the pre-post quality gates."""

    min_font_px: int
    min_contrast: float
    contrast_percentile: float
    watermark_min_px: int
    watermark_min_contrast: float
    voice_onset_s: tuple[float, float]
    voice_tail_min_s: float
    word_sync_ms: float
    loudness_lufs: tuple[float, float]
    true_peak_max_dbtp: float
    av_duration_tolerance_s: float
    speech_min_active_share: float


@dataclass(frozen=True)
class Config:
    """All settings; ``raw`` keeps the parsed YAML for rarely used sections."""

    raw: dict[str, Any]
    timezone: ZoneInfo
    pages: tuple[Page, ...]
    safe_zones: SafeZones
    gates: Gates

    def page(self, name: str) -> Page:
        """Return the page called ``name`` or raise ConfigError."""
        for page in self.pages:
            if page.name == name:
                return page
        raise ConfigError(f"unknown page: {name}")

    @property
    def enabled_pages(self) -> tuple[Page, ...]:
        """Pages that are switched on."""
        return tuple(p for p in self.pages if p.enabled)

    def section(self, *keys: str) -> Any:
        """Return a nested value from the raw YAML, raising ConfigError when absent."""
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                raise ConfigError(f"missing config key: {'.'.join(keys)}")
            node = node[key]
        return node


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(message)


def _pair(value: Any, name: str) -> tuple[float, float]:
    _require(isinstance(value, list) and len(value) == 2, f"{name} must be a [low, high] pair")
    low, high = float(value[0]), float(value[1])
    _require(low <= high, f"{name}: low must not exceed high")
    return low, high


def _parse_page(name: str, data: dict[str, Any]) -> Page:
    _require(bool(PAGE_NAME_RE.match(name)), f"invalid page name: {name!r}")
    _require(data.get("type") in PAGE_TYPES, f"page {name}: type must be one of {PAGE_TYPES}")
    handle = str(data.get("handle", ""))
    _require(
        handle == "@CHANGE_ME" or bool(HANDLE_RE.match(handle)), f"page {name}: invalid handle"
    )
    slots: list[Slot] = []
    for slot_name, slot in (data.get("slots") or {}).items():
        _require(bool(SLOT_NAME_RE.match(slot_name)), f"page {name}: bad slot name {slot_name!r}")
        _require(bool(TIME_RE.match(str(slot.get("time", "")))), f"{name}.{slot_name}: bad time")
        _require(slot.get("format") in FORMATS, f"{name}.{slot_name}: format must be image|reel")
        slots.append(Slot(slot_name, str(slot["time"]), str(slot["format"])))
    _require(bool(slots), f"page {name}: needs at least one slot")
    slots.sort(key=lambda s: s.minutes)
    return Page(
        name=name,
        enabled=bool(data.get("enabled", False)),
        type=str(data["type"]),
        handle=handle,
        theme=str(data.get("theme", name)),
        slots=tuple(slots),
    )


def parse_config(raw: dict[str, Any]) -> Config:
    """Validate a parsed config mapping and build a :class:`Config`."""
    _require(isinstance(raw, dict), "config root must be a mapping")
    try:
        tz = ZoneInfo(str(raw["timezone"]))
    except Exception as exc:
        raise ConfigError("invalid or missing timezone") from exc
    pages = tuple(_parse_page(n, d) for n, d in (raw.get("pages") or {}).items())
    _require(bool(pages), "config defines no pages")
    weights = raw.get("audio", {}).get("weights", {})
    _require(set(weights) <= set(AUDIO_MODES), "audio.weights has unknown modes")
    _require(
        abs(sum(float(v) for v in weights.values()) - 1.0) < 1e-6, "audio weights must sum to 1"
    )
    sz = raw.get("safe_zones", {})
    reel, image = sz.get("reel", {}), sz.get("image", {})
    safe = SafeZones(
        reel_top=float(reel["top"]),
        reel_bottom=float(reel["bottom"]),
        reel_sides=float(reel["sides"]),
        image_width=float(image["width"]),
        image_height=float(image["height"]),
    )
    _require(safe.reel_top + safe.reel_bottom < 0.8, "reel safe zones leave no room for text")
    g = raw.get("gates", {})
    gates = Gates(
        min_font_px=int(g["min_font_px"]),
        min_contrast=float(g["min_contrast"]),
        contrast_percentile=float(g["contrast_percentile"]),
        watermark_min_px=int(g["watermark_min_px"]),
        watermark_min_contrast=float(g["watermark_min_contrast"]),
        voice_onset_s=_pair(g["voice_onset_s"], "gates.voice_onset_s"),
        voice_tail_min_s=float(g["voice_tail_min_s"]),
        word_sync_ms=float(g["word_sync_ms"]),
        loudness_lufs=_pair(g["loudness_lufs"], "gates.loudness_lufs"),
        true_peak_max_dbtp=float(g["true_peak_max_dbtp"]),
        av_duration_tolerance_s=float(g["av_duration_tolerance_s"]),
        speech_min_active_share=float(g["speech_min_active_share"]),
    )
    return Config(raw=raw, timezone=tz, pages=pages, safe_zones=safe, gates=gates)


def load_config(path: Path | None = None) -> Config:
    """Read and validate ``config.yaml`` (default: repository root)."""
    path = path or ROOT / "config.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path.name}") from exc
    try:
        return parse_config(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid config: {exc}") from exc
