"""Small signal-processing helpers (numpy/scipy only)."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pyloudnorm as pyln
from numpy.typing import NDArray
from scipy import signal
from scipy.ndimage import maximum_filter1d, uniform_filter1d

Audio = NDArray[np.float32]


def db_to_gain(db: float) -> float:
    """Convert decibels to a linear gain."""
    return float(10 ** (db / 20))


def resample(x: Audio, ratio: float) -> Audio:
    """Resample by ``ratio`` (output length ~= len(x) * ratio) with a polyphase filter."""
    frac = Fraction(ratio).limit_denominator(400)
    y = signal.resample_poly(x, frac.numerator, frac.denominator, axis=0)
    return np.asarray(y, dtype=np.float32)


def highpass(x: Audio, sr: int, cutoff: float) -> Audio:
    """Gentle 2nd-order high-pass filter."""
    sos = signal.butter(2, cutoff, "highpass", fs=sr, output="sos")
    return np.asarray(signal.sosfilt(sos, x, axis=0), dtype=np.float32)


def lowpass(x: Audio, sr: int, cutoff: float) -> Audio:
    """2nd-order low-pass filter."""
    sos = signal.butter(2, cutoff, "lowpass", fs=sr, output="sos")
    return np.asarray(signal.sosfilt(sos, x, axis=0), dtype=np.float32)


def envelope(x: Audio, sr: int, attack_s: float, release_s: float) -> Audio:
    """Smoothed amplitude envelope of a mono signal (peak follower)."""
    mono = np.abs(x if x.ndim == 1 else x.mean(axis=1))
    # Fast block-based follower: 5 ms blocks, then one-pole smoothing per block.
    block = max(int(sr * 0.005), 1)
    n_blocks = int(np.ceil(len(mono) / block))
    padded = np.pad(mono, (0, n_blocks * block - len(mono)))
    peaks = padded.reshape(n_blocks, block).max(axis=1)
    a_att = np.exp(-block / (sr * attack_s))
    a_rel = np.exp(-block / (sr * release_s))
    env = np.empty_like(peaks)
    level = 0.0
    for i, p in enumerate(peaks):
        coeff = a_att if p > level else a_rel
        level = coeff * level + (1 - coeff) * p
        env[i] = level
    return np.repeat(env, block)[: len(mono)].astype(np.float32)


def compress(x: Audio, sr: int, threshold_db: float, ratio: float, makeup_db: float = 0.0) -> Audio:
    """Gentle downward compressor."""
    env = envelope(x, sr, 0.01, 0.15)
    level_db = 20 * np.log10(np.maximum(env, 1e-6))
    over = np.maximum(level_db - threshold_db, 0)
    gain_db = -over * (1 - 1 / ratio) + makeup_db
    gain = (10 ** (gain_db / 20)).astype(np.float32)
    return (x * (gain if x.ndim == 1 else gain[:, None])).astype(np.float32)


def reverb(x: Audio, sr: int, seconds: float, wet: float, seed: int) -> Audio:
    """Convolution reverb with a synthetic exponentially decaying noise impulse."""
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    t = np.arange(n) / sr
    noise = (rng.normal(0, 1, n) * np.exp(-6.9 * t / seconds)).astype(np.float32)  # -60 dB
    ir = lowpass(noise, sr, 5000)
    ir /= np.sqrt(np.sum(ir**2)) + 1e-9
    if x.ndim == 1:
        tail = np.asarray(signal.fftconvolve(x, ir)[: len(x)])
    else:
        chans = [signal.fftconvolve(x[:, c], ir)[: len(x)] for c in range(x.shape[1])]
        tail = np.stack(chans, 1)
    return np.asarray((1 - wet) * x + wet * tail, dtype=np.float32)


def fade(x: Audio, sr: int, fade_in: float, fade_out: float) -> Audio:
    """Apply raised-cosine fades at both ends."""
    y = x.copy()
    for seconds, at_start in ((fade_in, True), (fade_out, False)):
        n = min(int(sr * seconds), len(y))
        if n <= 0:
            continue
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(np.float32)
        if not at_start:
            ramp = ramp[::-1]
        sl = slice(0, n) if at_start else slice(len(y) - n, len(y))
        y[sl] *= ramp if y.ndim == 1 else ramp[:, None]
    return y


def true_peak_db(x: Audio) -> float:
    """Approximate true peak (dBTP) using 4x oversampling."""
    up = signal.resample_poly(x, 4, 1, axis=0)
    peak = float(np.max(np.abs(up)))
    return float(20 * np.log10(max(peak, 1e-9)))


def loudness_lufs(x: Audio, sr: int) -> float:
    """Integrated loudness (ITU-R BS.1770) in LUFS."""
    meter = pyln.Meter(sr)
    return float(meter.integrated_loudness(x.astype(np.float64)))


def limit(x: Audio, sr: int, ceiling_db: float) -> Audio:
    """Look-ahead peak limiter keeping (oversampled) peaks under ``ceiling_db``."""
    ceiling = db_to_gain(ceiling_db)
    mono_peak = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    look = max(int(sr * 0.003), 1)
    peak_win = maximum_filter1d(mono_peak * 1.12, size=2 * look + 1)  # 1.12 ~ inter-sample margin
    gain = np.minimum(1.0, ceiling / np.maximum(peak_win, 1e-9))
    gain = maximum_filter1d(1 - gain, size=int(sr * 0.05))  # hold reductions ~50 ms
    gain = 1 - uniform_filter1d(gain, size=int(sr * 0.02))  # smooth
    return np.asarray(x * (gain if x.ndim == 1 else gain[:, None]), dtype=np.float32)


def normalise_loudness(x: Audio, sr: int, target_lufs: float, ceiling_db: float) -> Audio:
    """Bring ``x`` to the target loudness while keeping true peaks under the ceiling."""
    y = x
    for _ in range(6):
        current = loudness_lufs(y, sr)
        if not np.isfinite(current):
            return y
        y = (y * db_to_gain(target_lufs - current)).astype(np.float32)
        if true_peak_db(y) <= ceiling_db:
            break
        y = limit(y, sr, ceiling_db - 0.3)
    return y
