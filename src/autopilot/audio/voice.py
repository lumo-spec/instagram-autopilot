"""Local narration with Kokoro TTS (Apache-2.0); no external API at synthesis time.

The model is pinned to a repository revision and every file is checked against its SHA-256
each time it is loaded (so a tampered cache is caught). The calm "wise narrator" style comes
from a slower rate, a lower pitch (resampling, which also deepens the voice), gentle
compression and a light reverb.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from autopilot.audio import dsp
from autopilot.config import Config
from autopilot.errors import RenderError

KOKORO_SR = 24000
_PUNCT_ONLY = re.compile(r"^\W+$")


@dataclass(frozen=True)
class SpokenWord:
    """A word as spoken, with start/end times (seconds) in the final track."""

    text: str
    start: float
    end: float


@dataclass
class VoiceTrack:
    """Processed narration (mono, float32) with word timings."""

    audio: dsp.Audio
    sr: int
    words: list[SpokenWord]
    voice: str

    @property
    def duration(self) -> float:
        """Length in seconds including the lead-in silence."""
        return len(self.audio) / self.sr


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_files(cfg: Config) -> dict[str, Path]:
    """Download (on cache miss) and verify every pinned Kokoro file; return local paths."""
    from huggingface_hub import hf_hub_download, try_to_load_from_cache

    tts = cfg.section("tts")
    paths: dict[str, Path] = {}
    for name, digest in tts["sha256"].items():
        cached = try_to_load_from_cache(tts["repo"], name, revision=tts["revision"])
        local = (
            cached
            if isinstance(cached, str)
            else hf_hub_download(tts["repo"], name, revision=tts["revision"])
        )
        path = Path(local)
        if _sha256(path) != digest:
            raise RenderError(f"TTS model file failed its checksum: {name}")
        paths[name] = path
    return paths


@lru_cache(maxsize=2)
def _pipeline(repo: str, config_path: str, model_path: str, lang: str) -> Any:
    from kokoro import KModel, KPipeline

    model = KModel(repo_id=repo, config=config_path, model=model_path).eval()
    return KPipeline(lang_code=lang, repo_id=repo, model=model)


def load_pronunciations(path: Path) -> dict[str, str]:
    """Read ``pronunciations.yaml``: ``words`` maps a word to Kokoro (misaki) phonemes."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (data.get("words") or {}).items()}


def narration_text(display: str, pronunciations: dict[str, str]) -> str:
    """Text sent to the TTS engine: no quote marks, dashes become pauses, phoneme overrides."""
    text = re.sub(r"[“”\"]", "", display)
    text = text.replace("—", ", ").replace("--", ", ")
    for word, phonemes in pronunciations.items():
        text = re.sub(rf"\b{re.escape(word)}\b", f"[{word}](/{phonemes}/)", text)
    return text


class Narrator:
    """Synthesises narration for a page using the configured voices and style."""

    def __init__(self, cfg: Config, pronunciations: dict[str, str]) -> None:
        self.cfg = cfg
        self.pronunciations = pronunciations
        self.paths = model_files(cfg)
        tts = cfg.section("tts")
        self.pipeline = _pipeline(
            tts["repo"],
            str(self.paths["config.json"]),
            str(self.paths["kokoro-v1_0.pth"]),
            tts["lang_code"],
        )
        self.sr = int(cfg.section("audio", "sample_rate"))

    def _voice_tensor(self, voice: str) -> Any:
        import torch

        key = f"voices/{voice}.pt"
        if key not in self.paths:
            raise RenderError(f"voice {voice} is not pinned in config.tts.sha256")
        return torch.load(self.paths[key], weights_only=True)

    def synthesise(self, display_text: str, voice: str, seed: int) -> VoiceTrack:
        """Speak ``display_text`` with ``voice``; returns processed audio and word timings."""
        style = self.cfg.section("audio", "voice_style")
        stretch = 2 ** (-float(style["pitch_semitones"]) / 12)  # >1 lowers pitch, slows down
        speed = float(style["rate"]) * stretch
        lead = float(style["lead_in_s"])
        text = narration_text(display_text, self.pronunciations)
        chunks: list[np.ndarray] = []
        words: list[SpokenWord] = []
        offset = 0.0
        for result in self.pipeline(text, voice=self._voice_tensor(voice), speed=speed):
            if result.audio is None:
                continue
            audio = result.audio.detach().cpu().numpy().astype(np.float32)
            for tok in result.tokens or []:
                if _PUNCT_ONLY.match(tok.text) or tok.start_ts is None or tok.end_ts is None:
                    continue
                words.append(
                    SpokenWord(
                        tok.text,
                        lead + (offset + float(tok.start_ts)) * stretch,
                        lead + (offset + float(tok.end_ts)) * stretch,
                    )
                )
            chunks.append(audio)
            offset += len(audio) / KOKORO_SR
        if not chunks:
            raise RenderError("TTS produced no audio")
        raw = np.concatenate(chunks)
        # Kokoro starts with a short silence; trim it so the configured lead-in is exact.
        loud = np.flatnonzero(np.abs(raw) > 0.02 * float(np.abs(raw).max()))
        trim = max(int(loud[0]) - int(0.03 * KOKORO_SR), 0) if loud.size else 0
        raw = raw[trim:]
        shift = trim / KOKORO_SR * stretch
        words = [SpokenWord(w.text, w.start - shift, w.end - shift) for w in words]
        # Reinterpret at a lower rate (pitch down + slower), then resample to the output rate.
        audio48 = dsp.resample(raw, self.sr * stretch / KOKORO_SR)
        audio48 = dsp.highpass(audio48, self.sr, 70)
        audio48 = dsp.compress(audio48, self.sr, -22, 2.5, 3)
        if style.get("reverb", "light") != "none":
            audio48 = dsp.reverb(audio48, self.sr, 0.7, 0.12, seed)
        audio48 = np.concatenate([np.zeros(int(lead * self.sr), np.float32), audio48])
        return VoiceTrack(audio48, self.sr, words, voice)
