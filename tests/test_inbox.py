"""Video inbox (@wellitstarted): folders, schedule, posting once, posted/failed, pause, alerts."""

from __future__ import annotations

import io
import json
import os
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from autopilot.__main__ import main
from autopilot.errors import PublishError, TransientPublishError
from autopilot.inbox import schedule
from autopilot.inbox.config import InboxConfig, load_inbox_config, resolve_root
from autopilot.inbox.destinations import (
    InstagramReels,
    Posted,
    ReelClient,
    YouTubeVideos,
    title_and_description,
)
from autopilot.inbox.fakes import FakeHost, FakeYouTube, sidecar_probe
from autopilot.inbox.folders import VideoInfo, kind_of, scan
from autopilot.inbox.rehearsal import run_guarded
from autopilot.inbox.runner import InboxRunner
from autopilot.inbox.setup import set_values
from autopilot.inbox.youtube import YouTubeApi
from autopilot.manifest import Manifest
from autopilot.rehearsal.fakes import FakeInstagram, SimulatedCrashError

START = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)  # Wednesday, GMT (after the clock change)
CAPTION = "Title {n}\n\nWhat happened next.\n\n#WellItStarted #BusinessStories #CompanyHistory"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (54, 96), (10, 20, 30)).save(out, "PNG")
    return out.getvalue()


def _cfg(**dest_on: bool) -> InboxConfig:
    cfg = load_inbox_config("wellitstarted")
    dests = tuple(
        replace(d, enabled=dest_on.get(d.name.replace("-", "_"), d.enabled))
        for d in cfg.destinations
    )
    return replace(cfg, destinations=dests)


def _folder(
    inbox: Path,
    name: str,
    spec: str = "1080x1920@40",
    caption: str | None = None,
    when: datetime = START,
    video: bytes | None = None,
) -> Path:
    f = inbox / name
    f.mkdir(parents=True)
    (f / f"{name}.mp4").write_bytes(video or f"{spec}\n{name}\n".encode())
    (f / "cover.png").write_bytes(_png())
    (f / "publication-copy.txt").write_text(caption or CAPTION.format(n=name), encoding="utf-8")
    (f / "notes.md").write_text("ignored", encoding="utf-8")
    stamp = (when - timedelta(hours=1)).timestamp()
    for p in [*f.iterdir(), f]:
        os.utime(p, (stamp, stamp))
    return f


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def page(tmp_path: Path) -> Path:
    (tmp_path / "postthem").mkdir()
    return tmp_path


def _runner(
    page: Path,
    clock: Clock,
    cfg: InboxConfig | None = None,
    ig: FakeInstagram | None = None,
    yt: FakeYouTube | None = None,
    host: FakeHost | None = None,
) -> InboxRunner:
    cfg = cfg or _cfg()
    pubs: dict[str, Any] = {
        "instagram-reels": InstagramReels(
            "wellitstarted",
            ig or FakeInstagram(),
            host or FakeHost(),
            True,
            [0.0],
            3,
            sleep=lambda _s: None,
        ),
        "youtube-shorts": YouTubeVideos(yt or FakeYouTube(), True, True),
        "youtube-long": YouTubeVideos(yt or FakeYouTube(), False, True),
    }
    return InboxRunner(cfg, page, pubs, sidecar_probe, clock)


# --- config and schedule ---------------------------------------------------------------------
def test_config_matches_owner_schedule() -> None:
    cfg = load_inbox_config("wellitstarted")
    ig = cfg.destination("instagram-reels")
    assert ig.times == ("07:30", "10:30", "13:00", "17:30", "20:30") and ig.enabled
    assert cfg.destination("youtube-shorts").times == ("12:00",)
    long = cfg.destination("youtube-long")
    assert long.times == ("18:00",) and long.weekdays == (1, 5)  # Tuesday, Saturday
    assert cfg.low_stock_days == 3 and cfg.ai_generated


