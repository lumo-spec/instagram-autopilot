"""End-to-end rehearsals: fake clock, real queue/rendering/gates, fake Instagram."""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from autopilot.config import Config, Page
from autopilot.pipeline import Request
from autopilot.rehearsal import runner
from autopilot.state import PageState
from story_helpers import post_data, unillustrated_carousel, write_post


def fill_queue(page: Page, carousels: int, images: int) -> None:
    for i in range(1, carousels + 1):
        write_post(page, post_data(f"c{i:02d}-test", n=2 + i % 3))
    for i in range(1, images + 1):
        write_post(page, post_data(f"i{i:02d}-test", fmt="image", n=1))


def test_rehearsal_week_with_injected_failures(
    story: tuple[Config, Page], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, page = story
    fill_queue(page, carousels=3, images=1)  # 5 slots in the week, only 4 posts
    monkeypatch.setattr(
        runner,
        "FAILURE_PLAN",
        {
            0: ("crash_after_publish",),
            1: ("container_error", "missed_runs"),
            2: ("publish_transient",),
            3: ("quota_high",),
        },
    )
    # Mon 26 Oct 2026 (the day after the clock change) .. Sun 1 Nov
    data = runner.run_rehearsal(cfg, 7, date(2026, 10, 26), tmp_path / "r", failures=True)
    assert data["expected_slots"] == 5  # Mon, Wed, Fri carousels + Tue, Sat images
    assert data["published"] == 4 and data["skipped"] == 1  # Saturday: no image left
    assert data["double_posts"] == 0 and data["posts_reused"] == 0
    events = "\n".join(data["events"])
    assert "[crash]" in events and "reconciled, not re-posted" in events
    assert "[fail]" in events and "[quota]" in events
    assert "queue empty: no approved image post" in events
    titles = "\n".join(data["alerts"])
    assert "no approved post for the single slot" in titles
    assert "only 0 approved posts left" in titles
    for post in data["posts"]:
        for sample in post["samples"]:
            assert (tmp_path / "r" / sample).exists()
        assert post["gates"][-1]["passed"]
    carousels = [p for p in data["posts"] if p["format"] == "carousel"]
    assert {len(p["samples"]) for p in carousels} == {2, 3, 4}
    calls = json.loads((tmp_path / "r" / "fake-instagram-calls.json").read_text(encoding="utf-8"))
    assert all("token" not in json.dumps(c).lower() for c in calls)
    assert all(c.get("ai_generated") for c in calls if c["op"] == "create")
    state = json.loads(
        (tmp_path / "r/state/simplestorymode/state.json").read_text(encoding="utf-8")
    )
    assert sorted(p["status"] for p in state["posts"]) == ["published"] * 4 + ["skipped"]


def test_real_queue_rehearses(cfg: Config, tmp_path: Path) -> None:
    """The owner's actual queue drafts render and post in a short practice run."""
    data = runner.run_rehearsal(cfg, 3, date(2026, 11, 2), tmp_path / "r", failures=False)
    assert data["double_posts"] == 0 and data["posts_reused"] == 0
    assert all(p["gates"][-1]["passed"] for p in data["posts"])


@pytest.mark.slow
def test_reel_voice_fallbacks(story: tuple[Config, Page], tmp_path: Path) -> None:
    """Narrated reel from the queue: broken first voice -> second voice; all voices -> music."""
    cfg, page = story
    reel = post_data("r01-rest", fmt="reel", n=1, audio="voice+music")
    reel["slides"][0]["text"] = "Rest is not a reward. It is allowed."
    write_post(page, reel)
    from autopilot.pipeline import StoryBuilder
    from autopilot.rehearsal.fakes import LocalHosting

    b = StoryBuilder(cfg, page, tmp_path / "b", LocalHosting(tmp_path / "h"), rehearsal=True)
    key = "simplestorymode-2026-11-02-reel"
    one = b.build(
        Request(key, date(2026, 11, 2), "reel", inject=frozenset({"voice_gate"})),
        PageState(page.name),
        log=lambda _m: None,
    )
    assert one.manifest is not None and one.fallback == "second-voice"
    assert one.manifest.ai_generated and "Voiceover: AI-generated" in one.manifest.caption
    allv = b.build(
        Request(key + "x", date(2026, 11, 2), "reel", inject=frozenset({"voice_gate_all"})),
        PageState(page.name),
        log=lambda _m: None,
    )
    assert allv.manifest is not None and allv.fallback == "music-only"
    assert "Voiceover" not in allv.manifest.caption


def test_unknown_page_kinds_rejected(story: tuple[Config, Page]) -> None:
    cfg, page = story
    from autopilot.errors import ContentError
    from autopilot.pipeline import StoryBuilder
    from autopilot.rehearsal.fakes import LocalHosting

    quotes = dataclasses.replace(page, type="quotes")
    with pytest.raises(ContentError):
        StoryBuilder(cfg, quotes, Path("x"), LocalHosting(Path("y")))


def test_music_reel_with_art_has_readable_panel(story: tuple[Config, Page], tmp_path: Path) -> None:
    """A reel over the owner's (here: deliberately busy) 9:16 art gets a cream panel and passes."""
    from autopilot.pipeline import StoryBuilder
    from autopilot.rehearsal.fakes import LocalHosting
    from story_helpers import make_art

    cfg, page = story
    reel = post_data("r02-art", fmt="reel", n=1, audio="music")
    reel["slides"][0]["text"] = "Rest is not a reward. It is allowed."
    folder = write_post(page, reel)
    make_art(folder / "art-01.png", "busy", size=(1080, 1920))
    b = StoryBuilder(cfg, page, tmp_path / "b", LocalHosting(tmp_path / "h"))
    composed = b.compose_reel(
        b.queue()[0].post, tmp_path / "o", "k", allow_placeholder=False, inject=frozenset()
    )
    assert composed.report.passed, composed.report.summary()
    assert composed.audio_mode == "music" and not composed.placeholder
    caption = b.caption(b.queue()[0].post, narrated=False)
    assert "Illustrations: AI-generated" in caption.text and "Voiceover" not in caption.text


def test_cli_queue_validate_and_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cfg: Config
) -> None:
    from autopilot.__main__ import main

    pid, _ = unillustrated_carousel(cfg.page("simplestorymode"))

    assert main(["validate", "--page", "simplestorymode"]) == 0
    assert main(["queue"]) == 0
    out = capsys.readouterr().out
    assert pid in out and "needs art" in out
    assert main(["preview", "simplestorymode", pid, "--out", str(tmp_path)]) == 0
    assert (tmp_path / "index.html").exists()
    from autopilot.config import ROOT

    preview = ROOT / "pages/simplestorymode/queue" / pid / "preview"
    existed = preview.exists()
    try:
        assert main(["build", "simplestorymode", pid]) == 1  # no art: refused
        assert "NOT APPROVED: art not supplied yet" in capsys.readouterr().out
        assert not (preview.parent / "approved.json").exists()
    finally:
        if not existed:
            shutil.rmtree(preview, ignore_errors=True)
