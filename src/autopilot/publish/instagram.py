"""Instagram API with Instagram Login: quota, containers, carousels, polling, publish, reconcile.

This module runs in the secret-holding ``publish`` job, so it uses only the standard library
(urllib) plus the package's own small modules. It never logs request headers, tokens, user IDs
or raw API responses; errors carry only an HTTP status and Meta's numeric error code.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from autopilot.errors import PublishError, TransientPublishError
from autopilot.manifest import Manifest

FINISHED, PUBLISHED, IN_PROGRESS, ERROR, EXPIRED = (
    "FINISHED",
    "PUBLISHED",
    "IN_PROGRESS",
    "ERROR",
    "EXPIRED",
)


class InstagramClient(Protocol):
    """Operations the publishing flow needs (real or fake)."""

    def publishing_usage(self) -> int: ...
    def create_container(self, manifest: Manifest) -> str: ...
    def create_carousel_item(self, image_url: str) -> str: ...
    def create_carousel(self, children: list[str], manifest: Manifest) -> str: ...
    def container_status(self, container_id: str) -> str: ...
    def publish(self, container_id: str) -> str: ...
    def permalink(self, media_id: str) -> str: ...
    def recent_media(self, limit: int) -> list[dict[str, str]]: ...


class GraphClient:
    """Real client for ``graph.instagram.com`` (token sent only in the Authorization header)."""

    def __init__(
        self, host: str, version: str, user_id: str, token: str, timeout: float = 30
    ) -> None:
        self._base = f"https://{host}/{version}"
        self._user = user_id
        self._token = token
        self._timeout = timeout

    def __repr__(self) -> str:  # never show the token or the user id
        return "GraphClient(<redacted>)"

    def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self._base}/{path}"
        body = None
        headers = {"Authorization": f"Bearer {self._token}"}
        if method == "GET" and params:
            url += "?" + urllib.parse.urlencode(params)
        elif params is not None:
            body = json.dumps(params).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                return dict(json.loads(resp.read().decode()))
        except urllib.error.HTTPError as exc:
            code = _error_code(exc)
            msg = f"Instagram API HTTP {exc.code} (error code {code})"
            if exc.code >= 500 or exc.code == 429:
                raise TransientPublishError(msg) from None
            raise PublishError(msg) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TransientPublishError("network error talking to Instagram") from None

    def publishing_usage(self) -> int:
        data = self._request(
            "GET", f"{self._user}/content_publishing_limit", {"fields": "quota_usage"}
        )
        items = data.get("data") or [{}]
        return int(items[0].get("quota_usage", 0))

    def create_container(self, manifest: Manifest) -> str:
        if manifest.format == "carousel":
            raise PublishError("carousels are created with create_carousel")
        params: dict[str, Any] = {"caption": manifest.caption}
        if manifest.format == "image":
            params["image_url"] = manifest.media_urls[0]
        else:
            params.update(media_type="REELS", video_url=manifest.media_urls[0], share_to_feed=True)
        if manifest.ai_generated:
            params["is_ai_generated"] = True
        return str(self._request("POST", f"{self._user}/media", params)["id"])

    def create_carousel_item(self, image_url: str) -> str:
        # Children carry no caption and no AI flag (Meta: setting it on children is an error).
        params = {"image_url": image_url, "is_carousel_item": True}
        return str(self._request("POST", f"{self._user}/media", params)["id"])

    def create_carousel(self, children: list[str], manifest: Manifest) -> str:
        params: dict[str, Any] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": manifest.caption,
        }
        if manifest.ai_generated:
            params["is_ai_generated"] = True
        return str(self._request("POST", f"{self._user}/media", params)["id"])

    def container_status(self, container_id: str) -> str:
        return str(
            self._request("GET", container_id, {"fields": "status_code"}).get("status_code", "")
        )

    def publish(self, container_id: str) -> str:
        return str(
            self._request("POST", f"{self._user}/media_publish", {"creation_id": container_id})[
                "id"
            ]
        )

    def permalink(self, media_id: str) -> str:
        return str(self._request("GET", media_id, {"fields": "permalink"}).get("permalink", ""))

    def recent_media(self, limit: int) -> list[dict[str, str]]:
        data = self._request(
            "GET", f"{self._user}/media", {"fields": "id,caption,timestamp", "limit": limit}
        )
        return [dict(m) for m in data.get("data", [])]


def _error_code(exc: urllib.error.HTTPError) -> str:
    """Only Meta's numeric error code is kept; messages are discarded (they may echo input)."""
    try:
        err = json.loads(exc.read().decode()).get("error", {})
        return str(int(err.get("code", 0)))
    except (ValueError, AttributeError, OSError):
        return "unknown"


