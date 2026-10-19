"""Story pages: queue files, approval, slide composition, carousel gates and slot building."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image

from autopilot.config import Config, Page
from autopilot.content.queue import (
    approval_problems,
    art_prompt,
    load_queue,
    next_for_slot,
    parse_post,
    publishable,
    read_approval,
    set_hold,
    structure_problems,
    style_paragraph,
    write_approval,
)
from autopilot.errors import ContentError
from autopilot.export import export_post
from autopilot.pipeline import Request, used_post_ids
from autopilot.rehearsal.preview import build_and_approve
from autopilot.render.story import parse_story_text
from autopilot.state import (
    STATUS_FAILED,
    STATUS_INTENT,
    STATUS_PUBLISHED,
    STATUS_SKIPPED,
    PageState,
    PostRecord,
)
from story_helpers import brief, builder, make_art, post_data, write_post


# ----- post.yaml ---------------------------------------------------------------------------------
def test_valid_post_parses(story: tuple[Config, Page]) -> None:
    _, page = story
    post, problems = parse_post(write_post(page, post_data("c01-test")))
    assert post is not None and problems == []
    assert [s.art for s in post.slides] == ["art-01.png", "art-02.png", "art-03.png"]
    assert post.plain_texts[0] == "You can miss someone and still let go."


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        ({"id": "Bad_Id"}, "bad id"),
        ({"id": "c99-other"}, "folder name"),
        ({"format": "story"}, "format"),
        ({"caption": "Hello #tag"}, "hashtags"),
        ({"hashtags": ["nohash"]}, "hashtags"),
        ({"hashtags": ["#a1", "#a1", "#b2"]}, "duplicate"),
        ({"slides": []}, "slides"),
        ({"slides": [{"text": "**open", "brief": brief()}]}, "unbalanced"),
        ({"slides": [{"text": "x", "position": "left", "brief": brief()}]}, "position"),
        ({"slides": [{"text": "x", "art": "art-02.png", "brief": brief()}]}, "art-01"),
        ({"slides": [{"text": "x", "brief": {"scene": "s"}}]}, "brief.emotion"),
        ({"slides": [{"text": "x", "brief": brief(["elf"])}]}, "characters"),
    ],
)
def test_invalid_posts_rejected(
    story: tuple[Config, Page], change: dict[str, Any], expect: str
) -> None:
    _, page = story
    data = post_data("c01-test", **change)
    folder = page.dir / "queue" / "c01-test"
    folder.mkdir(parents=True)
    (folder / "post.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    post, problems = parse_post(folder)
    assert post is None and any(expect in p for p in problems), problems


def test_structure_rules(story: tuple[Config, Page]) -> None:
    _, page = story
    one, _ = parse_post(write_post(page, post_data("c01-one", n=1)))
    eleven, _ = parse_post(write_post(page, post_data("c02-eleven", n=11)))
    wordy = post_data("c03-wordy")
    wordy["slides"][0]["text"] = " ".join(["word"] * 17)
    long_cover, _ = parse_post(write_post(page, wordy))
    image2, _ = parse_post(write_post(page, post_data("i01-two", fmt="image", n=2)))
    assert one and eleven and long_cover and image2
    assert structure_problems(one, (2, 10), 16)
    assert structure_problems(eleven, (2, 10), 16)
    assert any("17 words" in p for p in structure_problems(long_cover, (2, 10), 16))
    assert structure_problems(image2, (2, 10), 16)


def test_story_markup() -> None:
    paras = parse_story_text("You can **miss** them,\nstill.\n\nIt **means you**, loved.")
    assert len(paras) == 2 and len(paras[0]) == 2
    assert paras[0][0] == [("You", False), ("can", False), ("miss", True), ("them,", False)]
    assert paras[1][0] == [("It", False), ("means", True), ("you,", True), ("loved.", False)]


def test_art_prompt_always_forbids_text_and_elf_ears(story: tuple[Config, Page]) -> None:
    _, page = story
    post, _ = parse_post(write_post(page, post_data("c01-test")))
    assert post is not None
    chars = yaml.safe_load((page.dir / "characters.yaml").read_text(encoding="utf-8"))
    prompt = art_prompt(post, post.slides[0], chars)
    assert "No text, no letters" in prompt and "not elf ears" in prompt
    assert "olive-green long coat with gold leaf embroidery" in prompt
    assert "4:5" in prompt and "Keep the top of the picture calm" in prompt


def test_art_prompt_starts_with_locked_watercolour_style(story: tuple[Config, Page]) -> None:
    _, page = story
    post, _ = parse_post(write_post(page, post_data("c01-test", n=2)))
    assert post is not None
    chars = yaml.safe_load((page.dir / "characters.yaml").read_text(encoding="utf-8"))
    style = style_paragraph(chars)
    assert style.startswith("Soft watercolour storybook illustration")
    for word in (
        "golden autumn light",
        "nostalgic",
        "cream",
        "olive",
        "rust",
        "gold",
        "brown",
        "navy",
        "rounded human ears",
        "No text",
    ):
        assert word in style
    for slide in post.slides:
        assert art_prompt(post, slide, chars).startswith(style)
    banned = (
        "ghibli",
        "miyazaki",
        "disney",
        "pixar",
        "dreamworks",
        "potter",
        "studio",
        "in the style of",
    )
    text = (page.dir / "characters.yaml").read_text(encoding="utf-8").lower()
    prompts = " ".join(art_prompt(post, sl, chars) for sl in post.slides).lower()
    assert not [b for b in banned if b in prompts]
    assert "never name a studio" in text  # the rule is written next to the style


# ----- composition and gates -------------------------------------------------------------------
def test_placeholder_carousel_passes_with_one_text_size(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    b = builder(story, tmp_path)
    post, _ = parse_post(write_post(b.page, post_data("c01-test", n=4)))
    assert post is not None
    composed, caption, text_report = b.compose(post, tmp_path / "o", "k", allow_placeholder=True)
    assert text_report.passed and composed.report.passed, composed.report.summary()
    assert composed.placeholder and len(composed.paths) == 4
    sizes = [s.px for s in composed.slides]
    assert len(set(sizes[1:])) == 1 and sizes[0] >= sizes[1] >= 60
    assert all(Image.open(p).size == (1080, 1350) for p in composed.paths)
    assert "Illustrations: AI-generated" in caption.text
    assert caption.text.endswith("#simplestorymode #healing #feelings")


def test_placeholder_never_approvable(story: tuple[Config, Page], tmp_path: Path) -> None:
    b = builder(story, tmp_path)
    post, _ = parse_post(write_post(b.page, post_data("c01-test")))
    assert post is not None
    composed = b.compose_slides(post, tmp_path / "o", allow_placeholder=True)
    assert composed.report.passed
    with pytest.raises(ContentError):
        b.compose_slides(post, tmp_path / "o2", allow_placeholder=False)
    with pytest.raises(ContentError):
        write_approval(post, "x")


def test_busy_art_gets_a_panel_quiet_art_does_not(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    b = builder(story, tmp_path)
    busy, _ = parse_post(write_post(b.page, post_data("c01-busy"), art="busy"))
    quiet, _ = parse_post(write_post(b.page, post_data("c02-quiet"), art="quiet"))
    assert busy and quiet
    noisy = b.compose_slides(busy, tmp_path / "b", allow_placeholder=False)
    calm = b.compose_slides(quiet, tmp_path / "q", allow_placeholder=False)
    assert noisy.report.passed and calm.report.passed, noisy.report.summary()
    assert all(s.panel_opacity >= 0.55 for s in noisy.slides)
    assert all(s.panel_opacity == 0.0 for s in calm.slides)


def test_small_art_fails_resolution_gate(story: tuple[Config, Page], tmp_path: Path) -> None:
    b = builder(story, tmp_path)
    folder = write_post(b.page, post_data("c01-small"), art="quiet")
    make_art(folder / "art-02.png", "quiet", size=(500, 625))
    post, _ = parse_post(folder)
    assert post is not None
    report = b.compose_slides(post, tmp_path / "o", allow_placeholder=False).report
    assert [r.name for r in report.failures] == ["slide2:art-resolution"]


def test_too_much_text_fails_fit(story: tuple[Config, Page], tmp_path: Path) -> None:
    b = builder(story, tmp_path)
    data = post_data("c01-long")
    data["slides"][1]["text"] = "\n".join(["Line of words here"] * 12)
    post, _ = parse_post(write_post(b.page, data))
    assert post is not None
    report = b.compose_slides(post, tmp_path / "o", allow_placeholder=True).report
    assert not report.passed and report.failures[0].name == "text:fit"


def test_text_gates(story: tuple[Config, Page], tmp_path: Path) -> None:
    b = builder(story, tmp_path)
    data = post_data("c01-typo", caption="A gentel note.")
    data["hashtags"] = ["#like4like", "#a1", "#b2"]
    post, _ = parse_post(write_post(b.page, data))
    assert post is not None
    report = b.text_gates(post, b.caption(post, False))
    failed = {r.name for r in report.failures}
    assert failed == {"text:spelling", "caption:banned-hashtags"}


def test_captions_must_be_unique_in_queue(story: tuple[Config, Page], tmp_path: Path) -> None:
    b = builder(story, tmp_path)
    write_post(b.page, post_data("c01-a", caption="Same words."))
    post, _ = parse_post(write_post(b.page, post_data("c02-b", caption="Same  WORDS.")))
    assert post is not None
    report = b.text_gates(post, b.caption(post, False))
    assert [r.name for r in report.failures] == ["caption:unique"]


# ----- approval --------------------------------------------------------------------------------
def test_build_approves_and_detects_later_changes(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    folder = write_post(page, post_data("c01-real"), art="busy")
    preview, index = build_and_approve(cfg, page, "c01-real")
    assert preview.passed and index.exists()
    approval = read_approval(folder)
    assert approval is not None and set(approval.inputs) == {
        "post.yaml",
        "art-01.png",
        "art-02.png",
        "art-03.png",
    }
    items, _ = load_queue(page.dir)
    assert items[0].publishable
    # Same text with Windows line endings: still the same approval.
    lf = (folder / "post.yaml").read_bytes().replace(b"\r\n", b"\n")
    for ending in (b"\n", b"\r\n"):
        (folder / "post.yaml").write_bytes(lf.replace(b"\n", ending))
        assert load_queue(page.dir)[0][0].publishable
    # Any real change (here: new art) holds the post back until it is built again.
    make_art(folder / "art-02.png", "quiet")
    item = load_queue(page.dir)[0][0]
    assert not item.publishable and item.approval is not None
    assert any("changed since approval" in p for p in approval_problems(item.post, item.approval))


def test_hold_blocks_a_post_and_release_restores_it(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    folder = write_post(page, post_data("c01-real", n=2), art="quiet")
    build_and_approve(cfg, page, "c01-real")
    text = (folder / "post.yaml").read_text(encoding="utf-8")
    set_hold(folder, "check the ears on slide 2")
    item = load_queue(page.dir)[0][0]
    assert item.post.hold == "check the ears on slide 2"
    assert not item.publishable and item.problems == ["on hold: check the ears on slide 2"]
    assert publishable(load_queue(page.dir)[0], set()) == []
    with pytest.raises(ContentError, match="on hold"):
        export_post(cfg, page, "c01-real", tmp_path, reel=False)
    set_hold(folder, None)  # release: the approval is still valid (the hold line is not content)
    assert load_queue(page.dir)[0][0].publishable
    assert (folder / "post.yaml").read_text(encoding="utf-8").strip() == text.strip()
    (folder / "post.yaml").write_text(text + "hold: true\n", encoding="utf-8")  # by hand, too
    assert load_queue(page.dir)[0][0].post.hold == "put on hold by the owner"
    (folder / "post.yaml").write_text(text + "hold: [1, 2]\n", encoding="utf-8")
    assert load_queue(page.dir)[1]  # invalid hold value: the post does not even load


def test_build_refuses_without_art(story: tuple[Config, Page]) -> None:
    cfg, page = story
    folder = write_post(page, post_data("c01-noart"))
    with pytest.raises(ContentError, match="art not supplied"):
        build_and_approve(cfg, page, "c01-noart")
    assert not (folder / "approved.json").exists()
    assert (folder / "preview" / "index.html").exists()  # placeholders shown for review


def test_build_refuses_failing_gates(story: tuple[Config, Page]) -> None:
    cfg, page = story
    write_post(page, post_data("c01-typo", caption="A gentel note."), art="busy")
    with pytest.raises(ContentError, match="gates failed"):
        build_and_approve(cfg, page, "c01-typo")
    assert read_approval(page.dir / "queue" / "c01-typo") is None


# ----- queue order and slot building -----------------------------------------------------------
def approve_all(cfg: Config, page: Page, *ids: str) -> None:
    for pid in ids:
        build_and_approve(cfg, page, pid)


def test_queue_order_and_formats(story: tuple[Config, Page]) -> None:
    cfg, page = story
    write_post(page, post_data("c01-a"), art="busy")
    write_post(page, post_data("c02-b", priority=5), art="busy")
    write_post(page, post_data("i01-c", fmt="image", n=1), art="busy")
    write_post(page, post_data("c03-unapproved"), art="busy")
    approve_all(cfg, page, "c01-a", "c02-b", "i01-c")
    items, _ = load_queue(page.dir)
    first = next_for_slot(items, set(), "carousel")
    assert first is not None and first.post.id == "c02-b"  # lower priority number first
    nxt = next_for_slot(items, {"c02-b"}, "carousel")
    assert nxt is not None and nxt.post.id == "c01-a"
    assert next_for_slot(items, {"c02-b", "c01-a"}, "carousel") is None  # never the draft
    image = next_for_slot(items, set(), "image")
    assert image is not None and image.post.id == "i01-c"


def test_used_post_ids() -> None:
    state = PageState("simplestorymode")
    for key, status, cid, container in [
        ("k1", STATUS_PUBLISHED, "c01", "x"),
        ("k2", STATUS_INTENT, "c02", ""),  # outcome unknown: reserved
        ("k3", STATUS_FAILED, "c03", ""),  # never reached Instagram: reusable
        ("k4", STATUS_FAILED, "c04", "c9"),  # container existed: reserved
        ("k5", STATUS_SKIPPED, "", ""),
    ]:
        state.upsert(
            PostRecord(
                key, "2026-10-20", "carousel", status, content_id=cid, container_id=container
            )
        )
    assert used_post_ids(state) == {"c01", "c02", "c04"}
    assert used_post_ids(state, except_slot="k2") == {"c01", "c04"}


def test_build_slot_uses_next_approved_and_skips_when_empty(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    write_post(page, post_data("c01-a"), art="busy")
    approve_all(cfg, page, "c01-a")
    b = builder(story, tmp_path)
    state = PageState(page.name)
    key = "simplestorymode-2026-11-02-carousel"
    result = b.build(Request(key, date(2026, 11, 2), "carousel"), state, log=lambda _m: None)
    m = result.manifest
    assert m is not None and m.content_id == "c01-a" and m.format == "carousel"
    assert [u.rsplit("/", 1)[1] for u in m.media_urls] == [
        f"{key}-01.jpg",
        f"{key}-02.jpg",
        f"{key}-03.jpg",
    ]
    assert m.ai_generated and m.template == "art"
    assert result.queue_left == 0
    # Same slot retried later: the same post again (so reconciliation can match the caption).
    state.upsert(PostRecord(key, "2026-11-02", "carousel", STATUS_INTENT, content_id="c01-a"))
    again = b.build(Request(key, date(2026, 11, 2), "carousel"), state, log=lambda _m: None)
    assert again.manifest is not None and again.manifest.caption == m.caption
    # Next slot: the only post is used, nothing else may be posted.
    nxt = b.build(
        Request("simplestorymode-2026-11-04-carousel", date(2026, 11, 4), "carousel"),
        state,
        log=lambda _m: None,
    )
    assert nxt.manifest is None and nxt.skipped_reason.startswith("queue empty")
    image = b.build(
        Request("simplestorymode-2026-11-03-single", date(2026, 11, 3), "image"),
        PageState(page.name),
        log=lambda _m: None,
    )
    assert image.manifest is None and "image post" in image.skipped_reason


def test_unapproved_drafts_only_post_in_rehearsal(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    write_post(page, post_data("c01-draft"))  # no art, no approval
    req = Request("simplestorymode-2026-11-02-carousel", date(2026, 11, 2), "carousel")
    real = builder(story, tmp_path).build(req, PageState(page.name), log=lambda _m: None)
    assert real.manifest is None
    practice = builder(story, tmp_path, rehearsal=True).build(
        req, PageState(page.name), log=lambda _m: None
    )
    assert practice.manifest is not None and practice.manifest.template == "placeholder"


def test_changed_post_reported_and_held(story: tuple[Config, Page], tmp_path: Path) -> None:
    cfg, page = story
    folder = write_post(page, post_data("c01-a"), art="busy")
    approve_all(cfg, page, "c01-a")
    approval = json.loads((folder / "approved.json").read_text(encoding="utf-8"))
    approval["inputs"]["post.yaml"] = "0" * 64
    (folder / "approved.json").write_text(json.dumps(approval), encoding="utf-8")
    req = Request("simplestorymode-2026-11-02-carousel", date(2026, 11, 2), "carousel")
    result = builder(story, tmp_path).build(req, PageState(page.name), log=lambda _m: None)
    assert result.manifest is None and result.changed == ["c01-a"]
