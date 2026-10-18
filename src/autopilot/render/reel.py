"""Animated reels: 1080x1920, 30 fps, H.264 + AAC in MP4.

Text styles: word-by-word reveal (timed to the narration when a voice is present) or line
fade-in (music-only reels). Background motion: slow zoom, slow drift or drifting particles.
"""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw

from autopilot.audio import dsp
from autopilot.audio.voice import SpokenWord, VoiceTrack
from autopilot.config import Config
from autopilot.errors import RenderError
from autopilot.render.background import render_background
from autopilot.render.image import layout_for
from autopilot.render.layout import TextElement
from autopilot.render.theme import Template

FADE_S = 0.28
TAIL_AFTER_VOICE_S = 2.2
TAIL_MUSIC_ONLY_S = 3.5
FIRST_REVEAL_S = 0.8
_LETTERS = re.compile(r"[^a-z0-9]")


@dataclass
class RenderedReel:
    """A rendered reel plus what the gates need to check it."""

    path: Path
    elements: list[TextElement]
    sample_backgrounds: list[Image.Image]
    reveal_times: list[float]  # per body/attribution word, reading order
    spoken_starts: list[float | None]  # per body word (None when there is no voice)
    voice: VoiceTrack | None
    duration: float
    audio_mode: str
    template: str


def _key(text: str) -> str:
    return _LETTERS.sub("", text.lower().replace("’", "'").replace("'", ""))


def align_words(display: list[str], spoken: list[SpokenWord]) -> list[float | None]:
    """Map each displayed word to the start time of its spoken counterpart.

    Words are matched by their letters, so punctuation, curly quotes and hyphenation
    differences do not matter. A displayed word whose letters cannot be matched gets None.
    """
    starts: list[float | None] = []
    j = 0
    for word in display:
        target = _key(word)
        if not target:
            starts.append(starts[-1] if starts else None)
            continue
        acc, first = "", None
        k = j
        while k < len(spoken) and len(acc) < len(target):
            piece = _key(spoken[k].text)
            if first is None and piece:
                first = spoken[k].start
            acc += piece
            k += 1
        if acc == target and first is not None:
            starts.append(first)
            j = k
        else:
            starts.append(None)
    return starts


def _body_attr_words(elements: list[TextElement]) -> tuple[list[str], list[int]]:
    words, lines = [], []
    line_base = 0
    for el in elements:
        if el.role == "watermark":
            continue
        for w in el.words:
            words.append(w.text)
            lines.append(line_base + w.line)
        line_base += max(w.line for w in el.words) + 1
    return words, lines


def plan_reveals(
    elements: list[TextElement],
    voice: VoiceTrack | None,
    wps: float,
    limits: tuple[float, float],
    line_mode: bool,
) -> tuple[list[float], list[float | None], float]:
    """Return (reveal time per word, spoken start per body word, reel duration)."""
    words, lines = _body_attr_words(elements)
    n_body = len(elements[0].words)
    min_s, max_s = limits
    if voice is not None:
        spoken = align_words(words[:n_body], voice.words)
        known = [s for s in spoken if s is not None]
        if not known:
            raise RenderError("narration could not be aligned to the text")
        reveals: list[float] = []
        last = known[0]
        for s in spoken:
            last = s if s is not None else last
            reveals.append(last)
        end_voice = max(w.end for w in voice.words)
        reveals += [end_voice + 0.5] * (len(words) - n_body)
        duration = max(min_s, end_voice + TAIL_AFTER_VOICE_S, voice.duration + 0.3)
    else:
        spoken = [None] * n_body
        step = 1.0 / wps
        if line_mode:
            reveals, t, current = [], FIRST_REVEAL_S, lines[0]
            for i, ln in enumerate(lines):
                if ln != current:
                    t += step * sum(1 for x in lines[:i] if x == current)
                    current = ln
                reveals.append(t)
        else:
            reveals = [FIRST_REVEAL_S + i * step for i in range(len(words))]
        duration = max(min_s, reveals[-1] + FADE_S + TAIL_MUSIC_ONLY_S)
    if duration > max_s:
        raise RenderError(f"reel would last {duration:.1f}s, over the {max_s:.0f}s limit")
    return reveals, spoken, math.ceil(duration * 30) / 30


class _Motion:
    """Produces the text-free background for each frame."""

    def __init__(self, template: Template, size: tuple[int, int], seed: int, frames: int) -> None:
        self.kind, self.size, self.frames = template.motion, size, frames
        w, h = size
        scale = {"zoom": 1.12, "drift": 1.08, "particles": 1.0}[self.kind]
        self.big = render_background(template, round(w * scale), round(h * scale), seed)
        rng = np.random.default_rng(seed + 7)
        self.particles = rng.uniform(0, 1, (46, 5))  # x, y, radius, alpha, phase
        self.dot = tuple(min(255, c + 70) for c in template.colors[1])

    def frame(self, i: int) -> Image.Image:
        p = i / max(self.frames - 1, 1)
        w, h = self.size
        bw, bh = self.big.size
        if self.kind == "zoom":
            zoom = 1.0 + 0.08 * (0.5 - 0.5 * math.cos(math.pi * p))
            cw, ch = bw / zoom, bh / zoom
            left, top = (bw - cw) / 2, (bh - ch) / 2
            return self.big.resize(
                (w, h), Image.Resampling.BILINEAR, box=(left, top, left + cw, top + ch)
            )
        if self.kind == "drift":
            ox, oy = (bw - w) * p, (bh - h) * (1 - p) * 0.6
            return self.big.crop((round(ox), round(oy), round(ox) + w, round(oy) + h))
        img = self.big.copy()
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        t = i / 30.0
        for x0, y0, r0, a0, ph in self.particles:
            x = (x0 * w + 18 * math.sin(t * 0.4 + ph * 6.28)) % w
            y = (y0 * h - t * (8 + 14 * r0)) % h
            r = 2 + 4 * r0
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(*self.dot, int(40 + 70 * a0)))
        return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


