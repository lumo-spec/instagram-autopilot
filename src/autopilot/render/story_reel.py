"""Story reels: a carousel turned into a 9:16 reel of about 20-30 seconds.

Every slide's art fills the 1080x1920 frame (the full height of the 4:5 art is kept and the sides
are trimmed) and moves slowly, alternating zoom-in, pan right, zoom-out and pan left. The
picture is seen first; then the slide's words (with the cream panel behind them when the art
needs one) fade in. Slides cross-fade into each other. Text sits inside the reel safe zone (top
14 %, bottom 35 % and sides 8 % kept clear for Instagram's buttons and caption), in one size for
every slide (the first may be larger). There is no watermark: Instagram shows the account name
on every reel. Music is mixed separately and passed in.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw

from autopilot.audio import dsp
from autopilot.config import Config
from autopilot.errors import ContentError
from autopilot.render.image import save_jpeg
from autopilot.render.layout import Box, TextElement
from autopilot.render.reel import encode_mp4
from autopilot.render.story import (
    StoryStyle,
    max_fit_px,
    panel_for_all,
    place_story_text,
    with_panel,
)

MOTIONS = ("zoom-in", "pan-right", "zoom-out", "pan-left")


@dataclass(frozen=True)
class ReelSettings:
    """``config.yaml`` -> ``story_reel`` (plus the reel size, frame rate and safe zone)."""

    size: tuple[int, int]
    fps: int
    seconds: tuple[float, float]
    allowed_seconds: tuple[float, float]
    slide_seconds: tuple[float, float]
    words_per_second: float
    linger: float
    text_delay: float
    text_fade: float
    crossfade: float
    tail: float
    motion_scale: float
    zoom: float
    areas: dict[str, tuple[float, float]]
    safe: Box
    inset: int


def _pair(v: Any) -> tuple[float, float]:
    return float(v[0]), float(v[1])


def reel_settings(cfg: Config) -> ReelSettings:
    """Read the story reel settings."""
    s = cfg.section("story_reel")
    w, h = (int(v) for v in cfg.section("render", "reel_size"))
    sz = cfg.section("safe_zones", "reel")
    safe = (
        round(w * float(sz["sides"])),
        round(h * float(sz["top"])),
        round(w * (1 - float(sz["sides"]))),
        round(h * (1 - float(sz["bottom"]))),
    )
    return ReelSettings(
        size=(w, h),
        fps=int(cfg.section("render", "fps")),
        seconds=_pair(s["seconds"]),
        allowed_seconds=_pair(s["allowed_seconds"]),
        slide_seconds=_pair(s["slide_seconds"]),
        words_per_second=float(s["words_per_second"]),
        linger=float(s["linger_s"]),
        text_delay=float(s["text_delay_s"]),
        text_fade=float(s["text_fade_s"]),
        crossfade=float(s["crossfade_s"]),
        tail=float(s["tail_s"]),
        motion_scale=float(s["motion_scale"]),
        zoom=float(s["zoom"]),
        areas={k: _pair(v) for k, v in s["text_area"].items()},
        safe=safe,
        inset=round(w * float(cfg.section("render", "text_inset"))),
    )


def reel_text_box(rs: ReelSettings, position: str) -> Box:
    """Where a slide's words may go in the reel (always inside the reel safe zone)."""
    _, h = rs.size
    top_f, bottom_f = rs.areas[position]
    return (
        rs.safe[0] + rs.inset,
        max(round(h * top_f), rs.safe[1]),
        rs.safe[2] - rs.inset,
        min(round(h * bottom_f), rs.safe[3]),
    )


# ----- timing ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class SlideTiming:
    """When a slide starts, when its words start to fade in, and when it hands over."""

    start: float
    text_in: float
    end: float  # the next slide's start (or the end of the reel)

    def reading_time(self, rs: ReelSettings, last: bool) -> float:
        """Seconds the words are fully visible (before the cross-fade to the next slide)."""
        out = self.end if last else self.end - rs.crossfade
        return out - (self.text_in + rs.text_fade)


