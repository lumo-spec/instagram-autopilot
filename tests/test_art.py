"""Automatic art: generate -> vision check -> save, retries, cost cap, alerts, providers."""

from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from PIL import Image

from autopilot.__main__ import main
from autopilot.art import factory
from autopilot.art.budget import Ledger
from autopilot.art.clients import (
    CheckSpec,
    GeminiChecker,
    GeminiImageClient,
    NoImageError,
    OpenAIChecker,
    OpenAIImageClient,
    RefImage,
    check_prompt,
    model_exists,
    parse_verdict,
)
from autopilot.art.fakes import FakeChecker, FakeImageClient
from autopilot.art.generate import (
    ArtGenerator,
    ReferenceLibrary,
    check_problems,
    fit_to_canvas,
    read_checks,
)
from autopilot.art.http import HttpResponse, call
from autopilot.art.keys import load_key, read_env_file
from autopilot.config import Config, Page
from autopilot.content.queue import find_post, input_hashes, set_hold, write_approval
from autopilot.errors import ArtError, BudgetError, ConfigError, ContentError
from autopilot.rehearsal.preview import build_and_approve
from story_helpers import make_art, post_data, write_post

KEY = "test-key-0123456789abcdefghij"
LONDON = ZoneInfo("Europe/London")


def png(
    size: tuple[int, int] = (800, 1000), colour: tuple[int, int, int] = (200, 180, 150)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def chars(page: Page) -> dict[str, Any]:
    return dict(yaml.safe_load((page.dir / "characters.yaml").read_text(encoding="utf-8")))


def sheets(tmp_path: Path, page: Page) -> Path:
    folder = tmp_path / "refs"
    folder.mkdir(parents=True)
    for name in ("rowan", "wren"):
        (folder / chars(page)["characters"][name]["reference"]).write_bytes(png((300, 200)))
    (folder / "private face.png").write_bytes(png((50, 50)))
    return folder


def generator(
    story: tuple[Config, Page],
    tmp_path: Path,
    script: list[str] | None = None,
    refuse: set[int] | None = None,
    cap: float = 10.0,
) -> ArtGenerator:
    _, page = story
    c = chars(page)
    return ArtGenerator(
        page.title,
        c,
        FakeImageClient(refuse=refuse or set()),
        FakeChecker(script=list(script or [])),
        Ledger(tmp_path / "spend.json", cap, 0.8, LONDON),
        ReferenceLibrary(c, sheets(tmp_path, page), 512),
        (1080, 1350),
        3,
        log=lambda _m: None,
    )


# ----- the generate -> check -> save flow ------------------------------------------------------
def test_generates_checks_and_records_every_slide(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    write_post(page, post_data("c01-test", n=3))
    gen = generator(story, tmp_path)
    post = find_post(page.dir, "c01-test")
    result = gen.generate(post)
    assert result.status == "done"
    assert [s.status for s in result.slides] == ["generated"] * 3
    checks = read_checks(post.dir)
    for n in (1, 2, 3):
        art = post.dir / f"art-{n:02d}.png"
        with Image.open(art) as img:
            assert img.size == (1080, 1350)
        assert checks[art.name]["result"] == "pass"
        assert checks[art.name]["generated_by"] == "fake/painter"
    assert check_problems(post) == []
    painter = gen.painter
    assert isinstance(painter, FakeImageClient)
    assert "Soft watercolour storybook illustration" in painter.prompts[0]
    assert "Use the attached character sheet for Wren" in painter.prompts[0]
    assert "not pointed, not elf ears" in painter.prompts[0]
    assert painter.refs_seen[0] == ["Wren character sheet"]
    assert painter.refs_seen[1] == [
        "Wren character sheet",
        "earlier slide of this story (style only)",
    ]
    checker = gen.checker
    assert isinstance(checker, FakeChecker)
    assert checker.seen[0].characters[0].name == "Wren"
    assert gen.ledger.spent_gbp() == pytest.approx(3 * (0.14 + 0.01) * 0.8)
    assert result.alerts.resolved == ["art-failed-simple-story-mode-c01-test"]


def test_rejected_picture_is_redrawn_with_the_reason(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    write_post(page, post_data("i01-test", fmt="image", n=1))
    gen = generator(story, tmp_path, script=["ears", "text", "pass"])
    result = gen.generate(find_post(page.dir, "i01-test"))
    assert result.status == "done"
    assert result.slides[0].attempts == 3
    painter = gen.painter
    assert isinstance(painter, FakeImageClient)
    assert "rejected for: pointed or elf ears" in painter.prompts[1]
    assert "rejected for: visible text or letters" in painter.prompts[2]
    rejected = page.dir / "queue" / "i01-test" / "preview" / "rejected"
    assert sorted(p.name for p in rejected.iterdir()) == [
        "art-01-try1.png",
        "art-01-try1.txt",
        "art-01-try2.png",
        "art-01-try2.txt",
    ]


def test_after_three_regenerations_the_post_is_skipped_and_alerted(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    write_post(page, post_data("c02-test", n=3))
    gen = generator(story, tmp_path, script=["pass", "mismatch", "ears", "text", "mismatch"])
    post = find_post(page.dir, "c02-test")
    result = gen.generate(post)
    assert result.status == "failed"
    assert (post.dir / "art-01.png").is_file()
    assert not (post.dir / "art-02.png").exists()  # never saved: it never passed
    assert not (post.dir / "art-03.png").exists()  # the post was skipped: no more spending
    painter = gen.painter
    assert isinstance(painter, FakeImageClient)
    assert len(painter.prompts) == 5  # slide 1 once, slide 2: 1 + 3 regenerations
    (alert,) = result.alerts.raise_
    assert alert.key == "art-failed-simple-story-mode-c02-test"
    assert "skipped" in alert.title
    assert "pointed or elf ears" in alert.body
    assert result.alerts.resolved == []


def test_provider_refusal_counts_as_a_try(story: tuple[Config, Page], tmp_path: Path) -> None:
    _, page = story
    write_post(page, post_data("i02-test", fmt="image", n=1))
    gen = generator(story, tmp_path, refuse={1})
    result = gen.generate(find_post(page.dir, "i02-test"))
    assert result.status == "done"
    assert result.slides[0].attempts == 2
    assert gen.ledger.spent_gbp() == pytest.approx((0.14 + 0.01) * 0.8)  # refusal not charged


def test_cost_cap_stops_before_a_paid_call(story: tuple[Config, Page], tmp_path: Path) -> None:
    _, page = story
    write_post(page, post_data("c03-test", n=3))
    gen = generator(story, tmp_path, cap=0.25)  # room for two pictures (0.12 each)
    result = gen.generate(find_post(page.dir, "c03-test"))
    assert result.status == "budget"
    assert "monthly art budget reached" in result.message
    painter = gen.painter
    assert isinstance(painter, FakeImageClient)
    assert len(painter.prompts) == 2
    assert result.alerts.raise_[0].key == "art-budget-simple-story-mode"
    assert gen.ledger.spent_gbp() <= 0.25


def test_held_and_approved_posts_are_left_alone(story: tuple[Config, Page], tmp_path: Path) -> None:
    _, page = story
    held = write_post(page, post_data("i03-test", fmt="image", n=1))
    set_hold(held, "not yet")
    done = write_post(page, post_data("i04-test", fmt="image", n=1), art="quiet")
    write_approval(find_post(page.dir, "i04-test"), "ok")
    gen = generator(story, tmp_path)
    assert gen.generate(find_post(page.dir, "i03-test")).status == "skipped"
    assert gen.generate(find_post(page.dir, "i04-test")).status == "nothing-to-do"
    assert not (held / "art-01.png").exists()
    assert (done / "art-01.png").is_file()


def test_redo_replaces_a_slide_only_after_it_passes(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    folder = write_post(page, post_data("c04-test", n=2), art="quiet")
    before = (folder / "art-02.png").read_bytes()
    gen = generator(story, tmp_path, script=["ears"] * 4)
    result = gen.generate(find_post(page.dir, "c04-test"), redo={2})
    assert result.status == "failed"
    assert (folder / "art-02.png").read_bytes() == before
    result = generator(story, tmp_path / "again").generate(
        find_post(page.dir, "c04-test"), redo={2}
    )
    assert result.status == "done"
    assert (folder / "art-02.png").read_bytes() != before


def test_missing_reference_sheet_stops_without_spending(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    write_post(page, post_data("i05-test", fmt="image", n=1))
    c = chars(page)
    gen = ArtGenerator(
        page.title,
        c,
        FakeImageClient(),
        FakeChecker(),
        Ledger(tmp_path / "spend.json", 10, 0.8, LONDON),
        ReferenceLibrary(c, tmp_path / "nowhere", 512),
        (1080, 1350),
        3,
        log=lambda _m: None,
    )
    result = gen.generate(find_post(page.dir, "i05-test"))
    assert result.status == "failed"
    assert "reference sheet for wren is not on this PC" in result.message
    assert gen.ledger.spent_gbp() == 0


def test_reference_library_reads_only_the_named_sheets(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    _, page = story
    lib = ReferenceLibrary(chars(page), sheets(tmp_path, page), 128)
    ref = lib.sheet("rowan")
    assert ref.label == "Rowan character sheet"
    with Image.open(io.BytesIO(ref.data)) as img:
        assert max(img.size) <= 128
    with pytest.raises(ArtError):
        lib.sheet("rowan_battle_king")


# ----- Build refuses art that is not what was checked --------------------------------------------
def test_build_refuses_generated_art_changed_after_its_check(
    story: tuple[Config, Page], tmp_path: Path
) -> None:
    cfg, page = story
    write_post(page, post_data("i06-test", fmt="image", n=1))
    gen = generator(story, tmp_path)
    post = find_post(page.dir, "i06-test")
    assert gen.generate(post).status == "done"
    assert "art-check.json" in input_hashes(post)
    make_art(post.dir / "art-01.png", "quiet")  # swapped after the check
    assert check_problems(post)
    with pytest.raises(ContentError, match="not the picture that passed"):
        build_and_approve(cfg, page, "i06-test")
    assert not (post.dir / "approved.json").exists()


def test_fit_to_canvas_crops_to_four_by_five() -> None:
    for size in ((1024, 1536), (1856, 2304), (2000, 1000)):
        out = fit_to_canvas(png(size), (1080, 1350))
        with Image.open(io.BytesIO(out)) as img:
            assert img.size == (1080, 1350)
    with pytest.raises(NoImageError):
        fit_to_canvas(b"not a picture", (1080, 1350))


# ----- budget ----------------------------------------------------------------------------------
def test_ledger_is_per_month(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 31, 23, 30, tzinfo=UTC)]  # GMT after the clock change
    ledger = Ledger(tmp_path / "s.json", 1.0, 0.8, LONDON, now=lambda: now[0])
    ledger.add(1.0, "images")
    assert ledger.month() == "2026-10"
    assert ledger.spent_gbp() == pytest.approx(0.8)
    with pytest.raises(BudgetError):
        ledger.ensure(0.3)
    now[0] = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)
    assert ledger.month() == "2026-11"
    assert ledger.spent_gbp() == 0
    ledger.ensure(1.25)
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["months"]["2026-10"]["images"] == 1


# ----- keys ------------------------------------------------------------------------------------
def test_key_from_environment_or_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nOTHER=1\nIMAGE_API_KEY="from-file"\n', encoding="utf-8-sig")
    assert read_env_file(env)["IMAGE_API_KEY"] == "from-file"
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    assert load_key("IMAGE_API_KEY", env) == "from-file"
    monkeypatch.setenv("IMAGE_API_KEY", "from-env")
    assert load_key("IMAGE_API_KEY", env) == "from-env"
    with pytest.raises(ArtError, match="set-image-key"):
        load_key("CHECK_API_KEY", tmp_path / "none")


# ----- providers (fake transport: no network) ----------------------------------------------------
class Recorder:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> HttpResponse:
        self.requests.append((method, url, headers, body))
        return self.responses.pop(0)


def ok(data: Any) -> HttpResponse:
    return HttpResponse(200, json.dumps(data).encode())


def gemini_image(data: bytes, thought: bool = False) -> dict[str, Any]:
    return {
        "inlineData": {"mimeType": "image/png", "data": base64.b64encode(data).decode()},
        "thought": thought,
    }


def test_gemini_image_request_and_answer() -> None:
    final = png((900, 1125))
    rec = Recorder(
        ok(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                gemini_image(png((10, 10)), True),
                                {"text": "ok"},
                                gemini_image(final),
                            ]
                        }
                    }
                ]
            }
        )
    )
    client = GeminiImageClient(KEY, "gemini-3-pro-image", "4:5", "2K", 0.14, rec)
    ref = RefImage("Wren character sheet", "image/jpeg", b"jpg")
    got = client.generate("draw", [ref])
    assert got.data == final
    method, url, headers, body = rec.requests[0]
    assert method == "POST" and url.endswith("/models/gemini-3-pro-image:generateContent")
    assert KEY not in url and headers["x-goog-api-key"] == KEY
    sent = json.loads(body or b"")
    assert sent["generationConfig"]["imageConfig"] == {"aspectRatio": "4:5", "imageSize": "2K"}
    parts = sent["contents"][0]["parts"]
    assert parts[0]["text"] == "Reference image: Wren character sheet"
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[-1]["text"] == "draw"
    assert KEY not in repr(client)