class _Sprites:
    """Pre-rendered word images so each frame only pastes masks."""

    def __init__(self, elements: list[TextElement]) -> None:
        self.items: list[tuple[tuple[int, int], Image.Image, Image.Image, bool]] = []
        for el in elements:
            font = el.pil_font()
            for w in el.words:
                left, top, right, bottom = w.bbox
                pad = 4
                mask = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad), 0)
                ImageDraw.Draw(mask).text(
                    (w.x - left + pad, w.y - top + pad), w.text, font=font, fill=255, anchor="ls"
                )
                if el.opacity < 1:
                    mask = mask.point(lambda v, o=el.opacity: round(v * o))
                solid = Image.new("RGB", mask.size, el.color)
                self.items.append(((left - pad, top - pad), solid, mask, el.role == "watermark"))
        self._cache: dict[tuple[int, int], Image.Image] = {}

    def draw(self, frame: Image.Image, alphas: list[float]) -> None:
        idx = 0
        for n, (pos, solid, mask, is_wm) in enumerate(self.items):
            alpha = 1.0
            if not is_wm:
                alpha = alphas[idx]
                idx += 1
            if alpha <= 0:
                continue
            if alpha < 1:
                level = round(alpha * 20)
                key = (n, level)
                if key not in self._cache:
                    self._cache[key] = mask.point(lambda v, a=level / 20: round(v * a))
                frame.paste(solid, pos, self._cache[key])
            else:
                frame.paste(solid, pos, mask)


def _encode(
    frames_iter: Iterator[Image.Image], size: tuple[int, int], fps: int, wav: Path, out: Path
) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    w, h = size
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-i",
        str(wav),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-g",
        str(fps * 2),
        "-x264-params",
        "open-gop=0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-use_editlist",
        "0",
        "-shortest",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stdin is None:
        raise RenderError("could not start ffmpeg")
    try:
        for frame in frames_iter:
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RenderError(f"ffmpeg failed: {err.strip()[:200]}")


def render_reel(
    cfg: Config,
    template: Template,
    text: str,
    author: str | None,
    handle: str,
    seed: int,
    out: Path,
    audio: dsp.Audio,
    voice: VoiceTrack | None,
    audio_mode: str,
    watermark_opacity: float,
) -> RenderedReel:
    """Render frames, mux with ``audio`` (already mixed to the reel length) and save the MP4."""
    size_cfg = cfg.section("render", "reel_size")
    size = (int(size_cfg[0]), int(size_cfg[1]))
    fps = int(cfg.section("render", "fps"))
    sr = int(cfg.section("audio", "sample_rate"))
    elements = layout_for(cfg, template, text, author, handle, size, True, watermark_opacity)
    reveals, spoken, duration = plan_timing(cfg, elements, voice, template)
    n_frames = round(duration * fps)
    motion = _Motion(template, size, seed, n_frames)
    sprites = _Sprites(elements)

    def frames() -> Iterator[Image.Image]:
        for i in range(n_frames):
            t = i / fps
            frame = motion.frame(i)
            sprites.draw(frame, [min(max((t - r) / FADE_S, 0.0), 1.0) for r in reveals])
            yield frame

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "mix.wav"
        need = round(duration * sr)
        track = (
            audio[:need] if len(audio) >= need else np.pad(audio, ((0, need - len(audio)), (0, 0)))
        )
        sf.write(wav, track, sr, subtype="PCM_24")
        _encode(frames(), size, fps, wav, out)
    samples = [motion.frame(i) for i in (0, n_frames // 2, n_frames - 1)]
    return RenderedReel(
        out, elements, samples, reveals, spoken, voice, duration, audio_mode, template.name
    )


def plan_timing(
    cfg: Config, elements: list[TextElement], voice: VoiceTrack | None, template: Template
) -> tuple[list[float], list[float | None], float]:
    """Reveal schedule and duration for a reel (narrated reels always reveal word by word)."""
    lo, hi = (float(v) for v in cfg.section("render", "reel_seconds"))
    wps = float(cfg.section("render", "reading_words_per_second"))
    line_mode = voice is None and template.text_anim == "line"
    return plan_reveals(elements, voice, wps, (lo, hi), line_mode)


def reel_duration(
    cfg: Config, elements: list[TextElement], voice: VoiceTrack | None, template: Template
) -> float:
    """Duration the reel will have, so the audio can be mixed to length first."""
    return plan_timing(cfg, elements, voice, template)[2]
