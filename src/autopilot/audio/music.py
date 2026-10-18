"""Background music: procedural ambient (default) or a licence-checked CC0 library.

Procedural tracks are synthesised from scratch for every reel (seeded by the slot), so there is
no copyright risk and no two reels share a track: slow pad chords from a randomly chosen key and
progression, a soft filtered-noise wash and occasional bell notes from a pentatonic scale.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from autopilot.audio import dsp
from autopilot.config import ROOT
from autopilot.errors import RenderError

MUSIC_DIR = ROOT / "assets" / "music"
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)
PROGRESSIONS = ((0, 5, 3, 4), (0, 3, 5, 4), (5, 3, 0, 4), (0, 4, 5, 3), (0, 2, 3, 0))
PENTATONIC = (0, 2, 4, 7, 9)


def _midi_hz(note: float) -> float:
    return float(440.0 * 2 ** ((note - 69) / 12))


def _tone(freq: float, n: int, sr: int, rng: np.random.Generator) -> dsp.Audio:
    t = np.arange(n, dtype=np.float32) / sr
    out = np.zeros(n, np.float32)
    for k, amp in ((1, 1.0), (2, 0.28), (3, 0.1)):
        detune = 1 + rng.uniform(-0.0015, 0.0015)
        phase = rng.uniform(0, 2 * np.pi)
        out += amp * np.sin(2 * np.pi * freq * k * detune * t + phase).astype(np.float32)
    return out


def _envelope(n: int, sr: int, attack: float, release: float) -> dsp.Audio:
    env = np.ones(n, np.float32)
    a, r = min(int(attack * sr), n // 2), min(int(release * sr), n // 2)
    env[:a] = np.linspace(0, 1, a, dtype=np.float32) ** 2
    env[n - r :] = np.linspace(1, 0, r, dtype=np.float32) ** 2
    return env


def procedural(duration: float, sr: int, seed: int) -> dsp.Audio:
    """Synthesise a unique, calm ambient stereo track of ``duration`` seconds."""
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    out = np.zeros((n, 2), np.float32)
    root = int(rng.integers(45, 53))  # A2..C#3
    scale = MAJOR if rng.random() < 0.5 else MINOR
    prog = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    chord_len = float(rng.uniform(5.0, 7.0))
    overlap = 1.8
    start, i = 0.0, 0
    while start < duration:
        degree = prog[i % len(prog)]
        length = min(chord_len + overlap, duration - start + overlap)
        cn = int(length * sr)
        s0 = int(start * sr)
        notes = [scale[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in (0, 2, 4)]
        voicing = [root + notes[0] - 12, root + notes[0], root + notes[1], root + notes[2] + 12]
        env = _envelope(cn, sr, 1.6, 2.2)
        for v_i, note in enumerate(voicing):
            pan = 0.5 + (v_i - 1.5) * 0.18
            tone = _tone(_midi_hz(note), cn, sr, rng) * env * (0.5 if v_i == 0 else 0.32)
            end = min(s0 + cn, n)
            out[s0:end, 0] += tone[: end - s0] * (1 - pan)
            out[s0:end, 1] += tone[: end - s0] * pan
        start += chord_len
        i += 1
    # Bells: sparse soft notes from the pentatonic scale, two octaves up.
    t_bell = float(rng.uniform(1.5, 3.0))
    while t_bell < duration - 2:
        bn = int(2.5 * sr)
        s0 = int(t_bell * sr)
        note = root + 24 + PENTATONIC[int(rng.integers(5))]
        tt = np.arange(bn, dtype=np.float32) / sr
        bell = np.sin(2 * np.pi * _midi_hz(note) * tt) * np.exp(-tt * 2.2) * 0.12
        pan = float(rng.uniform(0.25, 0.75))
        end = min(s0 + bn, n)
        out[s0:end, 0] += bell[: end - s0].astype(np.float32) * (1 - pan)
        out[s0:end, 1] += bell[: end - s0].astype(np.float32) * pan
        t_bell += float(rng.uniform(2.0, 4.5))
    wash = dsp.lowpass(rng.normal(0, 1, (n, 2)).astype(np.float32), sr, 600) * 0.03
    out = dsp.lowpass(out, sr, 3500) + wash
    out = dsp.reverb(out, sr, 2.4, 0.35, seed + 1)
    return dsp.fade(out, sr, 1.5, 2.0)


def load_licences(directory: Path = MUSIC_DIR) -> dict[str, dict[str, str]]:
    """Read ``licences.json``; every library track must have an entry with source and licence."""
    path = directory / "licences.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if v.get("licence") == "CC0-1.0" and v.get("source_url")}


def library_track(duration: float, sr: int, seed: int, directory: Path = MUSIC_DIR) -> dsp.Audio:
    """Pick a licensed CC0 track; files without a licences.json entry are rejected."""
    licences = load_licences(directory)
    tracks = sorted(p for p in directory.glob("*.wav") if p.name in licences)
    if not tracks:
        raise RenderError("no licensed CC0 tracks available")
    path = tracks[np.random.default_rng(seed).integers(len(tracks))]
    data, file_sr = sf.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = dsp.resample(data[:, :2], sr / file_sr) if file_sr != sr else data[:, :2]
    n = int(duration * sr)
    if len(data) < n:
        data = np.concatenate([data] * (n // len(data) + 1))
    return dsp.fade(data[:n], sr, 1.5, 2.0)


def make_music(source: str, duration: float, sr: int, seed: int) -> dsp.Audio:
    """Music for a reel according to ``audio.music_source``."""
    return (
        library_track(duration, sr, seed) if source == "library" else procedural(duration, sr, seed)
    )
