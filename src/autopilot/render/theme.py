"""Visual templates for a page (``pages/<page>/theme.yaml``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from autopilot.errors import ConfigError
from autopilot.render.fonts import FAMILIES

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
BACKGROUNDS = ("gradient", "solid", "paper", "grain", "shapes", "vignette")
MOTIONS = ("zoom", "particles", "drift")
TEXT_ANIMS = ("word", "line")
MIN_TEMPLATES = 8

RGB = tuple[int, int, int]


def hex_to_rgb(value: str) -> RGB:
    """Convert ``#rrggbb`` to an RGB tuple."""
    if not HEX_RE.match(value):
        raise ConfigError(f"bad colour {value!r}")
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


@dataclass(frozen=True)
class FontSpec:
    """A font family, weight and style."""

    family: str
    weight: int
    italic: bool


@dataclass(frozen=True)
class Template:
    """One visual template."""

    name: str
    background: str
    colors: tuple[RGB, RGB]
    angle: float
    text_color: RGB
    accent_color: RGB
    font: FontSpec
    attribution_font: FontSpec
    motion: str
    text_anim: str


@dataclass(frozen=True)
class Theme:
    """All templates for a page plus shared styling."""

    templates: tuple[Template, ...]
    watermark_opacity: float

    def get(self, name: str) -> Template:
        """Template by name."""
        for t in self.templates:
            if t.name == name:
                return t
        raise ConfigError(f"unknown template {name}")


def _font(data: dict[str, object]) -> FontSpec:
    family = str(data["family"])
    if family not in FAMILIES:
        raise ConfigError(f"unknown font family {family}")
    return FontSpec(family, int(str(data.get("weight", 400))), bool(data.get("italic", False)))


@lru_cache(maxsize=8)
def load_theme(path: Path) -> Theme:
    """Load and validate a theme file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    templates = []
    for t in data["templates"]:
        bg = t["background"]
        if bg["kind"] not in BACKGROUNDS:
            raise ConfigError(f"template {t['name']}: unknown background {bg['kind']}")
        reel = t.get("reel", {})
        if reel.get("motion") not in MOTIONS or reel.get("text") not in TEXT_ANIMS:
            raise ConfigError(f"template {t['name']}: bad reel settings")
        c0, c1 = (hex_to_rgb(c) for c in bg["colors"])
        templates.append(
            Template(
                name=str(t["name"]),
                background=str(bg["kind"]),
                colors=(c0, c1),
                angle=float(bg.get("angle", 180)),
                text_color=hex_to_rgb(t["text_color"]),
                accent_color=hex_to_rgb(t["accent_color"]),
                font=_font(t["font"]),
                attribution_font=_font(t["attribution_font"]),
                motion=str(reel["motion"]),
                text_anim=str(reel["text"]),
            )
        )
    names = [t.name for t in templates]
    if len(templates) < MIN_TEMPLATES or len(set(names)) != len(names):
        raise ConfigError(f"{path}: need at least {MIN_TEMPLATES} uniquely named templates")
    return Theme(tuple(templates), float(data.get("watermark", {}).get("opacity", 0.85)))
