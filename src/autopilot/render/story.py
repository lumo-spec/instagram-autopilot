"""Story slides: the owner's text-free art, plus our words on a soft cream panel.

Text is set as large as it will go, in one size shared by every slide of a post (the first slide
may be larger). Where the art behind the words is busy, a feathered cream panel is faded in: the
lightest panel that still gives strong contrast (measured on the real pixels) is used.
Markup in slide text: a line break forces a new line, a blank line starts a new paragraph, and
``**words**`` are set in the accent colour.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from autopilot.config import Config
from autopilot.errors import RenderError
from autopilot.gates.visual import contrast_ratio
from autopilot.render.background import render_background
from autopilot.render.fonts import load_font
from autopilot.render.image import WATERMARK_FONT, draw_elements
from autopilot.render.layout import Box, PlacedWord, TextElement, display_text, wrap_balanced
from autopilot.render.layout import place_watermark as _place_watermark
from autopilot.render.theme import RGB, FontSpec, Template, hex_to_rgb

Word = tuple[str, bool]  # (text, accent)
Line = list[Word]


@dataclass(frozen=True)
class StoryStyle:
    """Typography and panel settings for story slides (``config.yaml`` -> ``story``)."""

    size: tuple[int, int]
    font: FontSpec
    cover_font: FontSpec
    px_range: tuple[int, int, int]  # max, min, step
    cover_px_max: int
    line_spacing: float
    paragraph_gap: float
    areas: dict[str, tuple[float, float]]
    text_color: RGB
    accent_color: RGB
    panel_color: RGB
    panel_opacities: tuple[float, ...]
    feather_px: int
    pad_px: int
    contrast_margin: float
    max_upscale: float
    watermark_px: int


def story_style(cfg: Config) -> StoryStyle:
    """Read the ``story`` section of the config."""
    s = cfg.section("story")
    fp, cp, panel = s["font_px"], s["cover_font_px"], s["panel"]

    def spec(d: dict[str, Any]) -> FontSpec:
        return FontSpec(str(d["family"]), int(d.get("weight", 400)), bool(d.get("italic", False)))

    return StoryStyle(
        size=(int(s["slide_size"][0]), int(s["slide_size"][1])),
        font=spec(s["font"]),
        cover_font=spec(s["cover_font"]),
        px_range=(int(fp["max"]), int(fp["min"]), int(fp["step"])),
        cover_px_max=int(cp["max"]),
        line_spacing=float(s["line_spacing"]),
        paragraph_gap=float(s["paragraph_gap"]),
        areas={k: (float(v[0]), float(v[1])) for k, v in s["text_area"].items()},
        text_color=hex_to_rgb(str(s["text_color"])),
        accent_color=hex_to_rgb(str(s["accent_color"])),
        panel_color=hex_to_rgb(str(panel["color"])),
        panel_opacities=tuple(float(o) for o in panel["opacities"]),
        feather_px=int(panel["feather_px"]),
        pad_px=int(panel["pad_px"]),
        contrast_margin=float(s["contrast_margin"]),
        max_upscale=float(s["art"]["max_upscale"]),
        watermark_px=int(cfg.section("render", "watermark_px")),
    )


# ----- art ------------------------------------------------------------------------------------
def open_art(path: Path) -> Image.Image:
    """The owner's art at full size, upright, as RGB (transparency flattened onto cream)."""
    try:
        with Image.open(path) as raw:
            img = ImageOps.exif_transpose(raw)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGBA", img.size, (247, 240, 225, 255))
                img = Image.alpha_composite(flat, img)
            return img.convert("RGB")
    except OSError as exc:
        raise RenderError(f"cannot read art {path.name}") from exc


def load_art(path: Path, size: tuple[int, int], focus: str) -> tuple[Image.Image, float]:
    """Scale and crop art to fill ``size``; returns (image, upscale factor needed)."""
    img = open_art(path)
    w, h = size
    scale = max(w / img.width, h / img.height)
    new = (max(w, math.ceil(img.width * scale)), max(h, math.ceil(img.height * scale)))
    img = img.resize(new, Image.Resampling.LANCZOS)
    left = (new[0] - w) // 2
    top = {"top": 0, "center": (new[1] - h) // 2, "bottom": new[1] - h}[focus]
    return img.crop((left, top, left + w, top + h)), scale


def placeholder_art(template: Template, size: tuple[int, int], seed: int) -> Image.Image:
    """Simple storybook-coloured stand-in used until the owner supplies real art."""
    w, h = size
    img = render_background(template, w, h, seed).convert("RGBA")
    rng = np.random.default_rng(seed)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    dark = tuple(max(0, round(c * 0.55)) for c in template.colors[1])
    for i in range(3):  # rolling hills
        base = h * (0.62 + 0.11 * i)
        amp, phase = h * rng.uniform(0.03, 0.07), rng.uniform(0, 6.28)
        pts = [(x, base + amp * math.sin(x / w * 6.28 * 0.8 + phase)) for x in range(0, w + 40, 40)]
        draw.polygon([*pts, (w, h), (0, h)], fill=(*dark, 90 + 50 * i))
    cx, cy, r = rng.uniform(0.2, 0.8) * w, h * 0.3, w * 0.07  # a low sun or moon
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(250, 226, 160, 120))
    layer = layer.filter(ImageFilter.GaussianBlur(3))
    return Image.alpha_composite(img, layer).convert("RGB")


