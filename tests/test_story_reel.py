"""Story reels: a carousel as a 9:16 reel, its music and the export of both."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autopilot.audio import dsp
from autopilot.audio.music import licence_problems, licensed_track, piano_note, procedural
from autopilot.config import Config, Page
from autopilot.content.queue import parse_post
from autopilot.errors import RenderError
from autopilot.export import NOTES, REEL_FILES, export_post
from autopilot.rehearsal.preview import build_and_approve
from autopilot.render.story import story_style
from autopilot.render.story_reel import (
    SlideMotion,
    plan_story_timing,
    reel_settings,
    reel_text_box,
    text_sizes,
)
from story_helpers import builder, make_art, post_data, write_post

SR = 48000
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


# ----- timing and layout ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("words", "fits"),
    [
        ([9, 8, 12, 7, 10, 9], "target"),  # a typical 6-slide carousel: 20-30 s
        ([6, 5], "allowed"),  # short: as long as the slides allow, still within limits
        ([16, 20, 20, 20, 20, 20, 20], "too-long"),  # reading time wins; the duration gate fails
    ],
)
def test_timing_reads_comfortably_and_lands_near_target(
    cfg: Config, words: list[int], fits: str
) -> None:
    rs = reel_settings(cfg)
    timings, total = plan_story_timing(words, rs)
    lo, hi = rs.allowed_seconds
    if fits == "target":
        assert rs.seconds[0] - 0.01 <= total <= rs.seconds[1] + 0.01
    elif fits == "allowed":
        assert lo <= total <= hi
    else:
        assert total > hi
    for n, (t, w) in enumerate(zip(timings, words, strict=True)):
        assert t.reading_time(rs, n == len(words) - 1) >= w / rs.words_per_second
    starts = [t.start for t in timings]
    assert starts == sorted(starts) and timings[-1].end == pytest.approx(total)


def test_text_boxes_stay_inside_the_reel_safe_zone(cfg: Config) -> None:
    rs = reel_settings(cfg)
    w, h = rs.size
    assert rs.safe == (round(w * 0.08), round(h * 0.14), round(w * 0.92), round(h * 0.65))
    for position in ("top", "middle", "bottom"):
        box = reel_text_box(rs, position)
        assert rs.safe[0] < box[0] < box[2] < rs.safe[2]
        assert rs.safe[1] <= box[1] < box[3] <= rs.safe[3]


def test_reel_text_is_large_and_one_size(cfg: Config) -> None:
    rs, st = reel_settings(cfg), story_style(cfg)
    texts = [
        "You can miss someone\nand still **let go.**",
        "It is allowed.",
        "Rest\nis not a reward.",
    ]
    cover, body = text_sizes(texts, ["top", "middle", "bottom"], st, rs)
    assert cover >= body >= cfg.gates.story_min_font_px


def test_motion_pans_and_zooms_without_overstretching_4x5_art(cfg: Config) -> None:
    rs = reel_settings(cfg)
    art = Image.new("RGB", (1080, 1350), (200, 180, 150))
    zoom = SlideMotion(art, rs, "center", "zoom-in")
    assert zoom.window(0)[3] - zoom.window(0)[1] > zoom.window(1)[3] - zoom.window(1)[1]
    pan = SlideMotion(art, rs, "top", "pan-right")
    assert pan.window(1)[0] > pan.window(0)[0] and pan.window(0)[1] == 0
    assert pan.frame(0.5).size == (1080, 1920)
    assert zoom.upscale <= story_style(cfg).max_upscale
    tall = SlideMotion(Image.new("RGB", (1080, 1920)), rs, "center", "pan-left")
    assert tall.kind == "zoom-in"  # nothing to pan across: zoom instead


def test_only_carousels_become_story_reels(story: tuple[Config, Page], tmp_path: Path) -> None:
    b = builder(story, tmp_path)
    post, _ = parse_post(write_post(b.page, post_data("i01-single", fmt="image", n=1)))
    assert post is not None
    composed = b.compose_story_reel(post, tmp_path / "r", allow_placeholder=True)
    assert not composed.paths and "story-reel:format" in {r.name for r in composed.report.failures}


# ----- music ---------------------------------------------------------------------------------
def test_piano_music_is_calm_unique_and_differs_from_bells() -> None:
    a = procedural(12.0, SR, 5, lead="piano")
    assert a.shape == (12 * SR, 2)
    assert np.allclose(procedural(12.0, SR, 5, lead="piano"), a)
    assert not np.allclose(procedural(12.0, SR, 5, lead="bell"), a)
    assert not np.allclose(procedural(12.0, SR, 6, lead="piano"), a)
    assert dsp.true_peak_db(a) < 0
    with pytest.raises(RenderError):
        procedural(3.0, SR, 1, lead="kazoo")


def test_piano_note_strikes_then_fades() -> None:
    note = piano_note(440.0, 2.0, SR, 0.2)
    early = float(np.abs(note[: SR // 10]).max())
    late = float(np.abs(note[SR : SR + SR // 10]).max())
    assert early > 3 * late > 0 and abs(note[-1]) < 1e-3


def _track(folder: Path, name: str = "calm.wav") -> Path:
    import soundfile as sf

    t = np.arange(SR * 3) / SR
    path = folder / name
    sf.write(path, (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), SR)
    return path


def _record(folder: Path, name: str, **change: str) -> None:
    rec = {
        "title": "Calm",
        "creator": "A. Musician",
        "source_url": "https://example.org/calm",
        "licence": "CC0-1.0",
        "sha256": hashlib.sha256((folder / name).read_bytes()).hexdigest(),
    }
    rec.update(change)
    (folder / "licences.json").write_text(json.dumps({name: rec}), encoding="utf-8")


def test_named_cc0_track_needs_a_complete_matching_record(tmp_path: Path) -> None:
    _track(tmp_path)
    assert licence_problems("calm.wav", tmp_path)  # no licences.json
    _record(tmp_path, "calm.wav", licence="CC-BY-4.0")
    assert any("only CC0-1.0" in p for p in licence_problems("calm.wav", tmp_path))
    _record(tmp_path, "calm.wav", source_url="http://example.org/calm")
    assert any("https" in p for p in licence_problems("calm.wav", tmp_path))
    _record(tmp_path, "calm.wav", creator="")
    assert any("lacks creator" in p for p in licence_problems("calm.wav", tmp_path))
    _record(tmp_path, "calm.wav", sha256="0" * 64)
    assert any("file changed" in p for p in licence_problems("calm.wav", tmp_path))
    assert licence_problems("../calm.wav", tmp_path)
    _record(tmp_path, "calm.wav")
    assert licence_problems("calm.wav", tmp_path) == []
    audio = licensed_track("calm.wav", 7.0, SR, tmp_path)  # looped to length
    assert audio.shape == (7 * SR, 2)
    _track(tmp_path)  # same name, same content: still fine
    assert licence_problems("calm.wav", tmp_path) == []


# ----- full render and export (ffmpeg) -------------------------------------------------------
@pytest.mark.slow
def test_story_reel_renders_and_passes_every_gate(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    b = builder(story, tmp_path)
    data = post_data("c01-reel", n=3)
    for slide, pos in zip(data["slides"], ("top", "middle", "bottom"), strict=True):
        slide["position"] = pos
    folder = write_post(b.page, data, art="quiet")
    make_art(folder / "art-03.png", "busy", seed=3)  # busy art: the panel must keep text readable
    post, _ = parse_post(folder)
    assert post is not None
    composed = b.compose_story_reel(post, tmp_path / "r", allow_placeholder=False)
    assert composed.report.passed, composed.report.summary()
    names = {r.name for r in composed.report.results}
    for gate in (
        "reel:codec",
        "reel:size",
        "reel:duration",
        "audio:loudness",
        "reel-slide3:contrast:body",
        "reel-slide3:placement:body",
        "reel-slide2:reading-time",
        "reel-slide1:art-resolution",
        "cover:image:size",
        "story-reel:art-supplied",
    ):
        assert gate in names
    assert composed.music == "procedural" and composed.cover is not None
    lo, hi = reel_settings(story[0]).allowed_seconds
    assert lo <= composed.seconds <= hi
    with Image.open(composed.cover) as im:
        assert im.size == (1080, 1920)


@pytest.mark.slow
def test_export_adds_the_story_reel_for_carousels(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    write_post(page, post_data("c01-real", n=2), art="quiet")
    build_and_approve(cfg, page, "c01-real")
    result = export_post(cfg, page, "c01-real", tmp_path, NOW)
    assert result.reel is not None and result.reel.made, result.reel
    for name in REEL_FILES:
        assert (result.folder / name).is_file()
    notes = (result.folder / NOTES).read_text(encoding="utf-8")
    assert "STORY REEL (optional" in notes and "our own generated music" in notes
    assert "not both at once" in notes

    bad = export_post(cfg, page, "c01-real", tmp_path, NOW, music="missing.flac")
    assert bad.reel is not None and not bad.reel.made
    assert "STORY REEL: not made" in (bad.folder / NOTES).read_text(encoding="utf-8")
    assert not (bad.folder / REEL_FILES[0]).exists()
    assert (bad.folder / "01.jpg").is_file()  # the carousel is still exported
