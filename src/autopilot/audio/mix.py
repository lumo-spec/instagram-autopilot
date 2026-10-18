"""Final reel mix: duck music under the voice, fade, normalise loudness and limit peaks."""

from __future__ import annotations

import numpy as np

from autopilot.audio import dsp

INTERNAL_CEILING_DB = -2.0  # AAC encoding can raise peaks slightly; gate checks -1 dBTP after
MUSIC_BED_LUFS = -21.0  # unducked music level; ducking then drops it duck_db further
VOICE_LUFS = -17.0


def _fit(x: dsp.Audio, n: int) -> dsp.Audio:
    if len(x) >= n:
        return x[:n]
    pad = np.zeros((n - len(x), *x.shape[1:]), np.float32)
    return np.concatenate([x, pad])


def mix(
    voice: dsp.Audio | None,
    music: dsp.Audio | None,
    sr: int,
    duration: float,
    duck_db: float,
    target_lufs: float,
) -> dsp.Audio:
    """Return a stereo mix of exactly ``duration`` seconds at ``target_lufs``."""
    n = round(duration * sr)
    out = np.zeros((n, 2), np.float32)
    if voice is not None:
        v = _fit(voice, n)
        v = v * dsp.db_to_gain(VOICE_LUFS - dsp.loudness_lufs(v, sr))
        out += np.stack([v, v], axis=1)
    if music is not None:
        m = _fit(music, n)
        m = m * dsp.db_to_gain(MUSIC_BED_LUFS - dsp.loudness_lufs(m, sr))
        if voice is not None:
            env = dsp.envelope(_fit(voice, n), sr, 0.05, 0.6)
            active = np.clip(env / (float(env.max()) * 0.25 + 1e-9), 0, 1)
            m = m * (10 ** (-duck_db * active / 20))[:, None].astype(np.float32)
        else:
            m = m * dsp.db_to_gain(VOICE_LUFS - MUSIC_BED_LUFS)  # music-only: music is the star
        out += m
    out = dsp.fade(out, sr, 0.05, 0.6)
    return dsp.normalise_loudness(out, sr, target_lufs, INTERNAL_CEILING_DB)