# ----- text -----------------------------------------------------------------------------------
def parse_story_text(text: str) -> list[list[Line]]:
    """Paragraphs -> hard lines -> words with an accent flag (``**...**`` toggles accent)."""
    paragraphs: list[list[Line]] = [[]]
    accent = False
    for raw_line in display_text(text.strip()).split("\n"):
        if not raw_line.strip():
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        line: Line = []
        glue = False  # True when the next chunk continues the previous word (no space between)
        for part in re.split(r"(\*\*)", raw_line):
            if part == "**":
                accent = not accent
                continue
            if not part:
                continue
            pieces = part.split()
            if not pieces:
                glue = False
                continue
            if glue and line and not part[0].isspace():
                prev, prev_accent = line[-1]
                line[-1] = (prev + pieces[0], prev_accent)
                pieces = pieces[1:]
            line += [(p, accent) for p in pieces]
            glue = not part[-1].isspace()
        if line:
            paragraphs[-1].append(line)
    return [p for p in paragraphs if p]


def _wrap(
    paragraphs: list[list[Line]], font: ImageFont.FreeTypeFont, width: float
) -> list[tuple[Line, bool]] | None:
    """Wrapped lines, each with True when it starts a new paragraph (after the first)."""
    out: list[tuple[Line, bool]] = []
    space = font.getlength(" ")
    for pi, para in enumerate(paragraphs):
        for li, hard in enumerate(para):
            words = [w for w, _ in hard]
            lines = wrap_balanced([font.getlength(w) for w in words], space, width, words)
            if lines is None:
                return None
            for k, idxs in enumerate(lines):
                out.append(([hard[i] for i in idxs], pi > 0 and li == 0 and k == 0))
    return out


def _height(
    lines: list[tuple[Line, bool]], font: ImageFont.FreeTypeFont, px: int, s: StoryStyle
) -> float:
    ascent, descent = font.getmetrics()
    gaps = sum(1 for _, new_para in lines if new_para)
    return ascent + descent + (len(lines) - 1) * px * s.line_spacing + gaps * px * s.paragraph_gap


def fits(
    text: str, box: Box, spec: FontSpec, px: int, style: StoryStyle, keep_lines: bool = False
) -> bool:
    """True when ``text`` fits ``box`` at ``px`` (``keep_lines``: no written line may wrap)."""
    font = load_font(spec.family, px, spec.weight, spec.italic)
    paragraphs = parse_story_text(text)
    lines = _wrap(paragraphs, font, box[2] - box[0])
    if lines is None or _height(lines, font, px, style) > box[3] - box[1]:
        return False
    return not keep_lines or len(lines) == sum(len(p) for p in paragraphs)


def max_fit_px(
    text: str, box: Box, spec: FontSpec, hi: int, lo: int, step: int, style: StoryStyle
) -> int | None:
    """Largest size in [lo, hi] (stepping down from ``hi``) at which ``text`` fits.

    Sizes that keep every written line on one line are preferred; only if none is large
    enough may long lines wrap.
    """
    for keep in (True, False):
        for px in range(hi, lo - 1, -step):
            if fits(text, box, spec, px, style, keep):
                return px
    return None


def place_story_text(
    text: str, box: Box, spec: FontSpec, px: int, style: StoryStyle
) -> list[TextElement]:
    """Lay out ``text`` centred in ``box``; returns body (and accent) elements."""
    font = load_font(spec.family, px, spec.weight, spec.italic)
    lines = _wrap(parse_story_text(text), font, box[2] - box[0])
    if lines is None:
        raise RenderError("text does not fit")
    total = _height(lines, font, px, style)
    if total > box[3] - box[1]:
        raise RenderError("text does not fit")
    ascent, _ = font.getmetrics()
    space = font.getlength(" ")
    body = TextElement("body", spec, px, style.text_color)
    accent = TextElement("accent", spec, px, style.accent_color)
    y = box[1] + (box[3] - box[1] - total) / 2 + ascent
    cx = (box[0] + box[2]) / 2
    for n, (line, new_para) in enumerate(lines):
        if n:
            y += px * style.line_spacing + (px * style.paragraph_gap if new_para else 0)
        width = sum(font.getlength(w) for w, _ in line) + space * (len(line) - 1)
        x = cx - width / 2
        for word, is_accent in line:
            left, top, right, bottom = font.getbbox(word, anchor="ls")
            bbox = (int(x + left), int(y + top), int(x + right) + 1, int(y + bottom) + 1)
            (accent if is_accent else body).words.append(PlacedWord(word, x, y, bbox, n))
            x += font.getlength(word) + space
    return [el for el in (body, accent) if el.words]