def test_slots_follow_uk_time_across_the_clock_change() -> None:
    cfg = load_inbox_config("wellitstarted")
    ig = cfg.destination("instagram-reels")
    sat = list(
        schedule.slots_between(
            ig, cfg.tz, datetime(2026, 10, 24, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC)
        )
    )
    assert [s.local.strftime("%d %H:%M") for s in sat][:5] == [
        "24 07:30",
        "24 10:30",
        "24 13:00",
        "24 17:30",
        "24 20:30",
    ]
    assert sat[0].at_utc.hour == 6  # BST on Saturday 24 Oct
    assert sat[5].at_utc.hour == 7  # GMT from Sunday 25 Oct
    long = list(
        schedule.slots_between(
            cfg.destination("youtube-long"),
            cfg.tz,
            datetime(2026, 10, 26, tzinfo=UTC),
            datetime(2026, 11, 2, tzinfo=UTC),
        )
    )
    assert [s.local.strftime("%a %H:%M") for s in long] == ["Tue 18:00", "Sat 18:00"]


def test_due_inside_catchup_window_only() -> None:
    cfg = load_inbox_config("wellitstarted")
    ig = cfg.destination("instagram-reels")
    at = datetime(2026, 10, 28, 7, 30, tzinfo=UTC)
    assert schedule.due(ig, cfg.tz, at - timedelta(minutes=1), 90) == []
    assert [s.key for s in schedule.due(ig, cfg.tz, at, 90)] == ["instagram-reels-2026-10-28-0730"]
    assert schedule.due(ig, cfg.tz, at + timedelta(minutes=91), 90) == []


# --- folders -----------------------------------------------------------------------------------
def test_scan_ready_invalid_waiting_and_order(page: Path) -> None:
    cfg = _cfg()
    inbox = page / "postthem"
    _folder(inbox, "b-second")
    _folder(inbox, "a-long", spec="1920x1080@600")
    two = _folder(inbox, "c-two-videos")
    (two / "extra.mp4").write_bytes(b"1x1@1")
    os.utime(two / "extra.mp4", ((START - timedelta(hours=1)).timestamp(),) * 2)
    nocopy = _folder(inbox, "d-no-copy")
    (nocopy / "publication-copy.txt").unlink()
    os.utime(nocopy, ((START - timedelta(hours=1)).timestamp(),) * 2)
    notpng = _folder(inbox, "e-not-png")
    (notpng / "cover.png").write_bytes(b"JPEG...")
    os.utime(notpng / "cover.png", ((START - timedelta(hours=1)).timestamp(),) * 2)
    _folder(inbox, "f-arriving", when=START + timedelta(minutes=55))  # changed 5 min ago
    fps: dict[str, str] = {}
    seen: dict[str, float] = {}
    res = scan(cfg, inbox, sidecar_probe, START.timestamp(), fps, seen)
    assert sorted(c.name for c in res.ready) == ["a-long", "b-second"]
    assert {c.name: c.kind for c in res.ready} == {"a-long": "long", "b-second": "short"}
    reasons = {r.name: r.reason for r in res.rejected}
    assert "exactly one .mp4" in reasons["c-two-videos"]
    assert "publication-copy.txt is missing" in reasons["d-no-copy"]
    assert "not a PNG" in reasons["e-not-png"]
    assert res.waiting == ["f-arriving"]
    assert len(fps) == 2  # hashes cached for the next run


def test_kind_rule() -> None:
    assert kind_of(VideoInfo(1080, 1920, 40), 180) == "short"
    assert kind_of(VideoInfo(1080, 1080, 180), 180) == "short"
    assert kind_of(VideoInfo(1080, 1920, 181), 180) == "long"
    assert kind_of(VideoInfo(1920, 1080, 30), 180) == "long"


