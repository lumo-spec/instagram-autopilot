"""The small manifest that ``build`` hands to ``publish``; every receiver re-validates it."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from autopilot.errors import ValidationError

PAGE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
SLOT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}-\d{4}-\d{2}-\d{2}-[a-z0-9_]{1,40}$")
# bank entries (q-1a2b3c4d), exact-text posts (custom-...), or queue post ids (c01-you-can-miss)
CONTENT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,9}$")
MAX_CONTENT_ID = 48
TEMPLATE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
FORMATS = ("image", "reel", "carousel")
CAROUSEL_MAX = 10  # Instagram API limit for carousel children
AUDIO_MODES = ("", "voice+music", "music", "voice")
FALLBACKS = ("", "second-voice", "music-only", "image", "skipped")
HASHTAG_RE = re.compile(r"^#[a-z0-9_]{2,40}$")


@dataclass(frozen=True)
class Manifest:
    """Everything publish needs, nothing more."""

    page: str
    slot_key: str
    content_id: str
    format: str
    audio_mode: str
    fallback: str
    template: str
    caption: str
    hashtags: tuple[str, ...]
    media_urls: tuple[str, ...]  # one for image/reel; 2-10 slides (in order) for a carousel
    ai_generated: bool

    def to_json(self) -> str:
        """Serialise for hand-over between jobs."""
        data = asdict(self)
        data["hashtags"] = list(self.hashtags)
        data["media_urls"] = list(self.media_urls)
        return json.dumps(data, sort_keys=True)


def media_names(slot_key: str, fmt: str, count: int) -> list[str]:
    """Hosted file names for a post, e.g. ``<slot>.jpg`` or ``<slot>-01.jpg`` ... for slides."""
    if fmt == "carousel":
        return [f"{slot_key}-{i:02d}.jpg" for i in range(1, count + 1)]
    return [f"{slot_key}{'.jpg' if fmt == 'image' else '.mp4'}"]


def allowed_media_prefixes(repo: str, page: str) -> tuple[str, ...]:
    """URL prefixes a page's hosted media may use (the repository's own hosts only)."""
    owner, name = repo.split("/", 1)
    return (
        f"https://raw.githubusercontent.com/{owner}/{name}/media/{page}/",
        f"https://{owner}.github.io/{name}/{page}/",
    )


def validate_manifest(
    data: dict[str, Any],
    repo: str,
    caption_max: int,
    max_hashtags: int,
    allow_rehearsal: bool = False,
) -> Manifest:
    """Strictly re-validate a manifest received from another job."""
    expected = set(Manifest.__dataclass_fields__)
    if not isinstance(data, dict) or set(data) != expected:
        raise ValidationError("manifest fields do not match")

    def check(cond: bool, what: str) -> None:
        if not cond:
            raise ValidationError(f"manifest: invalid {what}")

    page = data["page"]
    check(isinstance(page, str) and bool(PAGE_RE.match(page)), "page")
    check(
        isinstance(data["slot_key"], str) and bool(SLOT_KEY_RE.match(data["slot_key"])), "slot_key"
    )
    check(data["slot_key"].startswith(page + "-"), "slot_key/page pairing")
    check(
        isinstance(data["content_id"], str)
        and len(data["content_id"]) <= MAX_CONTENT_ID
        and bool(CONTENT_ID_RE.match(data["content_id"])),
        "content_id",
    )
    check(data["format"] in FORMATS, "format")
    check(data["audio_mode"] in AUDIO_MODES, "audio_mode")
    check(data["fallback"] in FALLBACKS, "fallback")
    check(
        isinstance(data["template"], str) and bool(TEMPLATE_RE.match(data["template"])), "template"
    )
    caption = data["caption"]
    check(isinstance(caption, str) and 0 < len(caption) <= caption_max, "caption length")
    tags = data["hashtags"]
    check(isinstance(tags, list) and len(tags) <= max_hashtags, "hashtag count")
    check(all(isinstance(t, str) and HASHTAG_RE.match(t) for t in tags), "hashtags")
    check(sum(1 for w in caption.split() if w.startswith("#")) <= max_hashtags, "caption hashtags")
    check(isinstance(data["ai_generated"], bool), "ai_generated")
    urls = data["media_urls"]
    check(isinstance(urls, list) and all(isinstance(u, str) for u in urls), "media_urls")
    if data["format"] == "carousel":
        check(2 <= len(urls) <= CAROUSEL_MAX, "carousel slide count")
    else:
        check(len(urls) == 1, "media_urls count")
    names = media_names(data["slot_key"], data["format"], len(urls))
    for url, name in zip(urls, names, strict=True):
        if allow_rehearsal and url.startswith("rehearsal://"):
            check(url == f"rehearsal://{page}/{name}", "rehearsal media_url")
            continue
        parsed = urlparse(url)
        check(parsed.scheme == "https" and not parsed.query and not parsed.fragment, "media_url")
        check(any(url.startswith(p) for p in allowed_media_prefixes(repo, page)), "media_url host")
        check(url.endswith(f"/{name}"), "media_url file name")
    return Manifest(
        page=page,
        slot_key=data["slot_key"],
        content_id=data["content_id"],
        format=data["format"],
        audio_mode=data["audio_mode"],
        fallback=data["fallback"],
        template=data["template"],
        caption=caption,
        hashtags=tuple(tags),
        media_urls=tuple(urls),
        ai_generated=data["ai_generated"],
    )