def plan_story_timing(word_counts: list[int], rs: ReelSettings) -> tuple[list[SlideTiming], float]:
    """Slide timings and total length: reading pace first, then stretched towards the target."""
    lo_s, hi_s = rs.slide_seconds
    need = [
        rs.text_delay + rs.text_fade + n / rs.words_per_second + rs.linger + rs.crossfade
        for n in word_counts
    ]
    holds = [min(max(x, lo_s), max(hi_s, x)) for x in need]
    target_lo, target_hi = rs.seconds
    for _ in range(20):  # nudge every slide (within its limits) towards the target length
        total = sum(holds) + rs.tail
        if target_lo <= total <= target_hi:
            break
        if total < target_lo:
            room = [max(hi_s, n) - h for h, n in zip(holds, need, strict=True)]
            if sum(room) <= 1e-6:
                break
            k = min(1.0, (target_lo - total) / sum(room))
            holds = [h + r * k for h, r in zip(holds, room, strict=True)]
        else:
            room = [h - max(lo_s, n) for h, n in zip(holds, need, strict=True)]
            if sum(room) <= 1e-6:
                break
            k = min(1.0, (total - target_hi) / sum(room))
            holds = [h - r * k for h, r in zip(holds, room, strict=True)]
    timings, t = [], 0.0
    for h in holds:
        timings.append(SlideTiming(t, t + rs.text_delay, t + h))
        t += h
    last = timings[-1]
    timings[-1] = SlideTiming(last.start, last.text_in, last.end + rs.tail)
    return timings, timings[-1].end


# ----- motion ---------------------------------------------------------------------------------
class SlideMotion:
    """The moving, text-free picture of one slide."""

    def __init__(self, art: Image.Image, rs: ReelSettings, focus: str, kind: str) -> None:
        w, h = rs.size
        scale = max(w * rs.motion_scale / art.width, h * rs.motion_scale / art.height)
        self.big = art.convert("RGB").resize(
            (math.ceil(art.width * scale), math.ceil(art.height * scale)),
            Image.Resampling.LANCZOS,
        )
        self.rs, self.focus, self.scale = rs, focus, scale
        bw, bh = self.big.size
        slack = bw - bh * w / h
        self.kind = kind if kind.startswith("zoom") or slack > w * 0.05 else "zoom-in"

    def window(self, p: float) -> tuple[float, float, float, float]:
        """Crop box in the enlarged art at progress ``p`` (0..1), eased."""
        e = 0.5 - 0.5 * math.cos(math.pi * min(max(p, 0.0), 1.0))
        w, h = self.rs.size
        bw, bh = self.big.size
        z = self.rs.zoom
        zoom = {
            "zoom-in": 1 + (z - 1) * e,
            "zoom-out": 1 + (z - 1) * (1 - e),
        }.get(self.kind, 1 + (z - 1) * 0.35)
        ch = bh / zoom
        cw = ch * w / h
        slack = bw - cw
        if self.kind == "pan-right":
            left = slack * (0.1 + 0.8 * e)
        elif self.kind == "pan-left":
            left = slack * (0.9 - 0.8 * e)
        else:
            left = slack / 2
        top = {"top": 0.0, "center": (bh - ch) / 2, "bottom": bh - ch}[self.focus]
        return left, top, left + cw, top + ch

    def frame(self, p: float) -> Image.Image:
        """The picture at progress ``p``."""
        return self.big.resize(self.rs.size, Image.Resampling.BILINEAR, box=self.window(p))

    @property
    def upscale(self) -> float:
        """Largest enlargement of the owner's art in any frame (at the deepest zoom)."""
        _, h = self.rs.size
        smallest = min(self.window(p)[3] - self.window(p)[1] for p in (0.0, 0.5, 1.0))
        return h * self.scale / smallest  # output pixels per pixel of the original art


# ----- one slide ------------------------------------------------------------------------------
@dataclass
class ReelSlide:
    """One slide of the reel and what the gates need."""

    index: int
    timing: SlideTiming
    motion: SlideMotion
    elements: list[TextElement]
    overlay: Image.Image  # RGBA: cream panel + words
    panel_opacity: float
    px: int
    words: int
    samples: list[Image.Image] = field(default_factory=list)  # picture + panel, words visible

    def progress(self, t: float, rs: ReelSettings) -> float:
        """Motion progress at time ``t`` (motion starts when the slide fades in)."""
        begin = max(self.timing.start - rs.crossfade, 0.0)
        return (t - begin) / max(self.timing.end - begin, 1e-6)


def _text_layer(size: tuple[int, int], elements: list[TextElement]) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for el in elements:
        font = el.pil_font()
        for word in el.words:
            draw.text((word.x, word.y), word.text, font=font, fill=(*el.color, 255), anchor="ls")
    return layer


def text_sizes(
    texts: list[str], positions: list[str], style: StoryStyle, rs: ReelSettings
) -> tuple[int, int]:
    """(first slide px, other slides px) for the reel; raises ContentError if text won't fit."""
    hi, lo, step = style.px_range
    body = hi
    for n, (text, pos) in enumerate(zip(texts[1:], positions[1:], strict=True), start=2):
        px = max_fit_px(text, reel_text_box(rs, pos), style.font, hi, lo, step, style)
        if px is None:
            raise ContentError(f"slide {n}: text too long for the reel at {lo}px")
        body = min(body, px)
    cover = max_fit_px(
        texts[0],
        reel_text_box(rs, positions[0]),
        style.cover_font,
        style.cover_px_max,
        body if len(texts) > 1 else lo,
        step,
        style,
    )
    if cover is None:
        raise ContentError("slide 1: text too long for the reel")
    return cover, body