# --- posting -----------------------------------------------------------------------------------
def test_slot_posts_copy_files_it_and_leaves_drop_folder_alone(page: Path) -> None:
    src = _folder(page / "postthem", "11-Purdue")
    before = {p.name: p.read_bytes() for p in src.iterdir()}
    ig, host = FakeInstagram(), FakeHost()
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    report = _runner(page, clock, ig=ig, host=host).run()
    assert report.posted == ["instagram-reels:11-Purdue"]
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before  # original untouched
    done = page / "posted" / "11-Purdue"
    assert sorted(p.name for p in done.iterdir()) == [
        "11-Purdue.mp4",
        "Instagram Reels.txt",
        "cover.png",
        "publication-copy.txt",
    ]
    note = (done / "Instagram Reels.txt").read_text(encoding="utf-8")
    assert "Post ID: m0001" in note and "Link: https://www.instagram.com/p/" in note
    create = next(c for c in ig.calls if c["op"] == "create")
    assert create["format"] == "reel" and create["ai_generated"] is True
    assert host.online == {}  # hosted copies removed after publishing
    assert not any((page / "working").rglob("*.mp4"))
    # the same slot and later runs never post it again
    assert _runner(page, clock, ig=ig).run().posted == []
    clock.t = datetime(2026, 10, 28, 10, 31, tzinfo=UTC)
    assert _runner(page, clock, ig=ig).run().posted == []
    assert len(ig.media) == 1


def test_empty_drop_folder_skips_silently(page: Path) -> None:
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    report = _runner(page, clock, cfg=_cfg()).run()
    assert report.posted == [] and report.failed == [] and report.lines == []
    runner = _runner(page, clock)
    assert runner.ledger.data["slots"]["instagram-reels-2026-10-28-0730"]["outcome"] == "empty"


def test_pause_file_stops_everything_and_paused_slots_are_not_caught_up(page: Path) -> None:
    _folder(page / "postthem", "11-Purdue")
    (page / "PAUSE.txt").write_text("", encoding="utf-8")
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    ig = FakeInstagram()
    assert _runner(page, clock, ig=ig).run().paused
    (page / "PAUSE.txt").unlink()
    clock.t += timedelta(minutes=20)  # still inside the 07:30 catch-up window
    assert _runner(page, clock, ig=ig).run().posted == []
    clock.t = datetime(2026, 10, 28, 10, 31, tzinfo=UTC)
    assert _runner(page, clock, ig=ig).run().posted == ["instagram-reels:11-Purdue"]


def test_same_video_under_two_names_posts_once(page: Path) -> None:
    _folder(page / "postthem", "11-Purdue", video=b"1080x1920@40\nsame\n")
    _folder(page / "postthem", "12-Copy", video=b"1080x1920@40\nsame\n")
    ig = FakeInstagram()
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    _runner(page, clock, ig=ig).run()
    clock.t = datetime(2026, 10, 28, 10, 31, tzinfo=UTC)
    assert _runner(page, clock, ig=ig).run().posted == []
    assert len(ig.media) == 1


def test_arrival_order_not_name_order(page: Path) -> None:
    # Simulated time before the files' real creation time, so arrival = when first seen.
    early = datetime(2020, 1, 6, 7, 0, tzinfo=UTC)
    ig = FakeInstagram()
    _folder(page / "postthem", "z-first", when=early - timedelta(hours=1))
    clock = Clock(early)
    _runner(page, clock, ig=ig).run()  # before the 07:30 slot: only notes the arrival
    _folder(page / "postthem", "a-second", when=early)
    clock.t = early + timedelta(minutes=31)
    runner = _runner(page, clock, ig=ig)
    assert [c.name for c in runner.scan().ready] == ["z-first", "a-second"]
    assert runner.run().posted == ["instagram-reels:z-first"]