def test_gemini_draws_nothing_or_fails() -> None:
    rec = Recorder(ok({"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]}))
    client = GeminiImageClient(KEY, "m", "4:5", "2K", 0.14, rec)
    with pytest.raises(NoImageError, match="IMAGE_SAFETY"):
        client.generate("draw", [])
    rec = Recorder(ok({"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(NoImageError, match="SAFETY"):
        GeminiImageClient(KEY, "m", "4:5", "2K", 0.14, rec).generate("draw", [])
    rec = Recorder(HttpResponse(401, b'{"error": "secret echo"}'))
    with pytest.raises(ArtError) as exc:
        GeminiImageClient(KEY, "m", "4:5", "2K", 0.14, rec).generate("draw", [])
    assert "HTTP 401 (key not accepted)" in str(exc.value)
    assert KEY not in str(exc.value) and "secret echo" not in str(exc.value)


def test_http_retries_rate_limits_then_gives_up() -> None:
    waits: list[float] = []
    rec = Recorder(HttpResponse(429, b""), HttpResponse(0, b""), ok({"a": 1}))
    resp = call(rec, "GET", "https://x.test", {}, None, "t", sleep=waits.append)
    assert resp.json() == {"a": 1} and waits == [5.0, 20.0]
    rec = Recorder(*[HttpResponse(503, b"")] * 4)
    with pytest.raises(ArtError, match="HTTP 503"):
        call(rec, "GET", "https://x.test", {}, None, "t", sleep=waits.append)
    rec = Recorder(HttpResponse(404, b""))
    with pytest.raises(ArtError, match="model not found"):
        model_exists("gemini", "nope", KEY, rec)


def test_gemini_checker_verdicts() -> None:
    answer = {
        "pointed_or_elf_ears": False,
        "visible_text": True,
        "characters_match": True,
        "problems": ["a sign"],
    }
    rec = Recorder(ok({"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}))
    checker = GeminiChecker(KEY, "gemini-3-flash-preview", 0.01, rec)
    verdict = checker.check(
        png(), [RefImage("Wren character sheet", "image/jpeg", b"j")], CheckSpec()
    )
    assert not verdict.passed
    assert verdict.reasons == ["visible text or letters", "a sign"]
    sent = json.loads(rec.requests[0][3] or b"")
    assert sent["generationConfig"]["responseMimeType"] == "application/json"
    images = [p for p in sent["contents"][0]["parts"] if "inline_data" in p]
    assert images[0]["inline_data"]["mime_type"] == "image/png"  # the candidate comes first
    assert len(images) == 2


def test_verdict_parsing_is_strict() -> None:
    assert not parse_verdict(None).passed
    assert not parse_verdict({"pointed_or_elf_ears": False, "visible_text": False}).passed
    assert not parse_verdict(
        {"pointed_or_elf_ears": "no", "visible_text": False, "characters_match": True}
    ).passed
    good = parse_verdict(
        {
            "pointed_or_elf_ears": False,
            "visible_text": False,
            "characters_match": True,
            "problems": [],
        }
    )
    assert good.passed and good.reasons == []


def test_check_prompt_names_the_rules_and_characters(story: tuple[Config, Page]) -> None:
    from autopilot.art.clients import CharacterSpec

    text = check_prompt(
        CheckSpec((CharacterSpec("Wren", ("navy blue scarf",)),), "a bridge"),
        ["Wren character sheet"],
    )
    assert "pointed_or_elf_ears" in text and "visible_text" in text
    assert "Expected character Wren: navy blue scarf." in text
    assert "no named character" in check_prompt(CheckSpec(), [])


def test_openai_clients() -> None:
    b64 = base64.b64encode(png()).decode()
    rec = Recorder(ok({"data": [{"b64_json": b64}]}), ok({"data": [{"b64_json": b64}]}))
    client = OpenAIImageClient(KEY, "gpt-image-1.5", "1024x1536", "high", 0.25, rec)
    client.generate("draw", [RefImage("Rowan character sheet", "image/jpeg", b"jpgdata")])
    client.generate("draw", [])
    (_, url1, h1, body1), (_, url2, _h2, body2) = rec.requests
    assert url1.endswith("/images/edits") and h1["Content-Type"].startswith("multipart/form-data")
    assert b'name="image[]"' in (body1 or b"") and b"jpgdata" in (body1 or b"")
    assert url2.endswith("/images/generations") and json.loads(body2 or b"")["size"] == "1024x1536"
    assert h1["Authorization"] == f"Bearer {KEY}"
    with pytest.raises(NoImageError):
        OpenAIImageClient(KEY, "m", "s", "q", 0.25, Recorder(ok({"data": []}))).generate("d", [])
    answer = {"pointed_or_elf_ears": True, "visible_text": False, "characters_match": True}
    rec = Recorder(ok({"choices": [{"message": {"content": json.dumps(answer)}}]}))
    verdict = OpenAIChecker(KEY, "gpt-5-mini", 0.01, rec).check(png(), [], CheckSpec())
    assert verdict.reasons == ["pointed or elf ears"]


# ----- config, factory and CLI --------------------------------------------------------------------
def test_settings_and_keys(
    raw_cfg: dict[str, Any], make_cfg: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    art = factory.settings(make_cfg())
    assert art["provider"] == "gemini" and art["monthly_cap_gbp"] == 10.0
    raw_cfg["art_generation"]["provider"] = "midjourney"
    with pytest.raises(ConfigError):
        factory.settings(make_cfg())
    monkeypatch.setenv("IMAGE_API_KEY", "shared-key-0123456789")
    monkeypatch.delenv("CHECK_API_KEY", raising=False)
    assert (
        factory.keys({"provider": "gemini", "checker": "gemini"}) == ("shared-key-0123456789",) * 2
    )
    with pytest.raises(ArtError, match="CHECK_API_KEY"):
        factory.keys({"provider": "gemini", "checker": "openai"})
    assert factory.keys({"provider": "fake", "checker": "fake"}) == ("", "")
    painter = factory.make_painter(art, "k" * 30, Recorder())
    assert painter.name == "gemini/gemini-3-pro-image"
    other = factory.make_painter({**art, "provider": "openai"}, "k" * 30, Recorder())
    assert other.name == "openai/gpt-image-1.5"
    assert (
        factory.make_checker({**art, "checker": "openai"}, "k", Recorder()).name
        == "openai/gpt-5-mini"
    )


def test_cli_fake_rehearsal_never_touches_the_real_queue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cfg: Config
) -> None:
    real = cfg.page("simplestorymode").dir / "queue" / "c01-you-can-miss"
    before = sorted(p.name for p in real.iterdir())
    out = tmp_path / "rehearsal"
    assert (
        main(["generate-art", "simplestorymode", "c01-you-can-miss", "--fake", "--out", str(out)])
        == 0
    )
    text = capsys.readouterr().out
    assert "c01-you-can-miss: DONE" in text
    copy = out / "page" / "queue" / "c01-you-can-miss"
    assert sorted(p.name for p in copy.glob("art-*.png")) == [
        f"art-{n:02d}.png" for n in range(1, 7)
    ]
    assert (copy / "art-check.json").is_file()
    assert (out / "preview" / "index.html").is_file()
    assert sorted(p.name for p in real.iterdir()) == before


def test_cli_dry_run_shows_prompts_and_cost(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate-art", "simplestorymode", "c01-you-can-miss", "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert text.count("--- c01-you-can-miss art-") == 6
    assert "6 pictures to draw" in text and "Nothing was sent" in text
    assert main(["generate-art", "simplestorymode"]) == 2  # no post named


def test_art_generation_environment_is_allowed_in_the_audit() -> None:
    import sys

    from autopilot.config import ROOT

    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_workflows

    assert "IMAGE_API_KEY" in audit_workflows._secrets_for("art-generation")
    assert "IMAGE_API_KEY" not in audit_workflows._secrets_for("page-simplestorymode")


def test_key_check_uses_free_model_lookups(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_API_KEY", KEY)
    rec = Recorder(ok({"name": "a"}), ok({"name": "b"}))
    assert factory.check_models(cfg, rec) == [
        "gemini/gemini-3-pro-image",
        "gemini/gemini-3-flash-preview",
    ]
    assert all(m == "GET" and KEY not in u for m, u, _, _ in rec.requests)


def test_alerts_are_e_mailed_through_the_owners_github_login(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from autopilot.ops import alerts
    from autopilot.ops.issues import SyncResult

    plan = alerts.AlertPlan()
    assert factory.send_alerts(cfg, plan) == ""
    plan.add(alerts.art_failed("Simple Story Mode", "c01-x", 2, "pointed or elf ears"))

    def no_gh(*_a: Any, **_k: Any) -> Any:
        raise OSError("gh missing")

    monkeypatch.setattr(subprocess, "run", no_gh)
    assert "could not send" in factory.send_alerts(cfg, plan)

    class Done:
        stdout = "gho_" + "x" * 36 + "\n"

    sent: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: Done())
    monkeypatch.setattr(
        factory,
        "sync",
        lambda gh, p, mention, url, today: (
            sent.append((gh.repo, p, mention)) or SyncResult(opened=[a.key for a in p.raise_])
        ),
    )
    note = factory.send_alerts(cfg, plan)
    assert note == "alert e-mail sent (art-failed-simple-story-mode-c01-x)"
    assert sent[0][0] == "lumo-spec/instagram-autopilot" and sent[0][2] == "@lumo-spec"