def build_slide(
    index: int,
    art: Image.Image,
    text: str,
    position: str,
    focus: str,
    px: int,
    words: int,
    timing: SlideTiming,
    style: StoryStyle,
    rs: ReelSettings,
    target_contrast: float,
    percentile: float,
) -> ReelSlide:
    """Lay out one slide's words and choose a panel that is readable in every sampled frame."""
    motion = SlideMotion(art, rs, focus, MOTIONS[(index - 1) % len(MOTIONS)])
    spec = style.cover_font if index == 1 else style.font
    elements = place_story_text(text, reel_text_box(rs, position), spec, px, style)
    slide = ReelSlide(index, timing, motion, elements, Image.new("RGBA", rs.size), 0.0, px, words)
    full = timing.text_in + rs.text_fade
    times = (full, (full + timing.end) / 2, timing.end)
    pictures = [motion.frame(slide.progress(t, rs)) for t in times]
    mask, opacity = panel_for_all(
        pictures, elements, style, target_contrast, percentile, style.pad_px
    )
    panel = Image.new("RGBA", rs.size, (*style.panel_color, 0))
    panel.putalpha(mask.point(lambda v: round(v * opacity)))
    slide.overlay = Image.alpha_composite(panel, _text_layer(rs.size, elements))
    slide.panel_opacity = opacity
    slide.samples = [with_panel(p, mask, style.panel_color, opacity) for p in pictures]
    return slide


# ----- the whole reel -------------------------------------------------------------------------
@dataclass
class StoryReel:
    """A rendered story reel."""

    path: Path
    cover: Path
    slides: list[ReelSlide]
    duration: float


class _Fader:
    """Overlay copies at quantised opacities (fading text is cheap to draw)."""

    def __init__(self, overlay: Image.Image) -> None:
        self.overlay = overlay
        self.alpha = overlay.getchannel("A")
        self.cache: dict[int, Image.Image] = {}

    def at(self, a: float) -> Image.Image:
        level = round(min(max(a, 0.0), 1.0) * 24)
        if level not in self.cache:
            layer = self.overlay.copy()
            layer.putalpha(self.alpha.point(lambda v, k=level / 24: round(v * k)))
            self.cache[level] = layer
        return self.cache[level]


def frame_at(
    t: float, slides: list[ReelSlide], faders: list[_Fader], rs: ReelSettings
) -> Image.Image:
    """The finished frame at time ``t``."""
    i = next((n for n, s in enumerate(slides) if t < s.timing.end), len(slides) - 1)
    s = slides[i]
    a = (t - s.timing.text_in) / rs.text_fade
    img = Image.alpha_composite(s.motion.frame(s.progress(t, rs)).convert("RGBA"), faders[i].at(a))
    if i + 1 < len(slides) and t > s.timing.end - rs.crossfade:
        nxt = slides[i + 1]
        incoming = nxt.motion.frame(nxt.progress(t, rs)).convert("RGBA")
        mix = (t - (s.timing.end - rs.crossfade)) / rs.crossfade
        img = Image.blend(img, incoming, min(max(mix, 0.0), 1.0))
    return img.convert("RGB")


def render_story_reel(
    slides: list[ReelSlide],
    duration: float,
    audio: dsp.Audio,
    sr: int,
    rs: ReelSettings,
    out: Path,
    cover: Path,
    jpeg_quality: int,
) -> StoryReel:
    """Encode the frames with ``audio`` (already mixed to ``duration``) and save the cover."""
    faders = [_Fader(s.overlay) for s in slides]
    n_frames = round(duration * rs.fps)

    def frames() -> Iterator[Image.Image]:
        for f in range(n_frames):
            yield frame_at(f / rs.fps, slides, faders, rs)

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "mix.wav"
        need = round(duration * sr)
        track = (
            audio[:need] if len(audio) >= need else np.pad(audio, ((0, need - len(audio)), (0, 0)))
        )
        sf.write(wav, track, sr, subtype="PCM_24")
        encode_mp4(frames(), rs.size, rs.fps, wav, out)
    first = slides[0]
    save_jpeg(
        frame_at(first.timing.text_in + rs.text_fade + 0.05, slides, faders, rs),
        cover,
        jpeg_quality,
    )
    return StoryReel(out, cover, slides, duration)
