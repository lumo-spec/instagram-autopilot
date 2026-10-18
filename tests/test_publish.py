from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from autopilot.errors import PublishError, ValidationError
from autopilot.manifest import Manifest, validate_manifest
from autopilot.publish.instagram import GraphClient, publish_manifest
from autopilot.rehearsal.fakes import (
    FakeInstagram,
    LocalHosting,
    SimulatedCrashError,
    no_instagram_network,
)
from autopilot.state import STATUS_INTENT, PageState, PostRecord, load_state, save_state

REPO = "lumo-spec/instagram-autopilot"
MEDIA = "https://raw.githubusercontent.com/lumo-spec/instagram-autopilot/media/simplestorymode/"
REEL_KEY = "simplestorymode-2026-10-20-evening"
KEY = "simplestorymode-2026-10-20-carousel"
GOOD: dict[str, Any] = {
    "page": "simplestorymode",
    "slot_key": REEL_KEY,
    "content_id": "r01-rest-is-allowed",
    "format": "reel",
    "audio_mode": "voice+music",
    "fallback": "",
    "template": "art",
    "caption": "A thought.\n\n#calm #rest",
    "hashtags": ["#calm", "#rest"],
    "media_urls": [f"{MEDIA}{REEL_KEY}.mp4"],
    "ai_generated": True,
}
CAROUSEL: dict[str, Any] = {
    **GOOD,
    "slot_key": KEY,
    "content_id": "c01-you-can-miss",
    "format": "carousel",
    "audio_mode": "",
    "media_urls": [f"{MEDIA}{KEY}-{i:02d}.jpg" for i in range(1, 7)],
}
REEL_URL = GOOD["media_urls"][0]


def manifest(**changes: Any) -> Manifest:
    return validate_manifest({**GOOD, **changes}, REPO, 2000, 5)


def carousel(**changes: Any) -> Manifest:
    return validate_manifest({**CAROUSEL, **changes}, REPO, 2000, 5)


# ----- manifest re-validation ------------------------------------------------------------------
def test_manifest_round_trip() -> None:
    for m in (manifest(), carousel()):
        assert validate_manifest(json.loads(m.to_json()), REPO, 2000, 5) == m


