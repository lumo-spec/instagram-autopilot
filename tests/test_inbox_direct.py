"""@wellitstarted reels uploaded straight from the PC (Facebook Login, owner's choice A).

Meta's resumable upload (checked 2026-10-27): create the container with
``upload_type=resumable``, send the file to the returned ``rupload.facebook.com`` address, then
the usual status check and ``media_publish``. Everything here runs against fake transports.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from autopilot.errors import PublishError
from autopilot.inbox.destinations import FacebookReelClient, InstagramReels, YouTubeVideos
from autopilot.inbox.fakes import FakeDirectInstagram, FakeYouTube, sidecar_probe
from autopilot.inbox.runner import InboxRunner
from autopilot.inbox.setup import build_publishers, facebook_connection, renew_instagram_token
from autopilot.manifest import Manifest
from autopilot.publish.instagram import publish_manifest
from autopilot.rehearsal.fakes import FakeInstagram
from test_inbox import Clock, _cfg, _folder

SLOT_TIME = datetime(2026, 10, 28, 7, 31, tzinfo=UTC)


@pytest.fixture
def page(tmp_path: Path) -> Path:
    (tmp_path / "postthem").mkdir()
    return tmp_path


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = json.dumps(data).encode()

    def read(self) -> bytes:
        return self.data

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _direct(ig: Any) -> InstagramReels:
    return InstagramReels("wellitstarted", ig, None, True, [0.0], 3, sleep=lambda _s: None)


def _post_now(page: Path, ig: Any) -> Any:
    runner = InboxRunner(
        _cfg(), page, {"instagram-reels": _direct(ig)}, sidecar_probe, Clock(SLOT_TIME)
    )
    return runner.post_now("instagram-reels")


def test_direct_upload_posts_local_file_with_cover_note(page: Path) -> None:
    _folder(page / "postthem", "31-direct")
    ig = FakeDirectInstagram()
    report = _post_now(page, ig)
    assert report.posted == ["instagram-reels:31-direct"] and len(ig.media) == 1
    assert ig.uploaded == [len(b"1080x1920@40\n31-direct\n")]
    assert ig.calls[0]["url"].startswith("file:")
    note = (page / "posted" / "31-direct" / "Instagram Reels.txt").read_text(encoding="utf-8")
    assert "Instagram chose a frame" in note


def test_direct_upload_refuses_a_client_without_local_files(page: Path) -> None:
    _folder(page / "postthem", "32-x")
    report = _post_now(page, FakeInstagram())
    assert report.failed and not report.posted


def _transport(
    monkeypatch: pytest.MonkeyPatch, upload_fail: int = 0, rupload: str = "rupload.facebook.com"
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    fails = [upload_fail]

    def fake(req: Any, timeout: float) -> Any:
        body = req.data.read() if hasattr(req.data, "read") else req.data
        url = req.full_url
        seen.append(
            {
                "url": url,
                "headers": {k.lower(): v for k, v in req.header_items()},
                "body": body,
            }
        )
        if url.startswith(f"https://{rupload}/"):
            if fails[0]:
                fails[0] -= 1
                raise urllib.error.URLError("connection reset")
            return _Resp({"success": True, "message": "Upload successful."})
        if "upload_type=resumable" in url:
            return _Resp({"id": "17900", "uri": f"https://{rupload}/ig-api-upload/v26.0/17900"})
        if "fields=video_status" in url:
            return _Resp({"video_status": {"uploading_phase": {"bytes_transferred": 4}}})
        if "fields=status_code" in url:
            return _Resp({"status_code": "FINISHED"})
        if url.endswith("/media_publish"):
            return _Resp({"id": "18000"})
        if "fields=permalink" in url:
            return _Resp({"permalink": "https://www.instagram.com/reel/abc/"})
        raise AssertionError(url)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return seen


def _manifest() -> Manifest:
    return Manifest("wellitstarted", "k", "x", "reel", "", "", "", "cap", (), ("file:///v",), True)


def _client(tmp_path: Path, data: bytes, token: str = "T") -> FacebookReelClient:  # noqa: S107
    video = tmp_path / "v.mp4"
    video.write_bytes(data)
    c = FacebookReelClient("v26.0", "u1", token, sleep=lambda _s: None)
    c.videos = {"k": video}
    return c


def test_resumable_upload_request_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = _transport(monkeypatch)
    c = _client(tmp_path, b"0123456789", token="PAGETOKEN")
    out = publish_manifest(c, _manifest(), None, lambda _c: None, [0.0], 2, lambda _s: None)
    assert out.media_id == "18000" and out.permalink.endswith("/reel/abc/")
    create, upload = seen[0], seen[1]
    assert create["url"] == "https://graph.facebook.com/v26.0/u1/media?upload_type=resumable"
    assert json.loads(create["body"]) == {
        "media_type": "REELS",
        "caption": "cap",
        "is_ai_generated": True,
    }
    assert upload["url"] == "https://rupload.facebook.com/ig-api-upload/v26.0/17900"
    assert upload["body"] == b"0123456789"
    assert upload["headers"]["offset"] == "0" and upload["headers"]["file_size"] == "10"
    assert seen[-2]["url"].endswith("/u1/media_publish")
    for item in seen:  # the token travels only in the header, only to Meta's two hosts
        assert item["headers"]["authorization"] == "OAuth PAGETOKEN"
        assert "PAGETOKEN" not in item["url"]
        assert item["url"].startswith(
            ("https://graph.facebook.com/", "https://rupload.facebook.com/")
        )
    assert "PAGETOKEN" not in repr(c)


def test_upload_resumes_from_the_bytes_meta_has(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _transport(monkeypatch, upload_fail=1)
    c = _client(tmp_path, b"0123456789")
    assert c.create_container(_manifest()) == "17900"
    uploads = [s for s in seen if s["url"].startswith("https://rupload.")]
    assert [u["headers"]["offset"] for u in uploads] == ["0", "4"]
    assert uploads[1]["body"] == b"456789" and uploads[1]["headers"]["content-length"] == "6"


def test_upload_goes_only_to_metas_upload_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _transport(monkeypatch, rupload="elsewhere.example.test")
    c = _client(tmp_path, b"01")
    with pytest.raises(PublishError, match="Meta upload address"):
        c.create_container(_manifest())
    assert len(seen) == 1  # nothing was sent to the other address


def test_facebook_connection_finds_the_page_and_keeps_the_secret_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []
    pages = [
        {
            "name": "Other",
            "access_token": "P0",
            "instagram_business_account": {"id": "9", "username": "x"},
        },
        {
            "name": "Well It Started",
            "access_token": "P1",
            "instagram_business_account": {"id": "1784", "username": "WellItStarted"},
        },
    ]

    def fake(url: str, timeout: float) -> Any:
        urls.append(url)
        if "oauth/access_token" in url:
            assert "client_secret=SECRET" in url and "fb_exchange_token=SHORT" in url
            return _Resp({"access_token": "LONG", "expires_in": 5183944})
        assert "access_token=LONG" in url and "/me/accounts" in url
        return _Resp({"data": pages})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    conn = facebook_connection("APP", "SECRET", "SHORT", "wellitstarted")
    assert (conn.page_name, conn.username, conn.page_token, conn.ig_user_id) == (
        "Well It Started",
        "WellItStarted",
        "P1",
        "1784",
    )
    assert "P1" not in repr(conn) and "1784" not in repr(conn)
    assert all(u.startswith("https://graph.facebook.com/v26.0/") for u in urls)
    with pytest.raises(PublishError) as exc:
        facebook_connection("APP", "SECRET", "SHORT", "nobody")
    assert "Other -> @x" in str(exc.value) and "SECRET" not in str(exc.value)


def test_build_publishers_uses_direct_upload_and_skips_ig_renewal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "WELLITSTARTED_FB_PAGE_TOKEN=P\nWELLITSTARTED_FB_IG_USER_ID=1\n"
        "WELLITSTARTED_IG_TOKEN=old\nWELLITSTARTED_IG_TOKEN_SET=2026-01-01\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    reels = build_publishers(_cfg())["instagram-reels"]
    assert isinstance(reels, InstagramReels) and reels.host is None
    assert isinstance(reels.client, FacebookReelClient)
    assert renew_instagram_token(_cfg(), date(2027, 1, 1)) is False


def test_youtube_private_lock_is_reported_not_hidden(page: Path) -> None:
    _folder(page / "postthem", "33-yt")
    yt = FakeYouTube(privacy_status="private")
    runner = InboxRunner(
        _cfg(youtube_shorts=True),
        page,
        {"youtube-shorts": YouTubeVideos(yt, True, True)},
        sidecar_probe,
        Clock(SLOT_TIME),
    )
    report = runner.post_now("youtube-shorts")
    assert report.posted == ["youtube-shorts:33-yt"] and len(yt.uploads) == 1  # not re-uploaded
    text = (page / "posted" / "33-yt" / "YouTube Shorts.txt").read_text(encoding="utf-8")
    assert "WARNING: YouTube shows this video as PRIVATE" in text
    assert any("check a video on YouTube Shorts" in a.title for a in report.alerts.raise_)
