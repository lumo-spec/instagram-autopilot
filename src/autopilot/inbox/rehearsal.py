"""Inbox rehearsal: simulated days of 10-minute runs against fakes, with injected trouble.

It works in a scratch folder (never the owner's folders): sample videos are copied in, or
small fake ones are made. Nothing is uploaded anywhere (Meta hosts are blocked).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autopilot.inbox.config import InboxConfig
from autopilot.inbox.destinations import InstagramReels, Publisher, YouTubeVideos
from autopilot.inbox.fakes import FakeHost, FakeYouTube, sidecar_probe
from autopilot.inbox.folders import Probe, ffmpeg_probe
from autopilot.inbox.runner import InboxRunner
from autopilot.rehearsal.fakes import FakeInstagram, SimulatedCrashError, no_instagram_network

TICK = timedelta(minutes=10)


def _tiny_png() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (108, 192), (200, 160, 90)).save(out, "PNG")
    return out.getvalue()


def _fake_video(folder: Path, name: str, spec: str, copy: str, cfg: InboxConfig) -> None:
    folder.mkdir(parents=True)
    (folder / f"{name}.mp4").write_bytes(f"{spec}\n{name}\n".encode() + b"\x00" * 2048)
    (folder / cfg.cover).write_bytes(_tiny_png())
    (folder / cfg.copy).write_text(copy, encoding="utf-8")
    (folder / "script.txt").write_text("ignored extra file", encoding="utf-8")


def _age(folder: Path, when: datetime) -> None:
    """Date a scratch folder and its files an hour before simulated time ``when``."""
    stamp = (when - timedelta(hours=1)).timestamp()
    for p in [*folder.rglob("*"), folder]:
        os.utime(p, (stamp, stamp))


def _copy_sample(src: Path, dst: Path, cfg: InboxConfig) -> bool:
    """Copy one finished video folder, naming its cover/caption the inbox way if needed."""
    videos = sorted(src.glob("*.mp4"))
    cover = next(
        (
            p
            for n in (cfg.cover, "cover-9x16-final.png", "cover-9x16.png")
            if (p := src / n).exists()
        ),
        None,
    )
    copy = next((p for n in (cfg.copy, "post-caption.txt") if (p := src / n).exists()), None)
    if len(videos) != 1 or cover is None or copy is None:
        return False
    dst.mkdir(parents=True)
    shutil.copy2(videos[0], dst / videos[0].name)
    shutil.copy2(cover, dst / cfg.cover)
    shutil.copy2(copy, dst / cfg.copy)
    return True


@dataclass
class Rehearsal:
    """Result of a rehearsal."""

    data: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True when every check held."""
        return not self.data.get("problems")


def _caption(i: int, title: str) -> str:
    return (
        f"{title} {i}\n\nHow it started, in forty seconds. Story number {i}.\n\n"
        "#WellItStarted #BusinessStories #CompanyHistory"
    )


