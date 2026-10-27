"""Fake Instagram client, local hosting and a network guard for rehearsals and tests."""

from __future__ import annotations

import shutil
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autopilot.errors import TransientPublishError
from autopilot.manifest import Manifest

BLOCKED_HOSTS = ("instagram.com", "facebook.com", "fbcdn.net", "facebook.net")


class SimulatedCrashError(Exception):
    """The runner 'died' (rehearsal only)."""


@contextmanager
def no_instagram_network(extra: tuple[str, ...] = ()) -> Iterator[None]:
    """Make any attempt to resolve an Instagram/Meta host (or one of ``extra``) fail loudly."""
    original = socket.getaddrinfo
    blocked = (*BLOCKED_HOSTS, *extra)

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, (str, bytes)):
            name = host.decode() if isinstance(host, bytes) else host
            if any(name == h or name.endswith("." + h) for h in blocked):
                raise AssertionError(f"rehearsal tried to reach {name}")
        return original(host, *args, **kwargs)

    socket.getaddrinfo = guarded
    try:
        yield
    finally:
        socket.getaddrinfo = original


@dataclass
class FakeInstagram:
    """Records what would have been sent; can inject one failure of each kind."""

    usage: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)
    media: list[dict[str, str]] = field(default_factory=list)
    fail_next: set[str] = field(
        default_factory=set
    )  # container_error, publish_transient, crash_after_publish, quota_high

    def _log(self, op: str, **data: Any) -> None:
        self.calls.append({"op": op, **data})

    def publishing_usage(self) -> int:
        self._log("quota")
        if "quota_high" in self.fail_next:
            self.fail_next.discard("quota_high")
            return 45
        return self.usage

    def _new(self, manifest: Manifest | None, kind: str) -> tuple[str, str]:
        cid = f"c{len(self.containers) + 1:04d}"
        status = "FINISHED"
        if kind != "child" and "container_error" in self.fail_next:
            self.fail_next.discard("container_error")
            status = "ERROR"
        self.containers[cid] = {"status": status, "manifest": manifest, "kind": kind}
        return cid, status

    def create_container(self, manifest: Manifest) -> str:
        if manifest.format == "carousel":
            raise AssertionError("carousels must use create_carousel")
        cid, _ = self._new(manifest, manifest.format)
        self._log(
            "create",
            container=cid,
            format=manifest.format,
            url=manifest.media_urls[0],
            ai_generated=manifest.ai_generated,
            caption_chars=len(manifest.caption),
        )
        return cid

    def create_carousel_item(self, image_url: str) -> str:
        if not image_url.endswith(".jpg"):
            raise AssertionError("carousel items must be JPEG")
        cid, _ = self._new(None, "child")
        self._log("create-item", container=cid, url=image_url)
        return cid

    def create_carousel(self, children: list[str], manifest: Manifest) -> str:
        if not 2 <= len(children) <= 10:
            raise AssertionError("carousel needs 2-10 children")
        for c in children:
            box = self.containers[c]
            if box["kind"] != "child" or box["status"] != "FINISHED" or box.get("used"):
                raise AssertionError("carousel child is not a fresh finished item")
            box["used"] = True
        cid, _ = self._new(manifest, "carousel")
        self.containers[cid]["children"] = list(children)
        self._log(
            "create",
            container=cid,
            format="carousel",
            children=len(children),
            ai_generated=manifest.ai_generated,
            caption_chars=len(manifest.caption),
        )
        return cid

    def container_status(self, container_id: str) -> str:
        return str(self.containers[container_id]["status"])

    def publish(self, container_id: str) -> str:
        if "publish_transient" in self.fail_next:
            self.fail_next.discard("publish_transient")
            self._log("publish-failed-transient", container=container_id)
            raise TransientPublishError("simulated 503")
        box = self.containers[container_id]
        if box["kind"] == "child":
            raise AssertionError("a carousel item cannot be published on its own")
        if box["status"] == "PUBLISHED":
            raise AssertionError("double publish attempted")
        box["status"] = "PUBLISHED"
        mid = f"m{len(self.media) + 1:04d}"
        self.media.insert(0, {"id": mid, "caption": box["manifest"].caption})
        self.usage += 1
        self._log("publish", container=container_id, media=mid, slot=box["manifest"].slot_key)
        if "crash_after_publish" in self.fail_next:
            self.fail_next.discard("crash_after_publish")
            raise SimulatedCrashError
        return mid

    def permalink(self, media_id: str) -> str:
        return f"https://www.instagram.com/p/rehearsal-{media_id}/"

    def recent_media(self, limit: int) -> list[dict[str, str]]:
        return self.media[:limit]

    def check_token(self) -> bool:
        self._log("check-token")
        return "bad_token" not in self.fail_next

    def published_slots(self) -> list[str]:
        """Slot keys in publish order (to prove nothing was posted twice)."""
        return [c["slot"] for c in self.calls if c["op"] == "publish"]


class LocalHosting:
    """Stores 'hosted' media in a folder; URLs use the rehearsal:// scheme."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def host(self, page: str, name: str, path: Path) -> str:
        dest = self.root / page / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return f"rehearsal://{page}/{name}"

    def remove(self, page: str, name: str) -> None:
        (self.root / page / name).unlink(missing_ok=True)
