"""Visual gates: readable font size, contrast against the real pixels, safe-zone placement."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from autopilot.config import Gates
from autopilot.gates import GateReport
from autopilot.render.layout import Box, TextElement
from autopilot.render.theme import RGB


def _linear(channel: NDArray[np.float64]) -> NDArray[np.float64]:
    c = channel / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb: NDArray[np.float64]) -> NDArray[np.float64]:
    """WCAG relative luminance of an (..., 3) array of sRGB values."""
    lin = _linear(rgb.astype(np.float64))
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def contrast_ratio(color: RGB, background: Image.Image, box: Box, percentile: float) -> float:
    """Contrast between ``color`` and the background pixels under ``box`` (worst percentile)."""
    w, h = background.size
    left, top, right, bottom = max(box[0], 0), max(box[1], 0), min(box[2], w), min(box[3], h)
    if right <= left or bottom <= top:
        return 0.0
    crop = background.crop((left, top, right, bottom)).convert("RGB")
    region = np.asarray(crop, dtype=np.float64)
    lum_bg = luminance(region.reshape(-1, 3))
    lum_text = float(luminance(np.asarray(color, dtype=np.float64)))
    hi = np.maximum(lum_bg, lum_text)
    lo = np.minimum(lum_bg, lum_text)
    ratios = (hi + 0.05) / (lo + 0.05)
    return float(np.percentile(ratios, percentile))


def _inside(box: Box, safe: Box) -> bool:
    return box[0] >= safe[0] and box[1] >= safe[1] and box[2] <= safe[2] and box[3] <= safe[3]


def check_visual(
    elements: list[TextElement], backgrounds: list[Image.Image], safe: Box, gates: Gates
) -> GateReport:
    """Run readability and placement gates for every text element.

    ``backgrounds`` are the text-free frames behind the text (one for an image; several sampled
    frames for a reel, since its background moves). Body and attribution text must meet the
    main thresholds; the decorative watermark has its own, lower ones.
    """
    report = GateReport()
    for el in elements:
        is_wm = el.role == "watermark"
        min_px = gates.watermark_min_px if is_wm else gates.min_font_px
        min_cr = gates.watermark_min_contrast if is_wm else gates.min_contrast
        report.add(f"font-size:{el.role}", el.px >= min_px, f"{el.px}px, minimum {min_px}px")
        worst = min(
            contrast_ratio(el.color, bg, w.bbox, gates.contrast_percentile)
            for bg in backgrounds
            for w in el.words
        )
        # Translucent text shows less contrast; scale conservatively by its opacity.
        effective = 1 + (worst - 1) * el.opacity
        report.add(
            f"contrast:{el.role}", effective >= min_cr, f"{effective:.2f}:1, minimum {min_cr}:1"
        )
        outside = [w.text for w in el.words if not _inside(w.bbox, safe)]
        report.add(
            f"placement:{el.role}",
            not outside,
            "inside safe zone" if not outside else f"outside safe zone: {' '.join(outside)[:40]}",
        )
    return report
