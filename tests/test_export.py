"""``Export <post-id>``: finished files for posting by hand."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from autopilot.config import Config, Page
from autopilot.errors import ContentError
from autopilot.export import CAPTION, NOTES, export_post
from autopilot.rehearsal.preview import build_and_approve
from story_helpers import make_art, post_data, write_post

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def test_export_carousel_writes_slides_caption_and_notes(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    write_post(page, post_data("c01-real", n=3), art="quiet")
    build_and_approve(cfg, page, "c01-real")
    result = export_post(cfg, page, "c01-real", tmp_path / "exports", NOW, reel=False)

    folder = tmp_path / "exports" / "c01-real"
    assert result.folder == folder and result.media == ["01.jpg", "02.jpg", "03.jpg"]
    names = {p.name for p in folder.iterdir()}
    assert names == {"01.jpg", "02.jpg", "03.jpg", CAPTION, NOTES}
    with Image.open(folder / "01.jpg") as im:
        assert im.size == (1080, 1350)
    caption = (folder / CAPTION).read_text(encoding="utf-8")
    assert caption.startswith("A gentle carousel note")
    assert "Illustrations: AI-generated" in caption
    assert caption.rstrip().endswith("#simplestorymode #healing #feelings")
    notes = (folder / NOTES).read_text(encoding="utf-8")
    assert "AI LABEL: YES" in notes and "illustrations are AI-generated" in notes
    assert "01.jpg (the cover" in notes
    assert "Mon, Wed, Fri at 19:00" in notes and "Europe/London" in notes
    assert not list((tmp_path / "exports").glob(".*partial"))


def test_export_without_ai_art_says_label_not_needed(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    page = dataclasses.replace(page, ai_generated_art=False)
    cfg = dataclasses.replace(cfg, pages=(page,))
    write_post(page, post_data("i01-single", fmt="image", n=1), art="quiet")
    build_and_approve(cfg, page, "i01-single")
    result = export_post(cfg, page, "i01-single", tmp_path, NOW, reel=False)
    assert result.media == ["01.jpg"] and result.ai_label == []
    assert "AI LABEL: not needed" in (result.folder / NOTES).read_text(encoding="utf-8")


def test_export_refuses_unapproved_and_changed_posts(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    folder = write_post(page, post_data("c01-real", n=2), art="quiet")
    with pytest.raises(ContentError, match="not approved"):
        export_post(cfg, page, "c01-real", tmp_path, NOW)
    build_and_approve(cfg, page, "c01-real")
    make_art(folder / "art-02.png", "busy", seed=9)  # art changed after approval
    with pytest.raises(ContentError, match="changed since approval"):
        export_post(cfg, page, "c01-real", tmp_path, NOW)
    with pytest.raises(ContentError, match="no valid post"):
        export_post(cfg, page, "c99-missing", tmp_path, NOW)
    assert not (tmp_path / "c01-real").exists()


def test_export_replaces_its_own_folder_but_not_other_files(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    write_post(page, post_data("c01-real", n=2), art="quiet")
    build_and_approve(cfg, page, "c01-real")
    export_post(cfg, page, "c01-real", tmp_path, NOW, reel=False)
    (tmp_path / "c01-real" / "03.jpg").write_bytes(b"stale")
    export_post(cfg, page, "c01-real", tmp_path, NOW, reel=False)  # replaces the old folder
    assert not (tmp_path / "c01-real" / "03.jpg").exists()

    other = tmp_path / "other"
    (other / "c01-real").mkdir(parents=True)
    (other / "c01-real" / "mine.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ContentError, match="not an earlier export"):
        export_post(cfg, page, "c01-real", other, NOW)
    assert (other / "c01-real" / "mine.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("flags", "reel"), [([], False), (["--reel"], True), (["--no-reel"], False)]
)
def test_export_command_makes_the_story_reel_only_when_asked(
    flags: list[str], reel: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import autopilot.export
    from autopilot.__main__ import main

    seen: dict[str, object] = {}

    def fake_export(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise ContentError("stop here")

    monkeypatch.setattr(autopilot.export, "export_post", fake_export)
    argv = ["export", "simplestorymode", "c01-you-can-miss", "--out", str(tmp_path), *flags]
    assert main(argv) == 1
    assert seen["reel"] is reel
