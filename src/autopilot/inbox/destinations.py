"""Where inbox videos are published: Instagram Reels and YouTube (Shorts or long videos).

Each destination checks a video against its own limits (``problems``) and publishes it exactly
once (``publish``). Before anything irreversible it records an *intent* through ``save_intent``,
so a run that crashes half way is reconciled by the next run instead of posting again.
Temporary trouble raises ``TransientPublishError`` (tried again at a later slot); anything else
raises ``PublishError`` (the video goes to ``failed``).
"""

from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autopilot.errors import PublishError, TransientPublishError
from autopilot.inbox.folders import Candidate
from autopilot.manifest import Manifest
from autopilot.publish.instagram import (
    GraphClient,
    InstagramClient,
    _error_code,
    publish_manifest,
)

SaveIntent = Callable[[dict[str, Any]], None]
_HASHTAG = re.compile(r"(?<!\w)#\w+")


@dataclass(frozen=True)
class Posted:
    """A successful publish."""

    media_id: str
    url: str
    reconciled: bool = False  # found already published by an earlier, interrupted run
    note: str = ""
    warning: str = ""  # posted, but the owner must look (also raised as an alert)


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


FACEBOOK_GRAPH_HOST = "graph.facebook.com"
RUPLOAD_HOST = "rupload.facebook.com"


class FacebookReelClient(GraphClient):
    """Instagram API with Facebook Login: the PC uploads the reel file straight to Meta.

    Meta's resumable upload (``upload_type=resumable`` + ``rupload.facebook.com``, checked
    2026-10-27) exists only for Facebook Login for Business, so the video never needs a public
    web address. The token is the Facebook Page's token; it goes only to Meta's two hosts.
    ``videos`` maps a slot to the local file to send for it.
    """

    auth_scheme = "OAuth"  # the header form in Meta's resumable-upload guide

    def __init__(
        self,
        version: str,
        user_id: str,
        page_token: str,
        upload_tries: int = 3,
        timeout: float = 120,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(FACEBOOK_GRAPH_HOST, version, user_id, page_token, timeout)
        self.videos: dict[str, Path] = {}
        self.upload_tries = upload_tries
        self.sleep = sleep

    def __repr__(self) -> str:  # never show the token or the user id
        return "FacebookReelClient(<redacted>)"

    def check_token(self) -> bool:
        return bool(self._request("GET", self._user, {"fields": "id"}))

    def create_container(self, manifest: Manifest) -> str:
        video = self.videos.get(manifest.slot_key)
        if video is None:
            raise PublishError("no local video given for this slot")
        # Meta lists the parameters a resumable reel accepts; share_to_feed is not one of them.
        params: dict[str, Any] = {"media_type": "REELS", "caption": manifest.caption}
        if manifest.ai_generated:
            params["is_ai_generated"] = True
        data = self._request("POST", f"{self._user}/media?upload_type=resumable", params)
        container, uri = str(data.get("id", "")), str(data.get("uri", ""))
        target = urllib.parse.urlsplit(uri)
        if not container or target.scheme != "https" or target.hostname != RUPLOAD_HOST:
            raise PublishError("Instagram did not return a Meta upload address")
        self._upload(container, uri, video)
        return container

    def _upload(self, container: str, uri: str, video: Path) -> None:
        """Send the file, resuming from what Meta already has after a network problem."""
        size, offset = video.stat().st_size, 0
        for attempt in range(self.upload_tries):
            try:
                self._send(uri, video, offset, size)
                return
            except TransientPublishError:
                if attempt == self.upload_tries - 1:
                    raise
                self.sleep(2 ** (attempt + 2))
                offset = self._bytes_received(container, size)

    def _send(self, uri: str, video: Path, offset: int, size: int) -> None:
        headers = {
            "Authorization": f"{self.auth_scheme} {self._token}",
            "offset": str(offset),
            "file_size": str(size),
            "Content-Length": str(size - offset),
            "Content-Type": "application/octet-stream",
        }
        with video.open("rb") as fh:
            fh.seek(offset)
            req = urllib.request.Request(uri, data=fh, method="POST", headers=headers)  # noqa: S310
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                    answer = json.loads(resp.read().decode() or "{}")
            except urllib.error.HTTPError as exc:
                code, _ = _error_code(exc)
                msg = f"Instagram video upload: HTTP {exc.code} (error code {code})"
                if exc.code >= 500 or exc.code == 429:
                    raise TransientPublishError(msg) from None
                raise PublishError(msg) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                raise TransientPublishError(
                    "network error uploading the video to Instagram"
                ) from None
        if not (isinstance(answer, dict) and answer.get("success") is True):
            raise PublishError("Instagram did not accept the video upload")

    def _bytes_received(self, container: str, size: int) -> int:
        try:
            data = self._request("GET", container, {"fields": "video_status"})
            done = int(data["video_status"]["uploading_phase"]["bytes_transferred"])
        except (KeyError, TypeError, ValueError):
            return 0
        return done if 0 <= done < size else 0


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


COVER_NOTE = (
    "cover: Instagram chose a frame of the video (cover.png can only be sent from a public "
    "web address); change it in the Instagram app if needed"
)


class InstagramReels:
    """Publish a video as an Instagram Reel with its cover.

    ``host=None`` = direct upload from this PC (``FacebookReelClient``): nothing is put on a
    public address, and Instagram picks the cover frame itself.
    """

    label = "Instagram Reels"
    MAX_BYTES = 300_000_000  # Meta's reel file limit
    SECONDS = (3.0, 900.0)  # 3 s to 15 min
    MAX_CAPTION = 2200
    MAX_HASHTAGS = 5  # Instagram caps posts at 5 hashtags (Dec 2025)

    def __init__(
        self,
        page: str,
        client: InstagramClient,
        host: VideoHost | None,
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
        if self.host is None:
            videos = getattr(self.client, "videos", None)
            if not isinstance(videos, dict):
                raise PublishError("direct upload needs the Facebook Login connection")
            videos.clear()
            videos[slot_key] = video.video
            urls = {names[0]: video.video.as_uri()}  # a record only; the file is uploaded
        else:
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
        if self.host is None:
            return Posted(out.media_id, out.permalink, out.reconciled, note=COVER_NOTE)
        self.host.remove(names)
        return Posted(out.media_id, out.permalink, out.reconciled)


# --- YouTube -------------------------------------------------------------------------------
class YouTubeClient(Protocol):
    """Operations the YouTube destination needs (real or fake)."""

    def upload(self, video: Path, title: str, description: str, synthetic: bool) -> str: ...
    def recent_uploads(self, limit: int) -> list[dict[str, str]]: ...  # id, title
    def set_thumbnail(self, video_id: str, image: Path) -> None: ...
    def privacy(self, video_id: str) -> str: ...  # "public", "private", "unlisted" or ""


PRIVATE_WARNING = (
    "YouTube shows this video as {status}, not public. The likely cause is YouTube's lock on "
    "uploads from API projects that have not passed its audit (Google's pages disagree on "
    "whether it still applies). Nobody can see the video. Switch YouTube off in inbox.yaml "
    "and see docs/WELLITSTARTED.md, 'YouTube: quota and the private lock'."
)


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
        warning = ""
        try:
            status = self.client.privacy(video_id)
        except PublishError:  # TransientPublishError too: the upload itself is done
            status = ""
        if status and status != "public":
            warning = PRIVATE_WARNING.format(status=status.upper())
        return Posted(video_id, self._url(video_id), note=note, warning=warning)