def rehearse(  # noqa: PLR0915 (one scripted scenario, read top to bottom)
    cfg: InboxConfig,
    out: Path,
    days: int = 14,
    start: datetime | None = None,
    sample: Path | None = None,
    crash_and_failures: bool = True,
) -> Rehearsal:
    """Run the rehearsal in ``out`` (emptied first)."""
    if out.exists():
        shutil.rmtree(out)
    root = out / "page"
    inbox = root / cfg.inbox
    inbox.mkdir(parents=True)
    probe: Probe = sidecar_probe
    if sample is not None:
        n = 0
        for src in sorted(p for p in sample.iterdir() if p.is_dir()):
            n += _copy_sample(src, inbox / src.name, cfg)
        if not n:
            raise ValueError("no usable video folders in the sample folder")
        probe = ffmpeg_probe
    else:
        for i in range(1, 19):
            _fake_video(
                inbox / f"{i:02d}-Short",
                f"{i:02d}-Short",
                "1080x1920@40",
                _caption(i, "Short"),
                cfg,
            )
        for i in (1, 2):
            _fake_video(
                inbox / f"L{i}-Long", f"L{i}-Long", "1920x1080@600", _caption(i, "Long"), cfg
            )
        dup = inbox / "99-Duplicate"  # the same video as 01-Short under another name
        shutil.copytree(inbox / "01-Short", dup)
        bad = inbox / "50-NoCover"
        _fake_video(bad, "50-NoCover", "1080x1920@40", _caption(50, "Bad"), cfg)
        (bad / cfg.cover).unlink()
    now = (start or datetime(2026, 10, 24, 0, 5, tzinfo=UTC)).astimezone(UTC)  # spans 25 Oct
    for folder in inbox.iterdir():
        _age(folder, now)
    before = {
        p.relative_to(inbox).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in inbox.rglob("*")
        if p.is_file()
    }

    # every destination on, in the rehearsal only
    cfg = replace(cfg, destinations=tuple(replace(d, enabled=True) for d in cfg.destinations))
    ig, yt_short, yt_long, host = FakeInstagram(), FakeYouTube(), FakeYouTube(), FakeHost()
    publishers: dict[str, Publisher] = {
        "instagram-reels": InstagramReels(
            cfg.page, ig, host, cfg.ai_generated, [0.0], 3, sleep=lambda _s: None
        ),
        "youtube-shorts": YouTubeVideos(yt_short, True, cfg.ai_generated),
        "youtube-long": YouTubeVideos(yt_long, False, cfg.ai_generated),
    }
    clock = [now]
    runner = InboxRunner(cfg, root, publishers, probe, lambda: clock[0])
    end = now + timedelta(days=days)
    events: Counter[str] = Counter()
    alerts: set[str] = set()
    pc_off = (now + timedelta(days=5, hours=1), now + timedelta(days=5, hours=11))
    pause = (now + timedelta(days=3, hours=6), now + timedelta(days=3, hours=14))
    late_added = False
    if crash_and_failures:
        ig.fail_next.add("publish_transient")
    while clock[0] < end:
        t = clock[0]
        if pc_off[0] <= t < pc_off[1]:
            clock[0] += TICK
            continue
        pause_file = root / cfg.pause_file
        if pause[0] <= t < pause[1]:
            pause_file.write_text("paused", encoding="utf-8")
        else:
            pause_file.unlink(missing_ok=True)
        if crash_and_failures:
            day = (t - now).days
            if day == 1 and "crash1" not in events:
                ig.fail_next.add("crash_after_publish")
                events["crash1"] += 1
            if day == 2 and "err1" not in events:
                ig.fail_next.add("container_error")
                yt_short.fail_next.add("upload_transient")
                events["err1"] += 1
            if day == 3 and "crash2" not in events:
                yt_short.fail_next.add("crash_after_upload")
                events["crash2"] += 1
        if sample is None and not late_added and t >= now + timedelta(days=6):
            for i in range(19, 25):
                _fake_video(
                    inbox / f"{i:02d}-Short",
                    f"{i:02d}-Short",
                    "1080x1920@40",
                    _caption(i, "Short"),
                    cfg,
                )
                _age(inbox / f"{i:02d}-Short", t + timedelta(minutes=55))  # just arrived
            late_added = True
        try:
            report = runner.run()
        except SimulatedCrashError:
            events["crashes"] += 1
            runner = InboxRunner(cfg, root, publishers, probe, lambda: clock[0])
        else:
            events["paused_runs"] += report.paused
            alerts |= {a.title for a in report.alerts.raise_}
        clock[0] += TICK

    problems: list[str] = []
    ledger = runner.ledger
    ig_videos = [c["container"] for c in ig.calls if c["op"] == "publish"]
    if len(ig_videos) != len(set(ig_videos)):
        problems.append("an Instagram container was published twice")
    for name, fake in (("youtube-shorts", yt_short), ("youtube-long", yt_long)):
        titles = [u["title"] for u in fake.uploads]
        if len(titles) != len(set(titles)):
            problems.append(f"{name}: a video was uploaded twice")
    captions = [m["caption"] for m in ig.media]
    if len(captions) != len(set(captions)):
        problems.append("instagram-reels: a video was posted twice")
    after = {
        p.relative_to(inbox).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in inbox.rglob("*")
        if p.is_file() and p.relative_to(inbox).parts[0] in {k.split("/")[0] for k in before}
    }
    if after != before:
        problems.append("the drop folder was changed")
    if host.online:
        problems.append(f"{len(host.online)} hosted files were not removed")
    for dest in cfg.destinations:
        for info in ledger.posted(dest.name).values():
            note = Path(info["folder"]) / f"{dest.label}.txt"
            if not note.exists():
                problems.append(f"{dest.name}: result file missing for {info['name']}")
    if (root / "working").exists() and any((root / "working").rglob("*.mp4")):
        leftover = [p.parent.name for p in (root / "working").rglob("*.mp4")]
        problems.append(f"working copies left behind: {leftover}")
    outcomes = Counter(v["outcome"] for v in ledger.data["slots"].values())
    if any(v["outcome"] == "posted" for v in ledger.data["slots"].values()) and not ig.media:
        problems.append("slots say posted but nothing reached Instagram")
    if sample is None and "50-NoCover" not in ledger.invalid:
        problems.append("the folder without a cover was not reported")
    return Rehearsal(
        {
            "days": days,
            "start": now.isoformat(),
            "folder": str(root),
            "posted": {d.name: len(ledger.posted(d.name)) for d in cfg.destinations},
            "failed": {
                d.name: sorted(i["name"] for i in ledger.failed(d.name).values())
                for d in cfg.destinations
            },
            "slots": dict(outcomes),
            "crashes_recovered": events["crashes"],
            "paused_runs": events["paused_runs"],
            "ai_label": {
                "instagram": all(c.get("ai_generated") for c in ig.calls if c["op"] == "create"),
                "youtube": all(u["synthetic"] for u in yt_short.uploads + yt_long.uploads),
            },
            "alerts": sorted(alerts),
            "problems": problems,
        }
    )


def run_guarded(*args: Any, **kwargs: Any) -> Rehearsal:
    """``rehearse`` with every Meta, Google and YouTube host blocked."""
    with no_instagram_network(("googleapis.com", "google.com", "youtube.com")):
        return rehearse(*args, **kwargs)
