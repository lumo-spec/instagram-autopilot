"""Procedural backgrounds (no external images, so no copyright risk)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageFilter

from autopilot.render.theme import RGB, Template

FloatImage = NDArray[np.float32]


def _lerp(c0: RGB, c1: RGB, t: FloatImage) -> FloatImage:
    a = np.asarray(c0, dtype=np.float32)
    b = np.asarray(c1, dtype=np.float32)
    return a + (b - a) * t[..., None]


def _smooth_noise(rng: np.random.Generator, w: int, h: int, scale: int) -> FloatImage:
    """Low-frequency noise in [-1, 1] made by upscaling a small random grid."""
    small = rng.uniform(-1, 1, (max(h // scale, 2), max(w // scale, 2))).astype(np.float32)
    img = Image.fromarray(((small + 1) * 127.5).astype(np.uint8)).resize(
        (w, h), Image.Resampling.BICUBIC
    )
    return np.asarray(img, dtype=np.float32) / 127.5 - 1.0


def _base(t: Template, w: int, h: int, rng: np.random.Generator) -> FloatImage:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    kind = t.background
    if kind == "gradient":
        ang = math.radians(t.angle)
        proj = (xx - w / 2) * math.sin(ang) - (yy - h / 2) * math.cos(ang)
        span = abs(w * math.sin(ang)) + abs(h * math.cos(ang))
        return _lerp(t.colors[0], t.colors[1], np.clip(proj / span + 0.5, 0, 1))
    if kind == "vignette":
        r = np.hypot((xx - w / 2) / (w / 2), (yy - h * 0.45) / (h / 2))
        return _lerp(t.colors[0], t.colors[1], np.clip(r / 1.3, 0, 1) ** 1.4)
    img = _lerp(t.colors[0], t.colors[0], np.zeros((h, w), np.float32))
    if kind == "paper":
        fibres = _smooth_noise(rng, w, h, 6) * 0.5 + _smooth_noise(rng, w, h, 40) * 0.5
        img = _lerp(t.colors[0], t.colors[1], np.clip(0.5 + fibres * 0.6, 0, 1))
        edge = np.hypot((xx - w / 2) / w, (yy - h / 2) / h)
        img *= (1.0 - 0.10 * np.clip(edge * 2 - 0.4, 0, 1))[..., None]
    elif kind == "shapes":
        img = _lerp(t.colors[0], t.colors[0], np.zeros((h, w), np.float32))
        for _ in range(5):
            cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            rad = rng.uniform(0.25, 0.55) * w
            mask = np.clip(1 - np.hypot(xx - cx, yy - cy) / rad, 0, 1) ** 2
            img = img + (np.asarray(t.colors[1], np.float32) - img) * (mask * 0.8)[..., None]
    elif kind == "grain":
        img = _lerp(t.colors[0], t.colors[1], np.clip(yy / h, 0, 1))
    return img


def render_background(t: Template, w: int, h: int, seed: int) -> Image.Image:
    """Render the template's background at ``w`` x ``h`` (unique per ``seed``)."""
    rng = np.random.default_rng(seed)
    img = _base(t, w, h, rng)
    grain = 7.0 if t.background == "grain" else 2.5  # light dither also prevents banding
    img = img + rng.normal(0, grain, img.shape[:2]).astype(np.float32)[..., None]
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    if t.background == "shapes":
        out = out.filter(ImageFilter.GaussianBlur(1.2))
    return out