def test_invalid_caption_goes_to_failed_with_reason_and_alert(page: Path) -> None:
    six = "Too many\n\n#a1 #b2 #c3 #d4 #e5 #f6"
    _folder(page / "postthem", "13-Tags", caption=six)
    _folder(page / "postthem", "14-Fine")
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    report = _runner(page, clock).run()
    assert report.failed == ["instagram-reels:13-Tags"]
    reason = (page / "failed" / "13-Tags" / "Instagram Reels - reason.txt").read_text("utf-8")
    assert "6 hashtags" in reason and "delete this folder from failed" in reason
    assert any("could not go to Instagram Reels" in a.title for a in report.alerts.raise_)
    # the slot moves on to the next video
    clock.t = datetime(2026, 10, 28, 10, 31, tzinfo=UTC)
    assert _runner(page, clock).run().posted == ["instagram-reels:14-Fine"]


def test_deleting_the_failed_folder_makes_it_postable_again(page: Path) -> None:
    src = _folder(page / "postthem", "13-Tags", caption="T\n\n#a1 #b2 #c3 #d4 #e5 #f6")
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    _runner(page, clock).run()
    (src / "publication-copy.txt").write_text(CAPTION.format(n=1), encoding="utf-8")
    shutil.rmtree(page / "failed" / "13-Tags")
    clock.t = datetime(2026, 10, 28, 10, 31, tzinfo=UTC)
    assert _runner(page, clock).run().posted == ["instagram-reels:13-Tags"]


def test_incomplete_folder_reported_once(page: Path) -> None:
    bad = _folder(page / "postthem", "15-NoCover")
    (bad / "cover.png").unlink()
    os.utime(bad, ((START - timedelta(hours=1)).timestamp(),) * 2)
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    first = _runner(page, clock).run()
    assert (page / "failed" / "15-NoCover" / "reason.txt").exists()
    assert any("incomplete" in a.title for a in first.alerts.raise_)
    clock.t += timedelta(minutes=10)
    assert not any("incomplete" in a.title for a in _runner(page, clock).run().alerts.raise_)


def test_temporary_errors_retry_then_fail(page: Path) -> None:
    class Flaky(InstagramReels):
        def publish(self, *a: Any, **k: Any) -> Posted:
            raise TransientPublishError("network error talking to Instagram")

    _folder(page / "postthem", "16-Flaky")
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    cfg = _cfg()
    outcomes = []
    for hhmm in ((7, 31), (10, 31), (13, 1)):
        clock.t = datetime(2026, 10, 28, *hhmm, tzinfo=UTC)
        r = InboxRunner(
            cfg,
            page,
            {"instagram-reels": Flaky("w", FakeInstagram(), FakeHost(), True, [0.0], 1)},
            sidecar_probe,
            clock,
        )
        r.run()
        outcomes.append(r.ledger.data["slots"][next(reversed(r.ledger.data["slots"]))]["outcome"])
    assert outcomes == ["retry", "retry", "failed"]
    assert "3 tries" in (page / "failed" / "16-Flaky" / "Instagram Reels - reason.txt").read_text(
        "utf-8"
    )


def test_crash_after_publish_is_reconciled_not_reposted(page: Path) -> None:
    _folder(page / "postthem", "17-Crash")
    ig = FakeInstagram(fail_next={"crash_after_publish"})
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    with pytest.raises(SimulatedCrashError):
        _runner(page, clock, ig=ig).run()
    assert not (page / ".autopilot" / "lock").exists()
    clock.t += timedelta(minutes=10)
    report = _runner(page, clock, ig=ig).run()
    assert report.posted == ["instagram-reels:17-Crash"]
    assert len(ig.media) == 1
    note = (page / "posted" / "17-Crash" / "Instagram Reels.txt").read_text("utf-8")
    assert "already posted after an interruption" in note


