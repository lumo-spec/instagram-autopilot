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
def no_instagram_network() -> Iterator[None]:
    """Make any attempt to resolve an Instagram/Meta host fail loudly."""
    original = socket.getaddrinfo

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, (str, bytes)):
            name = host.decode() if isinstance(host, bytes) else host
            if any(name == h or name.endswith("." + h) for h in BLOCKED_HOSTS):
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

    def create_container(self, manifest: Manifest) -> str:
        cid = f"c{len(self.containers) + 1:04d}"
        status = "FINISHED"
        if "container_error" in self.fail_next:
            self.fail_next.discard("container_error")
            status = "ERROR"
        self.containers[cid] = {"status": status, "manifest": manifest}
        self._log(
            "create",
            container=cid,
            format=manifest.format,
            url=manifest.media_url,
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

    def published_slots(self) -> list[str]:
        """Slot keys in publish order (to prove nothing was posted twice)."""
        return [c["slot"] for c in self.calls if c["op"] == "publish"]


class LocalHosting:
    """Stores 'hosted' media in a folder; URLs use the rehearsal:// scheme."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def host(self, page: str, slot_key: str, path: Path) -> str:
        dest = self.root / page / f"{slot_key}{path.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return f"rehearsal://{page}/{slot_key}{path.suffix}"

    def remove(self, page: str, slot_key: str, ext: str) -> None:
        (self.root / page / f"{slot_key}{ext}").unlink(missing_ok=True)