@pytest.mark.parametrize(
    "changes",
    [
        {"media_urls": [f"https://evil.example/simplestorymode/{REEL_KEY}.mp4"]},
        {"media_urls": [REEL_URL.replace("lumo-spec/instagram-autopilot", "someone/else")]},
        {"media_urls": [REEL_URL.replace("/media/simplestorymode/", "/media/other/")]},
        {"media_urls": [REEL_URL.replace("https://", "http://")]},
        {"media_urls": [REEL_URL + "?x=1"]},
        {"media_urls": [f"rehearsal://simplestorymode/{REEL_KEY}.mp4"]},
        {"media_urls": [REEL_URL, REEL_URL]},
        {"media_urls": []},
        {"media_urls": REEL_URL},
        {"caption": "x" * 2001},
        {"caption": ""},
        {"hashtags": ["#a1", "#b2", "#c3", "#d4", "#e5", "#f6"]},
        {"caption": "#a1 #b2 #c3 #d4 #e5 #f6"},
        {"format": "story"},
        {"page": "other"},
        {"slot_key": "simplestorymode; rm -rf"},
        {"content_id": "../../etc"},
        {"content_id": "x" * 49},
        {"ai_generated": "yes"},
    ],
)
def test_manifest_rejects(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        manifest(**changes)


def test_manifest_rejects_extra_or_old_fields() -> None:
    with pytest.raises(ValidationError):
        validate_manifest({**GOOD, "token": "x"}, REPO, 2000, 5)
    old = {k: v for k, v in GOOD.items() if k != "media_urls"} | {"media_url": REEL_URL}
    with pytest.raises(ValidationError):
        validate_manifest(old, REPO, 2000, 5)


@pytest.mark.parametrize(
    "urls",
    [
        [f"{MEDIA}{KEY}-01.jpg"],  # one slide is not a carousel
        [f"{MEDIA}{KEY}-{i:02d}.jpg" for i in range(1, 12)],  # 11 > the API limit of 10
        [f"{MEDIA}{KEY}-02.jpg", f"{MEDIA}{KEY}-01.jpg"],  # out of order
        [f"{MEDIA}{KEY}-01.jpg", f"{MEDIA}{KEY}-02.png"],  # not JPEG
        [f"{MEDIA}{KEY}-01.jpg", f"{MEDIA}{KEY}-01.jpg"],  # duplicate slide
    ],
)
def test_carousel_manifest_rejects(urls: list[str]) -> None:
    with pytest.raises(ValidationError):
        carousel(media_urls=urls)


def test_carousel_manifest_accepts_ten() -> None:
    m = carousel(media_urls=[f"{MEDIA}{KEY}-{i:02d}.jpg" for i in range(1, 11)])
    assert len(m.media_urls) == 10


def test_rehearsal_url_only_when_allowed() -> None:
    data = {**GOOD, "media_urls": [f"rehearsal://simplestorymode/{REEL_KEY}.mp4"]}
    m = validate_manifest(data, REPO, 2000, 5, allow_rehearsal=True)
    assert m.media_urls[0].startswith("rehearsal://")


# ----- publishing flow -------------------------------------------------------------------------
def run(fake: FakeInstagram, m: Manifest, prev: str | None = None) -> tuple[Any, list[str]]:
    intents: list[str] = []
    out = publish_manifest(fake, m, prev, intents.append, [0, 0, 0], 3, sleep=lambda _s: None)
    return out, intents


def test_happy_path() -> None:
    fake = FakeInstagram()
    out, intents = run(fake, manifest())
    assert out.media_id == "m0001" and out.permalink.endswith("m0001/")
    assert intents == ["c0001"]
    create = next(c for c in fake.calls if c["op"] == "create")
    assert create["ai_generated"] is True


def test_container_error_fails_without_posting() -> None:
    fake = FakeInstagram(fail_next={"container_error"})
    with pytest.raises(PublishError):
        run(fake, manifest())
    assert fake.published_slots() == []


def test_failed_container_retried_with_new_one() -> None:
    fake = FakeInstagram(fail_next={"container_error"})
    with pytest.raises(PublishError):
        run(fake, manifest())
    _, intents = run(fake, manifest(), prev="c0001")
    assert intents == ["c0002"] and fake.published_slots() == [REEL_KEY]


def test_transient_publish_error_retried() -> None:
    fake = FakeInstagram(fail_next={"publish_transient"})
    out, _ = run(fake, manifest())
    assert out.media_id and fake.published_slots() == [REEL_KEY]


def test_crash_after_publish_never_double_posts() -> None:
    fake = FakeInstagram(fail_next={"crash_after_publish"})
    with pytest.raises(SimulatedCrashError):
        run(fake, manifest())
    out, intents = run(fake, manifest(), prev="c0001")
    assert out.reconciled and intents == []
    assert fake.published_slots() == [REEL_KEY]


def test_intent_without_container_checks_recent_media() -> None:
    fake = FakeInstagram()
    run(fake, manifest())
    out, intents = run(fake, manifest(), prev="")
    assert out.reconciled and intents == [] and len(fake.published_slots()) == 1


def test_new_post_with_same_caption_as_old_one_is_still_posted() -> None:
    fake = FakeInstagram()
    run(fake, manifest())
    other = manifest(
        slot_key="simplestorymode-2026-10-21-evening",
        media_urls=[f"{MEDIA}simplestorymode-2026-10-21-evening.mp4"],
    )
    out, intents = run(fake, other)  # first attempt of a new slot: no caption matching
    assert not out.reconciled and len(intents) == 1 and len(fake.published_slots()) == 2


def test_container_timeout() -> None:
    fake = FakeInstagram()
    m = manifest()
    cid = fake.create_container(m)
    fake.containers[cid]["status"] = "IN_PROGRESS"
    with pytest.raises(PublishError):
        publish_manifest(fake, m, cid, lambda _c: None, [0, 0], 3, sleep=lambda _s: None)


# ----- carousels -------------------------------------------------------------------------------
def test_carousel_happy_path() -> None:
    fake = FakeInstagram()
    out, intents = run(fake, carousel())
    items = [c for c in fake.calls if c["op"] == "create-item"]
    parent = next(c for c in fake.calls if c["op"] == "create")
    assert [c["url"] for c in items] == CAROUSEL["media_urls"]  # slide order kept
    assert parent["format"] == "carousel" and parent["children"] == 6
    assert parent["ai_generated"] is True and "ai_generated" not in items[0]
    assert intents == ["c0007"] and out.media_id == "m0001"
    assert fake.published_slots() == [KEY]


def test_carousel_crash_after_publish_never_double_posts() -> None:
    fake = FakeInstagram(fail_next={"crash_after_publish"})
    with pytest.raises(SimulatedCrashError):
        run(fake, carousel())
    out, intents = run(fake, carousel(), prev="c0007")
    assert out.reconciled and intents == [] and fake.published_slots() == [KEY]


def test_carousel_container_error_retried_with_fresh_children() -> None:
    fake = FakeInstagram(fail_next={"container_error"})
    with pytest.raises(PublishError):
        run(fake, carousel())
    _, intents = run(fake, carousel(), prev="c0007")
    assert intents == ["c0014"]  # 6 new children + a new parent; old children never reused
    assert fake.published_slots() == [KEY]


def test_carousel_crash_before_parent_leaves_nothing_published() -> None:
    fake = FakeInstagram()
    real = fake.create_carousel

    def crash(children: list[str], m: Manifest) -> str:
        raise SimulatedCrashError

    fake.create_carousel = crash  # type: ignore[method-assign]
    with pytest.raises(SimulatedCrashError):
        run(fake, carousel())
    assert fake.published_slots() == []
    fake.create_carousel = real  # type: ignore[method-assign]
    out, intents = run(fake, carousel(), prev="")  # the intent had no container yet
    assert not out.reconciled and len(intents) == 1 and fake.published_slots() == [KEY]


def test_carousel_child_error_stops_before_parent() -> None:
    fake = FakeInstagram()
    real = fake.create_carousel_item

    def failing(url: str) -> str:
        cid = real(url)
        if url.endswith("-03.jpg"):
            fake.containers[cid]["status"] = "ERROR"
        return cid

    fake.create_carousel_item = failing  # type: ignore[method-assign]
    with pytest.raises(PublishError):
        run(fake, carousel())
    assert not any(c["op"] == "create" for c in fake.calls) and fake.published_slots() == []


def test_carousel_items_cannot_be_published_alone() -> None:
    fake = FakeInstagram()
    child = fake.create_carousel_item(CAROUSEL["media_urls"][0])
    with pytest.raises(AssertionError):
        fake.publish(child)


# ----- real client never leaks -----------------------------------------------------------------
def test_graph_client_repr_and_errors_hide_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GraphClient("graph.instagram.com", "v26.0", "1789000000000000", "IGSECRETTOKEN" * 3)
    assert "IGSECRET" not in repr(client) and "1789" not in repr(client)

    def fail(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.URLError("boom")

    import urllib.error

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(PublishError) as info:
        client.publishing_usage()
    assert "IGSECRET" not in str(info.value) and "1789" not in str(info.value)


# ----- rehearsal guard and hosting -------------------------------------------------------------
def test_no_instagram_network_guard() -> None:
    with no_instagram_network(), pytest.raises(AssertionError):
        socket.getaddrinfo("graph.instagram.com", 443)
    with no_instagram_network(), pytest.raises(AssertionError):
        socket.getaddrinfo("api.facebook.com", 443)


def test_local_hosting_isolates_pages(tmp_path: Path) -> None:
    src = tmp_path / "a.jpg"
    src.write_bytes(b"x")
    hosting = LocalHosting(tmp_path / "hosted")
    u1 = hosting.host("pagea", "pagea-2026-10-20-morning.jpg", src)
    u2 = hosting.host("pageb", "pageb-2026-10-20-morning.jpg", src)
    assert u1 != u2
    assert (tmp_path / "hosted/pagea/pagea-2026-10-20-morning.jpg").exists()
    assert (tmp_path / "hosted/pageb/pageb-2026-10-20-morning.jpg").exists()
    hosting.remove("pagea", "pagea-2026-10-20-morning.jpg")
    assert (tmp_path / "hosted/pageb/pageb-2026-10-20-morning.jpg").exists()


# ----- state ---------------------------------------------------------------------------------
def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    key = "simplestorymode-2026-10-20-carousel"
    state = PageState("simplestorymode")
    state.upsert(PostRecord(key, "2026-10-20", "carousel", STATUS_INTENT, container_id="c1"))
    state.upsert(PostRecord(key, "2026-10-20", "carousel", "published", media_id="m1", slides=6))
    save_state(path, state)
    loaded = load_state(path, "simplestorymode")
    assert len(loaded.posts) == 1 and loaded.is_done(key) and loaded.posts[0].slides == 6
    assert load_state(tmp_path / "missing.json", "simplestorymode").posts == []