# ----- geometry -------------------------------------------------------------------------------
def safe_box(cfg: Config, size: tuple[int, int]) -> Box:
    """Image safe zone (text and watermark must stay inside)."""
    w, h = size
    sz = cfg.safe_zones
    mx, my = (1 - sz.image_width) / 2, (1 - sz.image_height) / 2
    return (round(w * mx), round(h * my), round(w * (1 - mx)), round(h * (1 - my)))


def story_text_box(cfg: Config, style: StoryStyle, position: str) -> Box:
    """Where the words of a slide may go for a given position (top, middle, bottom)."""
    w, h = style.size
    safe = safe_box(cfg, style.size)
    inset = round(w * float(cfg.section("render", "text_inset")))
    top_f, bottom_f = style.areas[position]
    top = max(round(h * top_f), safe[1])
    bottom = min(round(h * bottom_f), round(safe[3] - style.watermark_px * 2.6))
    return (safe[0] + inset, top, safe[2] - inset, bottom)


# ----- panel ----------------------------------------------------------------------------------
def _union(elements: list[TextElement]) -> Box:
    boxes = [el.bbox for el in elements]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def panel_mask(size: tuple[int, int], box: Box, pad: int, feather: int) -> Image.Image:
    """Rounded, feathered mask around ``box`` expanded by ``pad``."""
    mask = Image.new("L", size, 0)
    rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    ImageDraw.Draw(mask).rounded_rectangle(rect, radius=pad, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(feather / 2)) if feather else mask


def _with_panel(art: Image.Image, mask: Image.Image, color: RGB, opacity: float) -> Image.Image:
    if opacity <= 0:
        return art
    alpha = mask.point(lambda v: round(v * opacity))
    solid = Image.new("RGB", art.size, color)
    return Image.composite(solid, art, alpha)


def _worst_contrast(elements: list[TextElement], bg: Image.Image, percentile: float) -> float:
    return min(
        contrast_ratio(el.color, bg, w.bbox, percentile) for el in elements for w in el.words
    )


def choose_panel(
    art: Image.Image,
    elements: list[TextElement],
    style: StoryStyle,
    target: float,
    percentile: float,
    pad: int,
    feather: int,
) -> tuple[Image.Image, float]:
    """Lightest panel (from ``style.panel_opacities``) giving at least ``target`` contrast."""
    mask = panel_mask(art.size, _union(elements), pad, feather)
    bg = art
    opacity = 0.0
    for opacity in style.panel_opacities:
        bg = _with_panel(art, mask, style.panel_color, opacity)
        if _worst_contrast(elements, bg, percentile) >= target:
            break
    return bg, opacity


def panel_for_all(
    backgrounds: list[Image.Image],
    elements: list[TextElement],
    style: StoryStyle,
    target: float,
    percentile: float,
    pad: int,
) -> tuple[Image.Image, float]:
    """Lightest panel that gives ``target`` contrast on EVERY background (moving art).

    Returns (panel mask, opacity); the strongest panel is returned if none reaches the target
    (the contrast gate then fails).
    """
    mask = panel_mask(backgrounds[0].size, _union(elements), pad, style.feather_px)
    opacity = 0.0
    for opacity in style.panel_opacities:
        if all(
            _worst_contrast(elements, _with_panel(bg, mask, style.panel_color, opacity), percentile)
            >= target
            for bg in backgrounds
        ):
            break
    return mask, opacity


def with_panel(art: Image.Image, mask: Image.Image, color: RGB, opacity: float) -> Image.Image:
    """``art`` with a cream panel (``mask`` scaled by ``opacity``) composited on top."""
    return _with_panel(art, mask, color, opacity)


@dataclass
class ComposedSlide:
    """One finished slide and what the gates need."""

    index: int
    image: Image.Image
    background: Image.Image  # art + panels, without the words
    elements: list[TextElement]
    panel_opacity: float
    px: int
    upscale: float
    placeholder: bool


def compose_slide(
    cfg: Config,
    style: StoryStyle,
    art: Image.Image,
    text: str,
    position: str,
    spec: FontSpec,
    px: int,
    handle: str,
) -> tuple[Image.Image, Image.Image, list[TextElement], float]:
    """Place text and watermark on ``art``.

    Returns (slide, text-free background, elements, panel opacity).
    """
    gates = cfg.gates
    box = story_text_box(cfg, style, position)
    elements = place_story_text(text, box, spec, px, style)
    bg, opacity = choose_panel(
        art,
        elements,
        style,
        gates.story_min_contrast + style.contrast_margin,
        gates.contrast_percentile,
        style.pad_px,
        style.feather_px,
    )
    safe = safe_box(cfg, style.size)
    wm = _place_watermark(
        handle,
        WATERMARK_FONT,
        style.watermark_px,
        style.text_color,
        (safe[0] + safe[2]) / 2,
        safe[3] - 4,
        1.0,
    )
    bg, _ = choose_panel(
        bg,
        [wm],
        style,
        gates.watermark_min_contrast + style.contrast_margin,
        gates.contrast_percentile,
        max(12, style.watermark_px // 2),
        6,
    )
    elements.append(wm)
    return draw_elements(bg, elements), bg, elements, opacity
