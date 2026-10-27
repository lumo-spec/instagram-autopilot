"""Where inbox videos are published: Instagram Reels and YouTube (Shorts or long videos).

Each destination checks a video against its own limits (``problems``) and publishes it exactly
once (``publish``). Before anything irreversible it records an *intent* through ``save_intent``,
so a run that crashes half way is reconciled by the next run instead of posting again.
Temporary trouble raises ``TransientPublishError`` (tried again at a later slot); anything else
raises ``PublishError`` (the video goes to ``failed``).
"""

from __future__ import annotations

import io
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autopilot.errors import PublishError, TransientPublishError
from autopilot.inbox.folders import Candidate
from autopilot.manifest import Manifest
from autopilot.publish.instagram import GraphClient, InstagramClient, publish_manifest

SaveIntent = Callable[[dict[str, Any]], None]
_HASHTAG = re.compile(r"(?<!\w)#\w+")


@dataclass(frozen=True)
class Posted:
    """A successful publish."""

    media_id: str
    url: str
    reconciled: bool = False  # found already published by an earlier, interrupted run
    note: str = ""


class Publisher(Protocol):
    """One destination."""

    label: str

    def problems(self, video: Candidate) -> list[str]: ...
    def publish(
        self, video: Candidate, slot_key: str, intent: dict[str, Any] | None, save: SaveIntent
    ) -> Posted: ...


# --- Instagram -----------------------------------------------------------------------------
class VideoHost(Protocol):
    """Instagram fetches reels from a public address: put the files there, remove them after."""

    def put(self, files: dict[str, bytes]) -> dict[str, str]: ...  # name -> public URL
    def remove(self, names: list[str]) -> None: ...


class NoHost:
    """Placeholder until the owner chooses how videos reach Instagram."""

    def put(self, files: dict[str, bytes]) -> dict[str, str]:
        raise PublishError(
            "Instagram needs the video at a public web address and no hosting is chosen yet"
        )

    def remove(self, names: list[str]) -> None:
        return None


class ReelClient(GraphClient):
    """``GraphClient`` that also sends the reel's cover image."""

    cover_urls: dict[str, str]

    def create_container(self, manifest: Manifest) -> str:
        params: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": manifest.media_urls[0],
            "caption": manifest.caption,
            "share_to_feed": True,
        }
        cover = getattr(self, "cover_urls", {}).get(manifest.slot_key)
        if cover:
            params["cover_url"] = cover
        if manifest.ai_generated:
            params["is_ai_generated"] = True
        return str(self._request("POST", f"{self._user}/media", params)["id"])


def cover_jpeg(png: Path) -> bytes:
    """The cover as a JPEG (Meta's cover format), 1080 px wide at most."""
    from PIL import Image

    with Image.open(png) as im:
        rgb = im.convert("RGB")
        if rgb.width > 1080:
            rgb = rgb.resize((1080, round(rgb.height * 1080 / rgb.width)), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=92)
        return out.getvalue()