@dataclass
class PublishOutcome:
    """What happened to a slot."""

    container_id: str
    media_id: str
    permalink: str
    reconciled: bool = False


IntentCallback = Callable[[str], None]  # receives the container id as soon as it exists


def wait_for_container(
    client: InstagramClient,
    container_id: str,
    delays: list[float],
    sleep: Callable[[float], None],
) -> str:
    """Poll until FINISHED/PUBLISHED; ERROR, EXPIRED or timing out raise PublishError."""
    for delay in [*delays, 0.0]:
        status = client.container_status(container_id)
        if status in (FINISHED, PUBLISHED):
            return status
        if status in (ERROR, EXPIRED):
            raise PublishError(f"container status {status}")
        if delay:
            sleep(delay)
    raise PublishError("container did not finish processing in time")


def _find_published(client: InstagramClient, caption: str) -> str:
    for media in client.recent_media(10):
        if media.get("caption", "") == caption:
            return str(media["id"])
    return ""


def _with_retries(fn: Callable[[], str], retries: int, sleep: Callable[[float], None]) -> str:
    for attempt in range(retries):
        try:
            return fn()
        except TransientPublishError:
            if attempt == retries - 1:
                raise
            sleep(2 ** (attempt + 2))
    raise PublishError("unreachable")


def _bind(fn: Callable[[str], str], arg: str) -> Callable[[], str]:
    return lambda: fn(arg)


def _create_carousel(
    client: InstagramClient,
    manifest: Manifest,
    delays: list[float],
    retries: int,
    sleep: Callable[[float], None],
) -> str:
    """Create one child container per slide (in order), wait for each, then the carousel.

    Children on their own can never be published, so a crash here leaves only harmless,
    self-expiring containers; the next run simply creates new ones.
    """
    children: list[str] = []
    for url in manifest.media_urls:
        child = _with_retries(_bind(client.create_carousel_item, url), retries, sleep)
        wait_for_container(client, child, delays, sleep)
        children.append(child)
    return _with_retries(lambda: client.create_carousel(children, manifest), retries, sleep)


def publish_manifest(
    client: InstagramClient,
    manifest: Manifest,
    previous_container: str | None,
    on_intent: IntentCallback,
    delays: list[float],
    retries: int,
    sleep: Callable[[float], None] = time.sleep,
) -> PublishOutcome:
    """Publish one slot exactly once, reconciling any earlier attempt first.

    ``previous_container`` is None when this slot has never been attempted; otherwise the
    container id from its earlier intent record ("" when that attempt never created one).
    ``on_intent`` must durably record the new container id before publishing continues.
    Recent media are searched (by exact caption) only for slots with an earlier attempt, so a
    new post is never mistaken for an older one; captions are unique within a page's queue.
    """
    if previous_container:
        status = client.container_status(previous_container)
        if status == PUBLISHED:
            media_id = _find_published(client, manifest.caption)
            if not media_id:
                raise PublishError("container published but media not found; not re-posting")
            return PublishOutcome(previous_container, media_id, client.permalink(media_id), True)
        container = previous_container if status in (FINISHED, IN_PROGRESS) else ""
    elif previous_container == "":
        # Intent without container (crash before it existed): make sure nothing went out.
        media_id = _find_published(client, manifest.caption)
        if media_id:
            return PublishOutcome("", media_id, client.permalink(media_id), True)
        container = ""
    else:
        container = ""
    if not container:
        if manifest.format == "carousel":
            container = _create_carousel(client, manifest, delays, retries, sleep)
        else:
            container = _with_retries(lambda: client.create_container(manifest), retries, sleep)
        on_intent(container)
    wait_for_container(client, container, delays, sleep)

    def do_publish() -> str:
        try:
            return client.publish(container)
        except TransientPublishError:
            # The publish may have gone through; never publish twice.
            if client.container_status(container) == PUBLISHED:
                found = _find_published(client, manifest.caption)
                if found:
                    return found
            raise

    media_id = _with_retries(do_publish, retries, sleep)
    return PublishOutcome(container, media_id, client.permalink(media_id))
