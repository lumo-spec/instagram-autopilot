"""Every gate with passing and deliberately failing fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pytest
from PIL import Image

from autopilot.audio import dsp
from autopilot.audio.voice import SpokenWord, VoiceTrack
from autopilot.config import Config
from autopilot.gates.media import check_image_file, check_reel
from autopilot.gates.spelling import load_allowed, unknown_words, words_in
from autopilot.gates.visual import check_visual, contrast_ratio
from autopilot.render.layout import PlacedWord, TextElement
from autopilot.render.theme import FontSpec

FONT = FontSpec("lora", 500, False)
SAFE = (54, 135, 1026, 1215)
SR = 48000


def element(
    px: int = 60,
    color: tuple[int, int, int] = (255, 255, 255),
    bbox: tuple[int, int, int, int] = (200, 400, 600, 470),
    role: str = "body",
) -> TextElement:
    el = TextElement(role, FONT, px, color)
    el.words = [PlacedWord("word", bbox[0], bbox[3], bbox, 0)]
    return el


def dark_bg() -> Image.Image:
    return Image.new("RGB", (1080, 1350), (20, 20, 30))


# ----- visual --------------------------------------------------------------------------------
def test_visual_pass(cfg: Config) -> None:
    assert check_visual([element()], [dark_bg()], SAFE, cfg.gates).passed


def test_small_font_fails(cfg: Config) -> None:
    report = check_visual([element(px=30)], [dark_bg()], SAFE, cfg.gates)
    assert [r.name for r in report.failures] == ["font-size:body"]


def test_low_contrast_fails(cfg: Config) -> None:
    report = check_visual([element(color=(60, 60, 70))], [dark_bg()], SAFE, cfg.gates)
    assert [r.name for r in report.failures] == ["contrast:body"]


def test_contrast_samples_real_pixels(cfg: Config) -> None:
    bg = dark_bg()
    bg.paste((240, 240, 240), (300, 400, 600, 470))  # bright patch right behind part of the text
    report = check_visual([element()], [bg], SAFE, cfg.gates)
    assert "contrast:body" in [r.name for r in report.failures]
    assert contrast_ratio((255, 255, 255), dark_bg(), (0, 0, 0, 0), 2) == 0.0


def test_outside_safe_zone_fails(cfg: Config) -> None:
    report = check_visual([element(bbox=(200, 1230, 600, 1300))], [dark_bg()], SAFE, cfg.gates)
    assert [r.name for r in report.failures] == ["placement:body"]


def test_watermark_has_own_thresholds(cfg: Config) -> None:
    wm = element(px=34, color=(140, 140, 150), role="watermark")
    assert check_visual([wm], [dark_bg()], SAFE, cfg.gates).passed
    assert not check_visual([element(px=34)], [dark_bg()], SAFE, cfg.gates).passed


def test_moving_background_checked_on_every_sample(cfg: Config) -> None:
    light = Image.new("RGB", (1080, 1350), (250, 250, 250))
    assert not check_visual([element()], [dark_bg(), light], SAFE, cfg.gates).passed


# ----- spelling ------------------------------------------------------------------------------
def test_spelling() -> None:
    assert words_in("It’s to-day, friend") == ["It’s", "to", "day", "friend"]
    assert unknown_words(["The quiet mind sees clearly."], frozenset()) == set()
    assert unknown_words(["The quiett mind"], frozenset()) == {"quiett"}
    assert unknown_words(["The quiett mind"], frozenset({"quiett"})) == set()
    assert unknown_words(["Marcus’s colour"], frozenset({"colour"})) == set()


def test_allowed_words_file(tmp_path: Path) -> None:
    f = tmp_path / "words.txt"
    f.write_text("# comment\nColour\n\nharbour # British\n", encoding="utf-8")
    assert load_allowed(f) == frozenset({"colour", "harbour"})
    assert load_allowed(tmp_path / "none.txt") == frozenset()


# ----- image file ----------------------------------------------------------------------------
def test_image_file_gate(cfg: Config, tmp_out: Path) -> None:
    good = tmp_out / "good.jpg"
    Image.new("RGB", (1080, 1350), (1, 2, 3)).save(good, "JPEG")
    assert check_image_file(good, cfg).passed
    bad = tmp_out / "bad.png"
    Image.new("RGBA", (1080, 1080)).save(bad, "PNG")
    names = {r.name for r in check_image_file(bad, cfg).failures}
    assert {"image:format", "image:size", "image:mode"} <= names


# ----- reels ---------------------------------------------------------------------------------
def make_clip(
    path: Path, seconds: float, audio: np.ndarray | None, size: str = "1080x1920"
) -> Path:
    """Encode a test clip with the given stereo audio (or none)."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:r=30:d={seconds}",
    ]
    if audio is not None:
        import soundfile as sf

        wav = path.with_suffix(".wav")
        sf.write(wav, audio, SR, subtype="PCM_24")
        cmd += ["-i", str(wav), "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True)
    return path


def pink(seconds: float, lufs: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    x = dsp.lowpass(rng.normal(0, 0.1, (int(seconds * SR), 2)).astype(np.float32), SR, 2000)
    return dsp.normalise_loudness(x, SR, lufs, -3.0)


def speech_like(seconds: float, onset: float, end: float) -> np.ndarray:
    """Amplitude-modulated tone standing in for narration between onset and end."""
    t = np.arange(int(seconds * SR)) / SR
    x = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    x[(t < onset) | (t > end)] = 0
    return x.astype(np.float32)


def voice_track(onset: float = 0.7, end: float = 8.0, silent: bool = False) -> VoiceTrack:
    audio = speech_like(10.5, onset, end)
    if silent:
        audio[:] = 0
    words = [
        SpokenWord(f"w{i}", onset + i * 0.5, onset + i * 0.5 + 0.4)
        for i in range(int((end - onset) / 0.5))
    ]
    return VoiceTrack(audio, SR, words, "bm_george")


@pytest.fixture(scope="module")
def good_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_clip(tmp_path_factory.mktemp("clips") / "good.mp4", 11.0, pink(11.0, -14))


def test_reel_specs_pass(cfg: Config, good_clip: Path) -> None:
    report = check_reel(good_clip, cfg, "music", None, [], [])
    assert report.passed, report.summary()


def test_reel_without_audio_fails(cfg: Config, tmp_out: Path) -> None:
    clip = make_clip(tmp_out / "silent.mp4", 11.0, None)
    names = {r.name for r in check_reel(clip, cfg, "music", None, [], []).failures}
    assert "reel:audio-stream" in names and "reel:av-duration" in names


def test_reel_wrong_size_and_too_short(cfg: Config, tmp_out: Path) -> None:
    clip = make_clip(tmp_out / "small.mp4", 4.0, pink(4.0, -14), size="720x1280")
    names = {r.name for r in check_reel(clip, cfg, "music", None, [], []).failures}
    assert {"reel:size", "reel:duration"} <= names


def test_reel_av_mismatch(cfg: Config, tmp_out: Path) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    clip = tmp_out / "mismatch.mp4"
    import soundfile as sf

    wav = tmp_out / "a.wav"
    sf.write(wav, pink(9.0, -14), SR)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:r=30:d=11",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(clip),
        ],
        check=True,
    )
    assert "reel:av-duration" in {
        r.name for r in check_reel(clip, cfg, "music", None, [], []).failures
    }


