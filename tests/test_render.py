"""Layout, rendering, alignment and audio processing (fast; no TTS model needed)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from autopilot.audio import dsp
from autopilot.audio.mix import mix
from autopilot.audio.music import load_licences, procedural
from autopilot.audio.voice import SpokenWord, narration_text
from autopilot.config import ROOT, Config
from autopilot.errors import FitError, RenderError
from autopilot.gates.visual import check_visual
from autopilot.render.fonts import verify_fonts
from autopilot.render.image import render_image, text_box
from autopilot.render.layout import display_text, fit_quote, wrap_balanced
from autopilot.render.reel import align_words
from autopilot.render.theme import FontSpec, load_theme

ARCHIVE_THEME = ROOT / "pages" / "simplestorymode" / "archive" / "theme-quotes.yaml"

SR = 48000
LORA = FontSpec("lora", 500, False)


def test_fonts_verified() -> None:
    verify_fonts()


def test_display_text() -> None:
    assert display_text('He said "be calm" -- and it\'s fine') == "He said “be calm”—and it’s fine"


def test_wrap_balanced_respects_width_and_prefers_punctuation() -> None:
    words = ["Thank", "the", "rain.", "The", "garden", "is", "drinking."]
    widths = [len(w) * 10.0 for w in words]
    lines = wrap_balanced(widths, 10, 240, words)
    assert lines is not None
    assert all(sum(widths[i] for i in ln) + 10 * (len(ln) - 1) <= 240 for ln in lines)
    assert words[lines[0][-1]] == "rain."
    assert wrap_balanced([500.0], 10, 200) is None


def test_fit_quote_shrinks_then_fails() -> None:
    box = (0, 0, 900, 900)
    short = fit_quote("Be calm.", None, box, LORA, LORA, ((0, 0, 0), (0, 0, 0)), (104, 44, 2), 1.3)
    long = fit_quote("word " * 60, None, box, LORA, LORA, ((0, 0, 0), (0, 0, 0)), (104, 44, 2), 1.3)
    assert short[0].px == 104 and 44 <= long[0].px < short[0].px
    for el in (*short, *long):
        left, top, right, bottom = el.bbox
        assert left >= 0 and top >= 0 and right <= 900 and bottom <= 900
    with pytest.raises(FitError):
        fit_quote("word " * 400, None, box, LORA, LORA, ((0, 0, 0), (0, 0, 0)), (104, 44, 2), 1.3)


def test_attribution_placed_below_body() -> None:
    els = fit_quote(
        "Be calm.",
        "Epictetus",
        (0, 0, 900, 900),
        LORA,
        LORA,
        ((0, 0, 0), (9, 9, 9)),
        (90, 44, 2),
        1.3,
    )
    assert els[1].role == "attribution" and els[1].bbox[1] > els[0].bbox[3]
    assert els[1].words[0].text == "—"


def test_themes_have_eight_templates(cfg: Config) -> None:
    for path in (cfg.page("simplestorymode").dir / "theme.yaml", ARCHIVE_THEME):
        theme = load_theme(path)
        assert len(theme.templates) >= 8
        assert len({t.name for t in theme.templates}) == len(theme.templates)


def test_every_quote_template_renders_and_passes(cfg: Config, tmp_out: Path) -> None:
    theme = load_theme(ARCHIVE_THEME)
    long_text = (
        "Keep thyself then simple, good, pure, serious, free from affectation, a friend of "
        "justice, a worshipper of the gods, kind, affectionate, strenuous in all proper acts."
    )
    for i, tpl in enumerate(theme.templates):
        r = render_image(
            cfg,
            tpl,
            long_text,
            "Marcus Aurelius",
            "@test.page",
            i,
            tmp_out / f"{tpl.name}.jpg",
            0.85,
        )
        safe, _ = text_box(cfg, r.background.size, False)
        report = check_visual(r.elements, [r.background], safe, cfg.gates)
        assert report.passed, f"{tpl.name}: {report.summary()}"


def test_storybook_templates_readable_without_art(cfg: Config, tmp_out: Path) -> None:
    """Art-free reels put text straight on these backgrounds, so each must pass on its own."""
    theme = load_theme(cfg.page("simplestorymode").dir / "theme.yaml")
    for i, tpl in enumerate(theme.templates):
        r = render_image(
            cfg,
            tpl,
            "You don't have to explain your tiredness to deserve rest.",
            None,
            "@simplestorymode",
            i,
            tmp_out / f"story-{tpl.name}.jpg",
            theme.watermark_opacity,
        )
        safe, _ = text_box(cfg, r.background.size, False)
        report = check_visual(r.elements, [r.background], safe, cfg.gates)
        assert report.passed, f"{tpl.name}: {report.summary()}"


def test_align_words() -> None:
    spoken = [
        SpokenWord(t, s, s + 0.2)
        for t, s in [
            ("It", 1.0),
            ("is", 1.2),
            ("to", 1.4),
            ("-", 1.5),
            ("day", 1.6),
            ("friend", 1.9),
        ]
    ]
    starts = align_words(["It’s"[:2], "is", "to-day,", "friend."], spoken)
    assert starts == [1.0, 1.2, 1.4, 1.9]
    assert align_words(["banana"], spoken) == [None]


def test_narration_text() -> None:
    out = narration_text("“Be calm”—said Epictetus.", {"Epictetus": "ɛpɪktˈiːtəs"})
    assert out == "Be calm, said [Epictetus](/ɛpɪktˈiːtəs/)."


# ----- audio -------------------------------------------------------------------------------
def test_normalise_and_limit() -> None:
    rng = np.random.default_rng(1)
    x = (rng.normal(0, 0.3, (SR * 5, 2))).astype(np.float32)
    y = dsp.normalise_loudness(x, SR, -14, -2.0)
    assert abs(dsp.loudness_lufs(y, SR) + 14) < 1.0
    assert dsp.true_peak_db(y) <= -1.0


def test_procedural_music_unique_and_calm() -> None:
    a = procedural(6.0, SR, 1)
    b = procedural(6.0, SR, 2)
    assert a.shape == (6 * SR, 2)
    assert not np.allclose(a, b)
    assert np.allclose(procedural(6.0, SR, 1), a)


def test_mix_hits_target_and_ducks() -> None:
    music = procedural(10.0, SR, 3)
    voice = np.zeros(SR * 10, np.float32)
    t = np.arange(SR * 4) / SR
    voice[SR * 3 : SR * 7] = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    out = mix(voice, music, SR, 10.0, 12, -14)
    assert out.shape == (SR * 10, 2)
    assert abs(dsp.loudness_lufs(out, SR) + 14) < 1.0
    assert dsp.true_peak_db(out) <= -1.0
    only_music = mix(None, music, SR, 10.0, 12, -14)
    assert abs(dsp.loudness_lufs(only_music, SR) + 14) < 1.0


def test_music_library_requires_licence_entry(tmp_path: Path) -> None:
    (tmp_path / "licences.json").write_text(
        '{"a.wav": {"licence": "CC0-1.0", "source_url": "https://example.org/a"},'
        ' "b.wav": {"licence": "CC-BY-4.0", "source_url": "https://example.org/b"}}',
        encoding="utf-8",
    )
    assert set(load_licences(tmp_path)) == {"a.wav"}
    from autopilot.audio.music import library_track

    with pytest.raises(RenderError):
        library_track(5.0, SR, 1, tmp_path)  # no licensed files present
