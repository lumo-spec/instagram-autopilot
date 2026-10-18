"""Text layout: typographic clean-up, balanced line wrapping and auto-fit into a box."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from PIL import ImageFont

from autopilot.errors import FitError
from autopilot.render.fonts import load_font
from autopilot.render.theme import RGB, FontSpec

Box = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass(frozen=True)
class PlacedWord:
    """A word drawn at baseline-left origin ``(x, y)``; ``bbox`` is its ink box."""

    text: str
    x: float
    y: float
    bbox: Box
    line: int


@dataclass
class TextElement:
    """A block of text drawn in one font and colour (body, attribution or watermark)."""

    role: str
    font: FontSpec
    px: int
    color: RGB
    words: list[PlacedWord] = field(default_factory=list)
    opacity: float = 1.0

    @property
    def bbox(self) -> Box:
        """Union of the ink boxes of all words."""
        boxes = [w.bbox for w in self.words]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def pil_font(self) -> ImageFont.FreeTypeFont:
        """The loaded font for this element."""
        return load_font(self.font.family, self.px, self.font.weight, self.font.italic)


def display_text(text: str) -> str:
    """Typographic clean-up for display: em dashes and curly quotes."""
    text = re.sub(r"\s*--\s*", "—", text)
    out: list[str] = []
    open_double = True
    for i, ch in enumerate(text):
        if ch == '"':
            out.append("“" if open_double else "”")
            open_double = not open_double
        elif ch == "'":
            prev = text[i - 1] if i else " "
            out.append("‘" if prev.isspace() else "’")
        else:
            out.append(ch)
    return "".join(out)


def _split_words(text: str) -> list[str]:
    # Allow breaks after em dashes as well as at spaces.
    return [w for w in re.split(r"\s+|(?<=—)", text) if w]


def _break_bonus(word: str, max_width: float) -> float:
    """Reward line breaks after sentence or clause punctuation."""
    if word.endswith((".", "!", "?", ";", ":", "—")):
        return (0.45 * max_width) ** 2
    if word.endswith(","):
        return (0.2 * max_width) ** 2
    return 0.0


def wrap_balanced(
    widths: list[float], space: float, max_width: float, words: list[str] | None = None
) -> list[list[int]] | None:
    """Split word indices into lines no wider than ``max_width``, minimising raggedness.

    When ``words`` is given, breaks after punctuation are preferred.
    """
    n = len(widths)
    best = [0.0] + [float("inf")] * n
    prev = [0] * (n + 1)
    for j in range(1, n + 1):
        line_w = -space
        for i in range(j, 0, -1):
            line_w += widths[i - 1] + space
            if line_w > max_width:
                break
            slack = max_width - line_w
            cost = best[i - 1] + (slack**2 if j < n else (slack * 0.3) ** 2)
            if words is not None and j < n:
                cost -= _break_bonus(words[j - 1], max_width)
            if j == n and i == j and n > 1:
                cost += max_width**2  # avoid a lonely last word
            if cost < best[j]:
                best[j], prev[j] = cost, i - 1
    if best[n] == float("inf"):
        return None
    lines: list[list[int]] = []
    j = n
    while j > 0:
        lines.append(list(range(prev[j], j)))
        j = prev[j]
    return lines[::-1]


def _place_block(
    words: list[str],
    font: ImageFont.FreeTypeFont,
    lines: list[list[int]],
    center_x: float,
    top: float,
    line_h: float,
) -> list[PlacedWord]:
    ascent, _ = font.getmetrics()
    space = font.getlength(" ")
    placed: list[PlacedWord] = []
    for li, idxs in enumerate(lines):
        line_w = sum(font.getlength(words[i]) for i in idxs) + space * (len(idxs) - 1)
        x = center_x - line_w / 2
        y = top + ascent + li * line_h
        for i in idxs:
            left, top_, right, bottom = font.getbbox(words[i], anchor="ls")
            box = (int(x + left), int(y + top_), int(x + right) + 1, int(y + bottom) + 1)
            placed.append(PlacedWord(words[i], x, y, box, li))
            x += font.getlength(words[i]) + space
    return placed


def fit_quote(
    text: str,
    author: str | None,
    box: Box,
    body_font: FontSpec,
    attr_font: FontSpec,
    colors: tuple[RGB, RGB],
    px_range: tuple[int, int, int],
    line_spacing: float,
) -> list[TextElement]:
    """Fit the quote (and attribution) into ``box`` at the largest size that fits.

    ``px_range`` is (max, min, step). Raises FitError when even the minimum size overflows.
    """
    max_px, min_px, step = px_range
    body_words = _split_words(display_text(text))
    attr_words = _split_words("— " + display_text(author)) if author else []
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    for px in range(max_px, min_px - 1, -step):
        font = load_font(body_font.family, px, body_font.weight, body_font.italic)
        lines = wrap_balanced(
            [font.getlength(w) for w in body_words], font.getlength(" "), width, body_words
        )
        if lines is None:
            continue
        line_h = px * line_spacing
        body_h = line_h * (len(lines) - 1) + sum(font.getmetrics())
        attr_px = max(min_px, round(px * 0.62))
        afont = load_font(attr_font.family, attr_px, attr_font.weight, attr_font.italic)
        attr_lines: list[list[int]] = []
        attr_h = 0.0
        gap = 0.0
        if attr_words:
            wrapped = wrap_balanced(
                [afont.getlength(w) for w in attr_words], afont.getlength(" "), width
            )
            if wrapped is None:
                continue
            attr_lines = wrapped
            gap = px * 0.8
            attr_h = attr_px * line_spacing * (len(attr_lines) - 1) + sum(afont.getmetrics())
        total = body_h + gap + attr_h
        if total > height:
            continue
        y0 = top + (height - total) / 2
        cx = left + width / 2
        body = TextElement("body", body_font, px, colors[0])
        body.words = _place_block(body_words, font, lines, cx, y0, line_h)
        elements = [body]
        if attr_words:
            attr = TextElement("attribution", attr_font, attr_px, colors[1])
            attr.words = _place_block(
                attr_words, afont, attr_lines, cx, y0 + body_h + gap, attr_px * line_spacing
            )
            elements.append(attr)
        return elements
    raise FitError(f"text does not fit at {min_px}px or above")


def place_watermark(
    handle: str, font: FontSpec, px: int, color: RGB, center_x: float, bottom: float, opacity: float
) -> TextElement:
    """Single-line handle watermark whose ink sits just above ``bottom``."""
    pil = load_font(font.family, px, font.weight, font.italic)
    _, descent = pil.getmetrics()
    ascent, _ = pil.getmetrics()
    elem = TextElement("watermark", font, px, color, opacity=opacity)
    elem.words = _place_block([handle], pil, [[0]], center_x, bottom - ascent - descent, px)
    return elem
