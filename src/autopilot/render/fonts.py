"""Open-licence fonts: checksum verification and loading of variable-font instances."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from autopilot.config import ROOT
from autopilot.errors import RenderError

FONT_DIR = ROOT / "assets" / "fonts"
FAMILIES = {
    "cormorant-garamond": ("CormorantGaramond-VF.ttf", "CormorantGaramond-Italic-VF.ttf"),
    "lora": ("Lora-VF.ttf", "Lora-Italic-VF.ttf"),
    "playfair-display": ("PlayfairDisplay-VF.ttf", "PlayfairDisplay-Italic-VF.ttf"),
    "inter": ("Inter-VF.ttf", "Inter-VF.ttf"),
}


def _expected_checksums() -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in (FONT_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.lstrip("*").strip()] = digest
    return sums


@lru_cache(maxsize=1)
def verify_fonts() -> None:
    """Check every font against its pinned SHA-256 and that a licence file sits beside it."""
    for rel, digest in _expected_checksums().items():
        path = FONT_DIR / rel
        if not path.exists():
            raise RenderError(f"font missing: {rel}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RenderError(f"font checksum mismatch: {rel}")
        if not (path.parent / "OFL.txt").exists():
            raise RenderError(f"font licence missing for {rel}")


def font_path(family: str, italic: bool) -> Path:
    """Path of the requested family's (italic) variable font."""
    try:
        regular, ital = FAMILIES[family]
    except KeyError as exc:
        raise RenderError(f"unknown font family: {family}") from exc
    return FONT_DIR / family / (ital if italic else regular)


@lru_cache(maxsize=256)
def load_font(
    family: str, size: int, weight: int = 400, italic: bool = False
) -> ImageFont.FreeTypeFont:
    """Load a font instance at ``size`` px with the given weight axis value."""
    verify_fonts()
    font = ImageFont.truetype(str(font_path(family, italic)), size=size)
    try:
        axes = font.get_variation_axes()
    except OSError:
        return font
    values: list[float] = []
    for axis in axes:
        name = axis.get("name", b"")
        label = name.decode() if isinstance(name, bytes) else str(name)
        lo, hi = float(axis["minimum"] or 0), float(axis["maximum"] or 0)
        target = float(axis["default"] or lo)
        if label.lower().startswith("weight"):
            target = float(weight)
        elif label.lower().startswith("optical"):
            target = float(size)
        values.append(min(max(target, lo), hi))
    font.set_variation_by_axes(values)
    return font
