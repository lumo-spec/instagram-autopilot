"""The real Instagram client, exercised against a mocked HTTP layer (no network)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from autopilot.errors import PublishError, TransientPublishError
from autopilot.manifest import validate_manifest
from autopilot.publish.instagram import GraphClient

TOKEN = "IGAAtesttoken" + "x" * 30
USER = "17841400000000000"
BASE = "https://raw.githubusercontent.com/lumo-spec/instagram-autopilot/media/simplestorymode/"


class Recorder:
    """Stands in for urllib.request.urlopen."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[urllib.request.Request] = []

    def __call__(self, req: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(req)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return io.BytesIO(json.dumps(item).encode())


def http_error(code: int, body: dict[str, Any]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://graph.instagram.com/x",
        code,
        "err",
        {},
        io.BytesIO(json.dumps(body).encode()),  # type: ignore[arg-type]
    )


@pytest.fixture
def client() -> GraphClient:
    return GraphClient("graph.instagram.com", "v26.0", USER, TOKEN)


def install(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> Recorder:
    rec = Recorder(responses)
    monkeypatch.setattr(urllib.request, "urlopen", rec)
    return rec


def manifest(fmt: str, ai: bool) -> Any:
    ext = "jpg" if fmt == "image" else "mp4"
    return validate_manifest(
        {
            "page": "simplestorymode",
            "slot_key": "simplestorymode-2026-10-20-evening",
            "content_id": "r01-rest-is-allowed",
            "format": fmt,
            "audio_mode": "voice" if fmt == "reel" else "",
            "fallback": "",
            "template": "art",
            "caption": "Hello.\n\n#calm",
            "hashtags": ["#calm"],
            "media_urls": [f"{BASE}simplestorymode-2026-10-20-evening.{ext}"],
            "ai_generated": ai,
        },
        "lumo-spec/instagram-autopilot",
        2000,
        5,
    )


def test_token_only_in_authorization_header(
    monkeypatch: pytest.MonkeyPatch, client: GraphClient
) -> None:
    rec = install(monkeypatch, [{"data": [{"quota_usage": 3}]}])
    assert client.publishing_usage() == 3
    req = rec.requests[0]
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in req.full_url
    assert req.full_url.startswith(
        f"https://graph.instagram.com/v26.0/{USER}/content_publishing_limit?"
    )


def test_reel_container_parameters(monkeypatch: pytest.MonkeyPatch, client: GraphClient) -> None:
    rec = install(monkeypatch, [{"id": "c1"}])
    assert client.create_container(manifest("reel", True)) == "c1"
    req = rec.requests[0]
    body = json.loads(req.data or b"{}")  # type: ignore[arg-type]
    assert req.get_method() == "POST" and req.full_url.endswith(f"/{USER}/media")
    assert body == {
        "caption": "Hello.\n\n#calm",
        "media_type": "REELS",
        "video_url": f"{BASE}simplestorymode-2026-10-20-evening.mp4",
        "share_to_feed": True,
        "is_ai_generated": True,
    }


def test_image_container_parameters(monkeypatch: pytest.MonkeyPatch, client: GraphClient) -> None:
    rec = install(monkeypatch, [{"id": "c2"}])
    client.create_container(manifest("image", False))
    body = json.loads(rec.requests[0].data or b"{}")  # type: ignore[arg-type]
    assert body == {
        "caption": "Hello.\n\n#calm",
        "image_url": f"{BASE}simplestorymode-2026-10-20-evening.jpg",
    }


def test_carousel_parameters(monkeypatch: pytest.MonkeyPatch, client: GraphClient) -> None:
    key = "simplestorymode-2026-10-20-carousel"
    m = validate_manifest(
        {
            "page": "simplestorymode",
            "slot_key": key,
            "content_id": "c01-you-can-miss",
            "format": "carousel",
            "audio_mode": "",
            "fallback": "",
            "template": "art",
            "caption": "Hello.\n\n#calm",
            "hashtags": ["#calm"],
            "media_urls": [f"{BASE}{key}-01.jpg", f"{BASE}{key}-02.jpg"],
            "ai_generated": True,
        },
        "lumo-spec/instagram-autopilot",
        2000,
        5,
    )
    rec = install(monkeypatch, [{"id": "k1"}, {"id": "p1"}])
    assert client.create_carousel_item(m.media_urls[0]) == "k1"
    assert client.create_carousel(["k1", "k2"], m) == "p1"
    child = json.loads(rec.requests[0].data or b"{}")  # type: ignore[arg-type]
    parent = json.loads(rec.requests[1].data or b"{}")  # type: ignore[arg-type]
    # Meta: only the carousel container may carry is_ai_generated; children must not.
    assert child == {"image_url": f"{BASE}{key}-01.jpg", "is_carousel_item": True}
    assert parent == {
        "media_type": "CAROUSEL",
        "children": "k1,k2",
        "caption": "Hello.\n\n#calm",
        "is_ai_generated": True,
    }
    with pytest.raises(PublishError):
        client.create_container(m)


def test_status_publish_permalink_recent(
    monkeypatch: pytest.MonkeyPatch, client: GraphClient
) -> None:
    rec = install(
        monkeypatch,
        [
            {"status_code": "FINISHED"},
            {"id": "m1"},
            {"permalink": "https://www.instagram.com/p/abc/"},
            {"data": [{"id": "m1", "caption": "Hello."}]},
        ],
    )
    assert client.container_status("c1") == "FINISHED"
    assert client.publish("c1") == "m1"
    assert client.permalink("m1").endswith("/abc/")
    assert client.recent_media(5) == [{"id": "m1", "caption": "Hello."}]
    assert json.loads(rec.requests[1].data or b"{}") == {"creation_id": "c1"}  # type: ignore[arg-type]
    assert "fields=status_code" in rec.requests[0].full_url


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (500, TransientPublishError),
        (503, TransientPublishError),
        (429, TransientPublishError),
        (400, PublishError),
    ],
)
def test_http_errors_mapped_without_echoing_body(
    monkeypatch: pytest.MonkeyPatch, client: GraphClient, code: int, kind: type[Exception]
) -> None:
    secret_echo = {"error": {"message": f"bad token {TOKEN} user {USER}", "code": 190}}
    install(monkeypatch, [http_error(code, secret_echo)])
    with pytest.raises(kind) as info:
        client.container_status("c1")
    text = str(info.value)
    assert TOKEN not in text and USER not in text and "bad token" not in text
    assert f"HTTP {code}" in text and "190" in text


def test_unparseable_error_body(monkeypatch: pytest.MonkeyPatch, client: GraphClient) -> None:
    err = urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b"<html>"))  # type: ignore[arg-type]
    install(monkeypatch, [err])
    with pytest.raises(PublishError, match="unknown"):
        client.publish("c1")


def test_network_error_is_transient(monkeypatch: pytest.MonkeyPatch, client: GraphClient) -> None:
    install(monkeypatch, [TimeoutError()])
    with pytest.raises(TransientPublishError):
        client.permalink("m1")