@pytest.mark.parametrize(("lufs", "gate"), [(-22.0, "audio:loudness"), (-8.0, "audio:loudness")])
def test_reel_loudness_out_of_range(cfg: Config, tmp_out: Path, lufs: float, gate: str) -> None:
    clip = make_clip(tmp_out / "loud.mp4", 11.0, pink(11.0, lufs))
    assert gate in {r.name for r in check_reel(clip, cfg, "music", None, [], []).failures}


def test_clipping_fails(cfg: Config, tmp_out: Path) -> None:
    audio = pink(11.0, -14)
    audio[SR * 5 : SR * 5 + 200] = 0.999  # a hard clipped burst
    clip = make_clip(tmp_out / "clip.mp4", 11.0, audio)
    assert "audio:true-peak" in {
        r.name for r in check_reel(clip, cfg, "music", None, [], []).failures
    }


def _voice_gates(cfg: Config, clip: Path, voice: VoiceTrack | None, shift: float = 0.0) -> set[str]:
    starts = [w.start for w in voice.words] if voice else []
    reveals = [s + shift for s in starts]
    return {
        r.name for r in check_reel(clip, cfg, "voice+music", voice, reveals, list(starts)).failures
    }


def test_voice_gates_pass(cfg: Config, good_clip: Path) -> None:
    assert _voice_gates(cfg, good_clip, voice_track()) == set()


def test_missing_voice_fails(cfg: Config, good_clip: Path) -> None:
    assert "voice:present" in _voice_gates(cfg, good_clip, voice_track(silent=True))
    assert "voice:present" in _voice_gates(cfg, good_clip, None)


def test_late_onset_fails(cfg: Config, good_clip: Path) -> None:
    assert "voice:onset" in _voice_gates(cfg, good_clip, voice_track(onset=1.6))


def test_early_onset_fails(cfg: Config, good_clip: Path) -> None:
    assert "voice:onset" in _voice_gates(cfg, good_clip, voice_track(onset=0.2))


def test_voice_too_close_to_end_fails(cfg: Config, good_clip: Path) -> None:
    assert "voice:tail" in _voice_gates(cfg, good_clip, voice_track(end=10.4))


def test_word_sync_fails_when_reveals_drift(cfg: Config, good_clip: Path) -> None:
    assert "voice:word-sync" in _voice_gates(cfg, good_clip, voice_track(), shift=0.2)
    assert "voice:word-sync" not in _voice_gates(cfg, good_clip, voice_track(), shift=0.1)


def test_unaligned_word_fails_sync(cfg: Config, good_clip: Path) -> None:
    v = voice_track()
    starts: list[float | None] = [w.start for w in v.words]
    starts[2] = None
    reveals = [w.start for w in v.words]
    names = {r.name for r in check_reel(good_clip, cfg, "voice", v, reveals, starts).failures}
    assert "voice:word-sync" in names