class InstagramReels:
    """Publish a video as an Instagram Reel (shared to the feed) with its cover."""

    label = "Instagram Reels"
    MAX_BYTES = 300_000_000  # Meta's reel file limit
    SECONDS = (3.0, 900.0)  # 3 s to 15 min
    MAX_CAPTION = 2200
    MAX_HASHTAGS = 5  # Instagram caps posts at 5 hashtags (Dec 2025)

    def __init__(
        self,
        page: str,
        client: InstagramClient,
        host: VideoHost,
        ai_generated: bool,
        delays: list[float],
        retries: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.page = page
        self.client = client
        self.host = host
        self.ai = ai_generated
        self.delays = delays
        self.retries = retries
        self.sleep = sleep

    def problems(self, video: Candidate) -> list[str]:
        out = []
        if video.size > self.MAX_BYTES:
            out.append(f"video is {video.size // 1_000_000} MB (Instagram allows 300 MB)")
        lo, hi = self.SECONDS
        if not lo <= video.info.seconds <= hi:
            out.append(f"video is {video.info.seconds:.0f} s (Instagram Reels: 3 s to 15 min)")
        if len(video.text) > self.MAX_CAPTION:
            out.append(f"caption has {len(video.text)} characters (Instagram allows 2,200)")
        tags = len(_HASHTAG.findall(video.text))
        if tags > self.MAX_HASHTAGS:
            out.append(f"caption has {tags} hashtags (Instagram allows 5)")
        return out

    def publish(
        self, video: Candidate, slot_key: str, intent: dict[str, Any] | None, save: SaveIntent
    ) -> Posted:
        base = f"{self.page}-{video.fingerprint[:12]}"
        names = [f"{base}.mp4", f"{base}-cover.jpg"]
        urls = self.host.put(
            {names[0]: video.video.read_bytes(), names[1]: cover_jpeg(video.cover)}
        )
        if isinstance(self.client, ReelClient):
            self.client.cover_urls = {slot_key: urls[names[1]]}
        manifest = Manifest(
            page=self.page,
            slot_key=slot_key,
            content_id=base,
            format="reel",
            audio_mode="",
            fallback="",
            template="",
            caption=video.text,
            hashtags=(),
            media_urls=(urls[names[0]],),
            ai_generated=self.ai,
        )
        # None: never tried; "": tried, no container made (check recent posts by caption).
        previous = None if intent is None else str(intent.get("container", ""))

        def on_intent(container: str) -> None:
            save({"container": container, "slot": slot_key})

        if intent is None:
            save({"container": "", "slot": slot_key})
        try:
            out = publish_manifest(
                self.client, manifest, previous, on_intent, self.delays, self.retries, self.sleep
            )
        except TransientPublishError:
            raise
        except PublishError as exc:
            # Meta could not process or fetch the video this time: a later slot makes a fresh
            # container (prepare_container replaces an ERROR/EXPIRED one).
            if "container status" in str(exc) or "did not finish" in str(exc):
                raise TransientPublishError(str(exc)) from None
            raise
        self.host.remove(names)
        return Posted(out.media_id, out.permalink, out.reconciled)


# --- YouTube -------------------------------------------------------------------------------
class YouTubeClient(Protocol):
    """Operations the YouTube destination needs (real or fake)."""

    def upload(self, video: Path, title: str, description: str, synthetic: bool) -> str: ...
    def recent_uploads(self, limit: int) -> list[dict[str, str]]: ...  # id, title
    def set_thumbnail(self, video_id: str, image: Path) -> None: ...


def title_and_description(text: str) -> tuple[str, str]:
    """First line = title, the rest = description."""
    first, _, rest = text.strip().partition("\n")
    return first.strip(), rest.strip()


class YouTubeVideos:
    """Upload as a YouTube Short (``short=True``) or a normal video with a custom thumbnail."""

    MAX_TITLE = 100
    MAX_DESCRIPTION_BYTES = 5000
    SHORT_SECONDS = 180.0

    def __init__(self, client: YouTubeClient, short: bool, ai_generated: bool) -> None:
        self.client = client
        self.short = short
        self.ai = ai_generated
        self.label = "YouTube Shorts" if short else "YouTube videos"

    def problems(self, video: Candidate) -> list[str]:
        title, desc = title_and_description(video.text)
        out = []
        if not title or len(title) > self.MAX_TITLE:
            out.append(
                f"the first line (the YouTube title) must be 1-100 characters ({len(title)})"
            )
        if len(desc.encode()) > self.MAX_DESCRIPTION_BYTES:
            out.append("the description is longer than YouTube's 5,000 bytes")
        if any(c in video.text for c in "<>"):
            out.append("YouTube does not accept < or > in titles and descriptions")
        if self.short and (
            video.info.seconds > self.SHORT_SECONDS or video.info.height < video.info.width
        ):
            out.append("a YouTube Short must be vertical or square and at most 3 minutes")
        return out

    def _url(self, video_id: str) -> str:
        if self.short:
            return f"https://www.youtube.com/shorts/{video_id}"
        return f"https://www.youtube.com/watch?v={video_id}"

    def publish(
        self, video: Candidate, slot_key: str, intent: dict[str, Any] | None, save: SaveIntent
    ) -> Posted:
        title, desc = title_and_description(video.text)
        if intent is not None:  # an earlier run may have finished the upload before crashing
            for item in self.client.recent_uploads(15):
                if item.get("title") == title:
                    return Posted(item["id"], self._url(item["id"]), reconciled=True)
        save({"title": title, "slot": slot_key})
        video_id = self.client.upload(video.video, title, desc, self.ai)
        note = ""
        if not self.short:
            try:
                self.client.set_thumbnail(video_id, video.cover)
            except PublishError as exc:  # needs a verified channel; the video is still up
                note = f"custom thumbnail not set ({exc})"
        return Posted(video_id, self._url(video_id), note=note)
