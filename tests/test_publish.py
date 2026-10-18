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
GOOD: dict[str, Any] = {
    "page": "wisdom",
    "slot_key": "wisdom-2026-10-20-evening",
    "content_id": "w-0a1b2c3d",
    "format": "reel",
    "audio_mode": "voice+music",
    "fallback": "",
    "template": "plum",
    "caption": "A thought.\n\n#calm #wisdom",
    "hashtags": ["#calm", "#wisdom"],
    "media_url": "https://raw.githubusercontent.com/lumo-spec/instagram-autopilot/media/wisdom/wisdom-2026-10-20-evening.mp4",
    "ai_generated": True,
}


def manifest(**changes: Any) -> Manifest:
    return validate_manifest({**GOOD, **changes}, REPO, 2000, 5)


# ----- manifest re-validation ------------------------------------------------------------------
def test_manifest_round_trip() -> None:
    m = manifest()
    assert validate_manifest(json.loads(m.to_json()), REPO, 2000, 5) == m


@pytest.mark.parametrize(
    "changes",
    [
        {"media_url": "https://evil.example/wisdom/wisdom-2026-10-20-evening.mp4"},
        {
            "media_url": "https://raw.githubusercontent.com/someone/else/media/wisdom/wisdom-2026-10-20-evening.mp4"
        },
        {
            "media_url": "https://raw.githubusercontent.com/lumo-spec/instagram-autopilot/media/science/wisdom-2026-10-20-evening.mp4"
        },
        {
            "media_url": "http://raw.githubusercontent.com/lumo-spec/instagram-autopilot/media/wisdom/wisdom-2026-10-20-evening.mp4"
        },
        {"media_url": GOOD["media_url"] + "?x=1"},
        {"media_url": "rehearsal://wisdom/wisdom-2026-10-20-evening.mp4"},
        {"caption": "x" * 2001},
        {"caption": ""},
        {"hashtags": ["#a1", "#b2", "#c3", "#d4", "#e5", "#f6"]},
        {"caption": "#a1 #b2 #c3 #d4 #e5 #f6"},
        {"format": "story"},
        {"page": "science"},
        {"slot_key": "wisdom; rm -rf"},
        {"content_id": "../../etc"},
        {"ai_generated": "yes"},
    ],
)
def test_manifest_rejects(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        manifest(**changes)


def test_manifest_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        validate_manifest({**GOOD, "token": "x"}, REPO, 2000, 5)


def test_rehearsal_url_only_when_allowed() -> None:
    data = {**GOOD, "media_url": "rehearsal://wisdom/wisdom-2026-10-20-evening.mp4"}
    assert validate_manifest(data, REPO, 2000, 5, allow_rehearsal=True).media_url.startswith(
        "rehearsal://"
    )


# ----- publishing flow -------------------------------------------------------------------------
def run(fake: FakeInstagram, m: Manifest, prev: str = "") -> tuple[Any, list[str]]:
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
    assert intents == ["c0002"] and fake.published_slots() == [GOOD["slot_key"]]


def test_transient_publish_error_retried() -> None:
    fake = FakeInstagram(fail_next={"publish_transient"})
    out, _ = run(fake, manifest())
    assert out.media_id and fake.published_slots() == [GOOD["slot_key"]]


def test_crash_after_publish_never_double_posts() -> None:
    fake = FakeInstagram(fail_next={"crash_after_publish"})
    with pytest.raises(SimulatedCrashError):
        run(fake, manifest())
    out, intents = run(fake, manifest(), prev="c0001")
    assert out.reconciled and intents == []
    assert fake.published_slots() == [GOOD["slot_key"]]


def test_intent_without_container_checks_recent_media() -> None:
    fake = FakeInstagram()
    run(fake, manifest())
    out, intents = run(fake, manifest(), prev="")
    assert out.reconciled and intents == [] and len(fake.published_slots()) == 1


def test_container_timeout() -> None:
    fake = FakeInstagram()
    m = manifest()
    cid = fake.create_container(m)
    fake.containers[cid]["status"] = "IN_PROGRESS"
    with pytest.raises(PublishError):
        publish_manifest(fake, m, cid, lambda _c: None, [0, 0], 3, sleep=lambda _s: None)


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
    u1 = hosting.host("wisdom", "wisdom-2026-10-20-morning", src)
    u2 = hosting.host("science", "science-2026-10-20-morning", src)
    assert u1 != u2
    assert (tmp_path / "hosted/wisdom/wisdom-2026-10-20-morning.jpg").exists()
    assert (tmp_path / "hosted/science/science-2026-10-20-morning.jpg").exists()
    hosting.remove("wisdom", "wisdom-2026-10-20-morning", ".jpg")
    assert (tmp_path / "hosted/science/science-2026-10-20-morning.jpg").exists()


# ----- state ---------------------------------------------------------------------------------
def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = PageState("wisdom")
    state.upsert(
        PostRecord(
            "wisdom-2026-10-20-morning", "2026-10-20", "morning", STATUS_INTENT, container_id="c1"
        )
    )
    state.upsert(
        PostRecord("wisdom-2026-10-20-morning", "2026-10-20", "morning", "published", media_id="m1")
    )
    save_state(path, state)
    loaded = load_state(path, "wisdom")
    assert len(loaded.posts) == 1 and loaded.is_done("wisdom-2026-10-20-morning")
    assert load_state(tmp_path / "missing.json", "wisdom").posts == []
