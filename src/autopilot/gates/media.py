"""Media gates: file format checks and the narration/audio gates for reels.

Everything is measured from the finished files (decoded with PyAV), not from intentions,
except word sync, which compares the on-screen reveal schedule with the TTS engine's own word
timestamps (there is no speech recogniser in the pipeline).
"""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
from PIL import Image

from autopilot.audio import dsp
from autopilot.audio.voice import VoiceTrack
from autopilot.config import Config
from autopilot.gates import GateReport

FRAME_S = 0.01


def check_image_file(path: Path, cfg: Config) -> GateReport:
    """JPEG, exact size, RGB, within Instagram's file-size limit."""
    report = GateReport()
    size = tuple(int(v) for v in cfg.section("render", "image_size"))
    limit = int(cfg.section("limits", "image_max_bytes"))
    with Image.open(path) as img:
        report.add("image:format", img.format == "JPEG", str(img.format))
        report.add("image:size", img.size == size, f"{img.size[0]}x{img.size[1]}")
        report.add("image:mode", img.mode == "RGB", img.mode)
    nbytes = path.stat().st_size
    report.add("image:bytes", 0 < nbytes <= limit, f"{nbytes} bytes")
    return report


def decode_audio(path: Path) -> tuple[dsp.Audio, int]:
    """Decode the first audio stream of a media file to float32 (samples, channels)."""
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        chunks = [f.to_ndarray() for f in container.decode(stream)]
        sr = int(stream.rate)
    data = np.concatenate(chunks, axis=1).T.astype(np.float32)  # planar -> (n, ch)
    return data, sr


def probe_reel(path: Path, cfg: Config) -> tuple[GateReport, float, float]:
    """Check container/codec specs; returns the report and video/audio durations (s)."""
    report = GateReport()
    w, h = (int(v) for v in cfg.section("render", "reel_size"))
    fps = int(cfg.section("render", "fps"))
    with av.open(str(path)) as container:
        v = container.streams.video[0]
        frames = sum(1 for pkt in container.demux(v) if pkt.size)
        video_s = frames / fps
        report.add("reel:codec", v.codec_context.name == "h264", v.codec_context.name)
        report.add("reel:size", (v.width, v.height) == (w, h), f"{v.width}x{v.height}")
        rate = float(v.average_rate) if v.average_rate else 0.0
        report.add("reel:fps", abs(rate - fps) < 0.01, f"{rate:.2f}")
        report.add("reel:pixfmt", v.format.name == "yuv420p", v.format.name)
        has_audio = bool(container.streams.audio)
    report.add("reel:audio-stream", has_audio, "present" if has_audio else "missing")
    audio_s = 0.0
    if has_audio:
        with av.open(str(path)) as container:
            a = container.streams.audio[0]
            report.add("reel:audio-codec", a.codec_context.name == "aac", a.codec_context.name)
            report.add("reel:audio-rate", int(a.rate) <= 48000, str(a.rate))
        data, sr = decode_audio(path)
        audio_s = len(data) / sr
    lo, hi = (float(x) for x in cfg.section("render", "reel_seconds"))
    report.add("reel:duration", lo - 0.05 <= video_s <= hi + 0.05, f"{video_s:.2f}s")
    nbytes = path.stat().st_size
    report.add(
        "reel:bytes", 0 < nbytes <= int(cfg.section("limits", "reel_max_bytes")), f"{nbytes}"
    )
    return report, video_s, audio_s


def _frame_rms(x: dsp.Audio, sr: int) -> np.ndarray:
    mono = x if x.ndim == 1 else x.mean(axis=1)
    n = int(sr * FRAME_S)
    usable = len(mono) // n * n
    return np.sqrt(np.mean(mono[:usable].reshape(-1, n) ** 2, axis=1))


def check_reel(
    path: Path,
    cfg: Config,
    audio_mode: str,
    voice: VoiceTrack | None,
    reveal_times: list[float],
    spoken_starts: list[float | None],
) -> GateReport:
    """All reel gates: specs, A/V duration, loudness/peaks and (if narrated) voice gates."""
    g = cfg.gates
    report, video_s, audio_s = probe_reel(path, cfg)
    report.add(
        "reel:av-duration",
        abs(video_s - audio_s) <= g.av_duration_tolerance_s,
        f"video {video_s:.2f}s, audio {audio_s:.2f}s",
    )
    if audio_s:
        data, sr = decode_audio(path)
        lufs = dsp.loudness_lufs(data, sr)
        lo, hi = g.loudness_lufs
        report.add("audio:loudness", lo <= lufs <= hi, f"{lufs:.1f} LUFS")
        tp = dsp.true_peak_db(data)
        report.add("audio:true-peak", tp <= g.true_peak_max_dbtp, f"{tp:.1f} dBTP")
    if "voice" not in audio_mode:
        return report
    if voice is None:
        report.add("voice:present", False, "narration missing")
        return report
    rms = _frame_rms(voice.audio, voice.sr)
    loud = float(rms.max()) if rms.size else 0.0
    active = rms > loud * 0.1
    idx = np.flatnonzero(active)
    if not idx.size or loud < 1e-4:
        report.add("voice:present", False, "no speech energy")
        return report
    onset, end = idx[0] * FRAME_S, (idx[-1] + 1) * FRAME_S
    lo_on, hi_on = g.voice_onset_s
    report.add("voice:onset", lo_on <= onset <= hi_on, f"{onset:.2f}s")
    report.add(
        "voice:tail", video_s - end >= g.voice_tail_min_s, f"ends {video_s - end:.2f}s before end"
    )
    first, last = min(w.start for w in voice.words), max(w.end for w in voice.words)
    speaking = rms[int(first / FRAME_S) : int(last / FRAME_S)] > loud * 0.03
    share = float(speaking.mean()) if speaking.size else 0.0
    report.add(
        "voice:present", share >= g.speech_min_active_share, f"speech in {share:.0%} of span"
    )
    misses = [
        abs(r - s) * 1000
        for r, s in zip(reveal_times, spoken_starts, strict=False)
        if s is not None
    ]
    unaligned = sum(1 for s in spoken_starts if s is None)
    worst = max(misses) if misses else float("inf")
    report.add(
        "voice:word-sync",
        unaligned == 0 and worst <= g.word_sync_ms,
        f"worst {worst:.0f} ms, {unaligned} unaligned words",
    )
    return report
