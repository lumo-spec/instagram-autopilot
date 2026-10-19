"""Background music: procedural ambient (default) or a licence-checked CC0 library.

Procedural tracks are synthesised from scratch for every reel (seeded by the slot), so there is
no copyright risk and no two reels share a track: slow pad chords from a randomly chosen key and
progression, a soft filtered-noise wash and, on top, either occasional bell notes or (story
reels) a sparse piano-like melody that follows the chords. The "piano" is additive synthesis
(slightly stretched harmonics that fade faster the higher they are, plus a soft hammer click):
a gentle electric-piano / music-box sound, not a recorded piano.

Library tracks live in ``assets/music/`` and are only used with an entry in ``licences.json``:
licence ``CC0-1.0`` and a source URL; a track chosen by name additionally needs a title, creator
and the file's SHA-256 (so a swapped file is refused).
"""

from __future__ import annotations

import hashlib
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
LEADS = ("bell", "piano")
RECORD_FIELDS = ("title", "creator", "source_url", "licence", "sha256")


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


def piano_note(freq: float, length: float, sr: int, velocity: float) -> dsp.Audio:
    """A soft piano-like note: stretched harmonics, faster decay up high, a hammer click."""
    n = int(length * sr)
    t = np.arange(n, dtype=np.float32) / sr
    out = np.zeros(n, np.float32)
    for k in range(1, 9):
        fk = freq * k * float(np.sqrt(1 + 0.0004 * k * k))  # piano strings are slightly stiff
        if fk > sr / 2.2:
            break
        decay = 0.9 + 0.75 * k
        out += (np.sin(2 * np.pi * fk * t) * np.exp(-t * decay) / k**1.4).astype(np.float32)
    attack = min(int(0.006 * sr), n)
    out[:attack] *= np.linspace(0, 1, attack, dtype=np.float32)
    out += (np.exp(-t * 400) * np.sin(2 * np.pi * freq * 6 * t) * 0.05).astype(np.float32)
    release = min(int(0.4 * sr), n // 3)
    out[n - release :] *= np.linspace(1, 0, release, dtype=np.float32) ** 2
    return out * velocity


def _melody(
    out: dsp.Audio,
    sr: int,
    rng: np.random.Generator,
    root: int,
    chords: list[tuple[float, list[int]]],
    duration: float,
) -> None:
    """Sparse piano notes from the current chord and the pentatonic scale (mixed in place)."""
    t = float(rng.uniform(0.8, 1.6))
    prev = root + 24
    while t < duration - 2.5:
        chord = next((c for start, c in reversed(chords) if start <= t), chords[0][1])
        pool = sorted(
            {root + 24 + (c - root) % 12 for c in chord} | {root + 24 + p for p in PENTATONIC}
        )
        near = [p for p in pool if abs(p - prev) <= 5] or pool  # small steps: a calm tune
        note = int(near[int(rng.integers(len(near)))])
        length = float(rng.uniform(2.2, 3.4))
        tone = piano_note(_midi_hz(note), length, sr, float(rng.uniform(0.10, 0.16)))
        if rng.random() < 0.3:  # now and then a soft lower note with it
            tone = tone + piano_note(_midi_hz(note - 12), length, sr, 0.07)
        s0 = int(t * sr)
        end = min(s0 + len(tone), len(out))
        pan = float(rng.uniform(0.35, 0.65))
        out[s0:end, 0] += tone[: end - s0] * (1 - pan)
        out[s0:end, 1] += tone[: end - s0] * pan
        prev = note
        t += float(rng.choice([0.9, 1.2, 1.2, 1.8, 2.4]))


def procedural(duration: float, sr: int, seed: int, lead: str = "bell") -> dsp.Audio:
    """Synthesise a unique, calm ambient stereo track of ``duration`` seconds.

    ``lead`` is ``bell`` (sparse bell notes) or ``piano`` (a sparse piano-like melody over the
    pad, used by story reels).
    """
    if lead not in LEADS:
        raise RenderError(f"music lead must be one of {LEADS}")
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    out = np.zeros((n, 2), np.float32)
    root = int(rng.integers(45, 53))  # A2..C#3
    scale = MAJOR if rng.random() < 0.5 else MINOR
    prog = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    chord_len = float(rng.uniform(5.0, 7.0))
    overlap = 1.8
    pad_level = 0.7 if lead == "piano" else 1.0  # the pad sits further back under a melody
    chords: list[tuple[float, list[int]]] = []
    start, i = 0.0, 0
    while start < duration:
        degree = prog[i % len(prog)]
        length = min(chord_len + overlap, duration - start + overlap)
        cn = int(length * sr)
        s0 = int(start * sr)
        notes = [scale[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in (0, 2, 4)]
        voicing = [root + notes[0] - 12, root + notes[0], root + notes[1], root + notes[2] + 12]
        chords.append((start, [root + x for x in notes]))
        env = _envelope(cn, sr, 1.6, 2.2) * pad_level
        for v_i, note in enumerate(voicing):
            pan = 0.5 + (v_i - 1.5) * 0.18
            tone = _tone(_midi_hz(note), cn, sr, rng) * env * (0.5 if v_i == 0 else 0.32)
            end = min(s0 + cn, n)
            out[s0:end, 0] += tone[: end - s0] * (1 - pan)
            out[s0:end, 1] += tone[: end - s0] * pan
        start += chord_len
        i += 1
    if lead == "piano":
        _melody(out, sr, rng, root, chords, duration)
    # Bells: sparse soft notes from the pentatonic scale, two octaves up.
    t_bell = float(rng.uniform(1.5, 3.0)) if lead == "bell" else duration
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


def _loop_to(data: dsp.Audio, file_sr: int, duration: float, sr: int) -> dsp.Audio:
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = dsp.resample(data[:, :2], sr / file_sr) if file_sr != sr else data[:, :2]
    n = int(duration * sr)
    if len(data) < n:
        data = np.concatenate([data] * (n // len(data) + 1))
    return dsp.fade(data[:n], sr, 1.5, 2.0)


def library_track(duration: float, sr: int, seed: int, directory: Path = MUSIC_DIR) -> dsp.Audio:
    """Pick a licensed CC0 track; files without a licences.json entry are rejected."""
    licences = load_licences(directory)
    tracks = sorted(p for p in directory.glob("*.wav") if p.name in licences)
    if not tracks:
        raise RenderError("no licensed CC0 tracks available")
    path = tracks[np.random.default_rng(seed).integers(len(tracks))]
    data, file_sr = sf.read(path, dtype="float32", always_2d=True)
    return _loop_to(data, int(file_sr), duration, sr)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def licence_problems(name: str, directory: Path = MUSIC_DIR) -> list[str]:
    """Why the library track ``name`` may not be used (empty: licensed and unchanged)."""
    path = directory / name
    if Path(name).name != name or not path.is_file():
        return [f"{name}: no such file in {directory.name}/"]
    try:
        data = json.loads((directory / "licences.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["licences.json missing or unreadable"]
    record = data.get(name) if isinstance(data, dict) else None
    if not isinstance(record, dict):
        return [f"{name}: no licence record in licences.json"]
    problems = [f"{name}: licence record lacks {f}" for f in RECORD_FIELDS if not record.get(f)]
    if record.get("licence") != "CC0-1.0":
        problems.append(f"{name}: licence is {record.get('licence')!r}; only CC0-1.0 is allowed")
    if not str(record.get("source_url", "")).startswith("https://"):
        problems.append(f"{name}: source_url must be an https:// link")
    if record.get("sha256") and record["sha256"] != _file_sha256(path):
        problems.append(f"{name}: file changed since its licence was recorded (sha256)")
    return problems


def track_credit(name: str, directory: Path = MUSIC_DIR) -> str:
    """Plain-English credit for a library track, from its licence record."""
    data = json.loads((directory / "licences.json").read_text(encoding="utf-8"))
    r = data[name]
    return f'"{r["title"]}" by {r["creator"]} (CC0 1.0, {r["source_url"]})'


def licensed_track(name: str, duration: float, sr: int, directory: Path = MUSIC_DIR) -> dsp.Audio:
    """The named CC0 track, looped/trimmed to ``duration`` (refused without a valid record)."""
    problems = licence_problems(name, directory)
    if problems:
        raise RenderError("; ".join(problems))
    try:
        data, file_sr = sf.read(directory / name, dtype="float32", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise RenderError(f"cannot read {name}") from exc
    return _loop_to(data, int(file_sr), duration, sr)


def make_music(source: str, duration: float, sr: int, seed: int) -> dsp.Audio:
    """Music for a reel according to ``audio.music_source``."""
    return (
        library_track(duration, sr, seed) if source == "library" else procedural(duration, sr, seed)
    )