def test_one_video_goes_to_reels_and_shorts_into_one_posted_folder(page: Path) -> None:
    _folder(page / "postthem", "18-Both")
    yt = FakeYouTube()
    cfg = _cfg(youtube_shorts=True)
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))
    _runner(page, clock, cfg=cfg, yt=yt).run()
    clock.t = datetime(2026, 10, 28, 12, 1, tzinfo=UTC)
    assert _runner(page, clock, cfg=cfg, yt=yt).run().posted == ["youtube-shorts:18-Both"]
    names = sorted(p.name for p in (page / "posted" / "18-Both").iterdir())
    assert "Instagram Reels.txt" in names and "YouTube Shorts.txt" in names
    assert yt.uploads[0]["synthetic"] is True and yt.uploads[0]["title"] == "Title 18-Both"
    assert "youtube.com/shorts/" in (page / "posted" / "18-Both" / "YouTube Shorts.txt").read_text(
        "utf-8"
    )


def test_long_video_only_to_long_youtube_with_thumbnail(page: Path) -> None:
    _folder(page / "postthem", "20-Doc", spec="1920x1080@900")
    yt = FakeYouTube()
    cfg = _cfg(youtube_long=True)
    clock = Clock(datetime(2026, 10, 28, 7, 31, tzinfo=UTC))  # Wednesday: no long slot
    assert _runner(page, clock, cfg=cfg, yt=yt).run().posted == []
    clock.t = datetime(2026, 10, 31, 18, 1, tzinfo=UTC)  # Saturday 18:00 GMT
    assert _runner(page, clock, cfg=cfg, yt=yt).run().posted == ["youtube-long:20-Doc"]
    assert yt.thumbnails == ["yt0001"]


def test_youtube_crash_after_upload_reconciled_by_title(page: Path) -> None:
    _folder(page / "postthem", "19-YT")
    yt = FakeYouTube(fail_next={"crash_after_upload"})
    cfg = _cfg(instagram_reels=False, youtube_shorts=True)
    clock = Clock(datetime(2026, 10, 28, 12, 1, tzinfo=UTC))
    with pytest.raises(SimulatedCrashError):
        _runner(page, clock, cfg=cfg, yt=yt).run()
    clock.t += timedelta(minutes=10)
    assert _runner(page, clock, cfg=cfg, yt=yt).run().posted == ["youtube-shorts:19-YT"]
    assert len(yt.uploads) == 1


def test_low_stock_alert_under_three_days(page: Path) -> None:
    cfg = _cfg()
    clock = Clock(datetime(2026, 10, 28, 7, 0, tzinfo=UTC))  # before the first slot
    for i in range(15):
        _folder(page / "postthem", f"{i:02d}-v")
    r = _runner(page, clock, cfg=cfg)
    report = r.run()
    assert not report.alerts.raise_  # 15 videos = exactly 3 days of 5 reels
    shutil.rmtree(page / "postthem" / "14-v")
    report = _runner(page, clock, cfg=cfg).run()
    titles = [a.title for a in report.alerts.raise_]
    assert titles == [
        "ACTION NEEDED [Well It Started]: Instagram Reels is running out of videos "
        "— add more to postthem"
    ]
    assert "14 videos left" in report.alerts.raise_[0].body


def test_post_now_posts_exactly_one_video(page: Path) -> None:
    for name in ("21-a", "22-b"):
        _folder(page / "postthem", name)
    ig = FakeInstagram()
    clock = Clock(datetime(2026, 10, 28, 9, 0, tzinfo=UTC))  # between slots
    report = _runner(page, clock, ig=ig).post_now("instagram-reels")
    assert report.posted == ["instagram-reels:21-a"] and len(ig.media) == 1


