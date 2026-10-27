"""Scan the drop folder: which video folders are ready, which are invalid, in arrival order.

The drop folder belongs to the owner and is only ever read. A folder is looked at once nothing
in it changed for ``settle_minutes`` (a folder still being copied in is left alone).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autopilot.errors import InboxError
from autopilot.inbox.config import InboxConfig

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class VideoInfo:
    """What the scheduler needs to know about a video file."""

    width: int
    height: int
    seconds: float


Probe = Callable[[Path], VideoInfo]


@dataclass(frozen=True)
class Candidate:
    """A ready video folder."""

    name: str
    folder: Path
    video: Path
    cover: Path
    copy: Path
    text: str
    fingerprint: str  # SHA-256 of the video file: the "never post twice" identity
    arrived: float
    kind: str  # "short" or "long"
    info: VideoInfo
    size: int

    def with_folder(self, folder: Path) -> Candidate:
        """The same candidate, its files taken from ``folder`` (the working copy)."""
        return Candidate(
            self.name,
            folder,
            folder / self.video.name,
            folder / self.cover.name,
            folder / self.copy.name,
            self.text,
            self.fingerprint,
            self.arrived,
            self.kind,
            self.info,
            self.size,
        )


@dataclass(frozen=True)
class Rejected:
    """A settled folder that cannot be posted, and why."""

    name: str
    folder: Path
    reason: str
    signature: str  # changes when the owner changes the folder


@dataclass
class ScanResult:
    """Everything one scan found."""

    ready: list[Candidate]
    rejected: list[Rejected]
    waiting: list[str]  # still changing (being copied in)


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, read in 1 MB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _signature(files: list[Path]) -> str:
    parts = sorted(f"{p.name}|{p.stat().st_size}|{p.stat().st_mtime_ns}" for p in files)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def ffmpeg_probe(path: Path) -> VideoInfo:
    """Size and length of a video, read from ffmpeg's banner (ffmpeg comes from imageio-ffmpeg)."""
    import imageio_ffmpeg

    out = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        check=False,
    ).stderr
    dur = re.search(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", out)
    size = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", out)
    if not dur or not size:
        raise InboxError("the video file could not be read (is it a finished MP4?)")
    h, m, s = dur.groups()
    return VideoInfo(int(size.group(1)), int(size.group(2)), int(h) * 3600 + int(m) * 60 + float(s))


def kind_of(info: VideoInfo, short_max_seconds: float) -> str:
    """ "short" for vertical or square videos up to the limit, otherwise "long"."""
    return "short" if info.height >= info.width and info.seconds <= short_max_seconds else "long"


def _problems(folder: Path, cfg: InboxConfig) -> tuple[list[str], list[Path]]:
    files = [p for p in folder.iterdir() if p.is_file()]
    videos = [p for p in files if p.suffix.lower() == ".mp4"]
    names = {p.name.lower(): p for p in files}
    problems = []
    if len(videos) != 1:
        problems.append(f"needs exactly one .mp4 file (found {len(videos)})")
    cover = names.get(cfg.cover.lower())
    if cover is None:
        problems.append(f"{cfg.cover} is missing")
    else:
        with cover.open("rb") as f:
            if f.read(8) != PNG_MAGIC:
                problems.append(f"{cfg.cover} is not a PNG image")
    copy = names.get(cfg.copy.lower())
    if copy is None:
        problems.append(f"{cfg.copy} is missing")
    else:
        try:
            if not copy.read_text(encoding="utf-8-sig").strip():
                problems.append(f"{cfg.copy} is empty")
        except UnicodeDecodeError:
            problems.append(f"{cfg.copy} is not UTF-8 text")
    used = [*videos[:1], *(p for p in (cover, copy) if p is not None)]
    return problems, used


def scan(
    cfg: InboxConfig,
    inbox: Path,
    probe: Probe,
    now: float,
    fingerprints: dict[str, str],
    first_seen: dict[str, float],
) -> ScanResult:
    """Ready folders in arrival order, invalid ones, and ones still being copied in.

    ``fingerprints`` caches video hashes by name/size/mtime so big files are not re-read every
    run; ``first_seen`` keeps each video's arrival time (both are updated in place).
    """
    result = ScanResult([], [], [])
    settle = cfg.settle_minutes * 60
    for folder in sorted(p for p in inbox.iterdir() if p.is_dir()):
        files = [p for p in folder.iterdir() if p.is_file()]
        newest = max([folder.stat().st_mtime, *(p.stat().st_mtime for p in files)])
        if now - newest < settle:
            result.waiting.append(folder.name)
            continue
        problems, used = _problems(folder, cfg)
        if problems:
            sig = _signature(files) if files else "empty"
            result.rejected.append(Rejected(folder.name, folder, "; ".join(problems), sig))
            continue
        video, cover, copy = used
        try:
            info = probe(video)
        except InboxError as exc:
            result.rejected.append(Rejected(folder.name, folder, str(exc), _signature(files)))
            continue
        st = video.stat()
        cache_key = f"{folder.name}/{video.name}|{st.st_size}|{st.st_mtime_ns}"
        if cache_key not in fingerprints:
            fingerprints[cache_key] = file_sha256(video)
        sha = fingerprints[cache_key]
        # Windows: st_ctime is when the folder was created (dropped in) = arrival order.
        first_seen.setdefault(sha, min(folder.stat().st_ctime, now))
        result.ready.append(
            Candidate(
                name=folder.name,
                folder=folder,
                video=video,
                cover=cover,
                copy=copy,
                text=copy.read_text(encoding="utf-8-sig").strip(),
                fingerprint=sha,
                arrived=first_seen[sha],
                kind=kind_of(info, cfg.short_max_seconds),
                info=info,
                size=st.st_size,
            )
        )
    result.ready.sort(key=lambda c: (c.arrived, c.name))
    return result
