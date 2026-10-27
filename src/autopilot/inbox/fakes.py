"""Fakes for rehearsals and tests: a hosting folder and a YouTube channel that only record."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autopilot.errors import TransientPublishError
from autopilot.inbox.folders import VideoInfo
from autopilot.rehearsal.fakes import SimulatedCrashError


@dataclass
class FakeHost:
    """Files 'hosted' in memory; remembers what is still online."""

    online: dict[str, int] = field(default_factory=dict)
    puts: int = 0

    def put(self, files: dict[str, bytes]) -> dict[str, str]:
        self.puts += 1
        for name, data in files.items():
            self.online[name] = len(data)
        return {name: f"https://media.example.test/{name}" for name in files}

    def remove(self, names: list[str]) -> None:
        for name in names:
            self.online.pop(name, None)


@dataclass
class FakeYouTube:
    """Records uploads; ``fail_next`` may hold ``upload_transient`` or ``crash_after_upload``."""

    uploads: list[dict[str, Any]] = field(default_factory=list)
    thumbnails: list[str] = field(default_factory=list)
    fail_next: set[str] = field(default_factory=set)

    def upload(self, video: Path, title: str, description: str, synthetic: bool) -> str:
        if "upload_transient" in self.fail_next:
            self.fail_next.discard("upload_transient")
            raise TransientPublishError("YouTube upload: HTTP 503 (backendError)")
        vid = f"yt{len(self.uploads) + 1:04d}"
        self.uploads.insert(
            0,
            {
                "id": vid,
                "title": title,
                "synthetic": synthetic,
                "bytes": video.stat().st_size,
                "description_chars": len(description),
            },
        )
        if "crash_after_upload" in self.fail_next:
            self.fail_next.discard("crash_after_upload")
            raise SimulatedCrashError
        return vid

    def recent_uploads(self, limit: int) -> list[dict[str, str]]:
        return [{"id": u["id"], "title": u["title"]} for u in self.uploads[:limit]]

    def set_thumbnail(self, video_id: str, image: Path) -> None:
        self.thumbnails.append(video_id)


def sidecar_probe(path: Path) -> VideoInfo:
    """Test probe: reads ``WxH@seconds`` from the fake video file itself."""
    head = path.read_bytes()[:40].decode("ascii", "replace")
    size, _, secs = head.split("\n", 1)[0].partition("@")
    w, _, h = size.partition("x")
    return VideoInfo(int(w), int(h), float(secs))