# --- clients -----------------------------------------------------------------------------------
def test_reel_client_sends_cover_and_ai_label() -> None:
    sent: list[dict[str, Any]] = []

    class Capture(ReelClient):
        def _request(
            self, method: str, path: str, params: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            sent.append(dict(params or {}))
            return {"id": "1"}

    c = Capture("graph.instagram.com", "v26.0", "u", "t")
    c.cover_urls = {"k": "https://h/c.jpg"}
    m = Manifest(
        "wellitstarted", "k", "x", "reel", "", "", "", "cap", (), ("https://h/v.mp4",), True
    )
    c.create_container(m)
    assert sent[0] == {
        "media_type": "REELS",
        "video_url": "https://h/v.mp4",
        "caption": "cap",
        "share_to_feed": True,
        "cover_url": "https://h/c.jpg",
        "is_ai_generated": True,
    }


def test_youtube_problems_and_title_split() -> None:
    assert title_and_description("Line one\n\nRest\nmore") == ("Line one", "Rest\nmore")
    yt = YouTubeVideos(FakeYouTube(), True, True)

    class V:
        text = "x" * 101 + "\nok <b>"
        info = VideoInfo(1920, 1080, 200)

    problems = " | ".join(yt.problems(V()))  # type: ignore[arg-type]
    assert "1-100 characters" in problems and "< or >" in problems and "vertical" in problems


def test_youtube_upload_request_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    seen: list[Any] = []

    class Resp:
        def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
            self.status, self.headers, self._body = status, headers, body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float) -> Resp:
        seen.append(req)
        if "oauth2" in req.full_url:
            return Resp(200, {}, b'{"access_token": "AT"}')
        if req.get_method() == "POST":
            return Resp(
                200,
                {"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=1"},
                b"",
            )
        return Resp(200, {}, b'{"id": "abc"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    api = YouTubeApi("cid", "secret", "refresh")
    assert api.upload(video, "T", "D", True) == "abc"
    start = seen[1]
    meta = json.loads(start.data)
    assert meta["status"] == {
        "privacyStatus": "public",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    assert start.get_header("Authorization") == "Bearer AT"
    assert all("secret" not in r.full_url and "AT" not in r.full_url for r in seen)
    assert repr(api) == "YouTubeApi(<redacted>)"


def test_youtube_errors_carry_only_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def boom(req: Any, timeout: float) -> Any:
        body = io.BytesIO(
            b'{"error": {"errors": [{"reason": "quotaExceeded"}], "message": "token AT leaked?"}}'
        )
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(PublishError) as exc:
        YouTubeApi("c", "s", "r").recent_uploads(5)
    assert str(exc.value) == "YouTube sign-in: HTTP 403 (quotaExceeded)"
    assert not isinstance(exc.value, TransientPublishError)


# --- setup, CLI, rehearsal ---------------------------------------------------------------------
def test_env_values_replaced_in_place(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# keep me\nIMAGE_API_KEY=abc\nWELLITSTARTED_IG_TOKEN=old\n", encoding="utf-8")
    set_values({"WELLITSTARTED_IG_TOKEN": "new", "WELLITSTARTED_IG_USER_ID": "7"}, env)
    assert env.read_text(encoding="utf-8").splitlines() == [
        "# keep me",
        "IMAGE_API_KEY=abc",
        "WELLITSTARTED_IG_TOKEN=new",
        "WELLITSTARTED_IG_USER_ID=7",
    ]


def test_root_from_env_and_status_command(
    page: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = load_inbox_config("wellitstarted")
    monkeypatch.setenv("WELLITSTARTED_DIR", str(page))
    assert resolve_root(cfg) == page
    monkeypatch.setattr("autopilot.inbox.folders.ffmpeg_probe", sidecar_probe)
    from autopilot.inbox import folders

    monkeypatch.setattr(folders, "ffmpeg_probe", sidecar_probe)
    assert main(["inbox", "status", "wellitstarted"]) == 0
    out = capsys.readouterr().out
    assert "Well It Started: running" in out and "Instagram Reels (on): 0 waiting" in out


def test_rehearsal_three_days_passes(tmp_path: Path) -> None:
    result = run_guarded(load_inbox_config("wellitstarted"), tmp_path / "r", days=3)
    assert result.passed, result.data["problems"]
    assert result.data["posted"]["instagram-reels"] >= 5
    assert result.data["crashes_recovered"] >= 1
    assert result.data["ai_label"] == {"instagram": True, "youtube": True}
