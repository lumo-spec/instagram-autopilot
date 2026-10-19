"""The approved-post queue of a story page (``pages/<page>/queue/<post-id>/``).

Each post folder holds ``post.yaml`` (slide text, art briefs, caption, hashtags) and, once the
owner has made it, the text-free art (``art-01.png``, ``art-02.png``, ...). ``Build <post-id>``
composes the slides, runs every gate and writes ``approved.json`` with the SHA-256 of every input
file. Only approved posts whose inputs are unchanged since approval can ever be published; the
publish-time build re-checks this. Nothing else (old quotes, filler) is ever posted.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from autopilot.errors import ContentError

POST_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,9}$")
ART_RE = re.compile(r"^art-(\d{2})\.(png|jpg|jpeg|webp)$")
HASHTAG_RE = re.compile(r"^#[a-z0-9_]{2,40}$")
FORMATS = ("carousel", "image", "reel")
KINDS = ("story", "one-feeling", "repeated-opening", "single", "reel")
POSITIONS = ("top", "middle", "bottom")
FOCUSES = ("top", "center", "bottom")
CHARACTERS = ("rowan", "wren", "others", "none")
BRIEF_FIELDS = ("scene", "emotion", "composition")
AUDIO_MODES = ("voice+music", "voice", "music")
NO_TEXT = "No text, no letters, no numbers, no signs, no logos, no watermark."
MAX_ID_LEN = 48
HOLD_LINE_RE = re.compile(r"^hold\s*:")
ART_CHECKS = "art-check.json"  # written by generate-art: SHA-256 of every checked picture


@dataclass(frozen=True)
class SlideSpec:
    """One slide as written in post.yaml."""

    index: int  # 1-based
    text: str
    position: str
    focus: str
    art: str  # expected art file name (may not exist yet)
    brief: dict[str, Any]


@dataclass(frozen=True)
class Post:
    """A queued post (validated structure; content gates run at build time)."""

    id: str
    dir: Path
    format: str
    topic: str
    kind: str
    slides: tuple[SlideSpec, ...]
    caption: str
    hashtags: tuple[str, ...]
    priority: int = 100
    audio: str = "voice+music"
    template: str = ""
    hold: str = ""  # non-empty: on hold (the reason) - never published or exported

    @property
    def art_paths(self) -> list[Path]:
        """Where each slide's art is expected."""
        return [self.dir / s.art for s in self.slides]

    def missing_art(self) -> list[str]:
        """Art files that have not been supplied yet."""
        return [s.art for s in self.slides if not (self.dir / s.art).is_file()]

    @property
    def plain_texts(self) -> list[str]:
        """Slide texts without markup, for spelling and captions."""
        return [strip_markup(s.text) for s in self.slides]


@dataclass(frozen=True)
class Approval:
    """Contents of approved.json."""

    id: str
    approved_at: str
    format: str
    slides: int
    inputs: dict[str, str]
    gates: str


@dataclass
class QueuedPost:
    """A post plus its approval state."""

    post: Post
    approval: Approval | None
    problems: list[str] = field(default_factory=list)  # why it cannot be published (if any)

    @property
    def publishable(self) -> bool:
        """Approved, and nothing changed since approval."""
        return self.approval is not None and not self.problems


def strip_markup(text: str) -> str:
    """Remove ``**emphasis**`` markers and collapse whitespace."""
    return " ".join(text.replace("**", "").split())


def _require(cond: bool, problems: list[str], message: str) -> None:
    if not cond:
        problems.append(message)


def _brief_problems(n: int, brief: Any, problems: list[str]) -> None:
    if not isinstance(brief, dict):
        problems.append(f"slide {n}: brief must be a mapping")
        return
    for key in BRIEF_FIELDS:
        _require(bool(str(brief.get(key, "")).strip()), problems, f"slide {n}: brief.{key} missing")
    chars = brief.get("characters", [])
    _require(
        isinstance(chars, list) and all(c in CHARACTERS for c in chars),
        problems,
        f"slide {n}: brief.characters must be a list of {CHARACTERS}",
    )


def parse_post(post_dir: Path) -> tuple[Post | None, list[str]]:
    """Read and structurally validate ``post.yaml``; returns (post or None, problems)."""
    problems: list[str] = []
    path = post_dir / "post.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None, [f"{post_dir.name}: cannot read post.yaml"]
    if not isinstance(data, dict):
        return None, [f"{post_dir.name}: post.yaml must be a mapping"]
    pid = str(data.get("id", ""))
    _require(bool(POST_ID_RE.match(pid)) and len(pid) <= MAX_ID_LEN, problems, f"bad id {pid!r}")
    _require(pid == post_dir.name, problems, "id must equal the folder name")
    fmt = data.get("format")
    _require(fmt in FORMATS, problems, f"format must be one of {FORMATS}")
    kind = data.get("kind", "single")
    _require(kind in KINDS, problems, f"kind must be one of {KINDS}")
    raw_slides = data.get("slides")
    slides: list[SlideSpec] = []
    if not isinstance(raw_slides, list) or not raw_slides:
        problems.append("slides must be a non-empty list")
        raw_slides = []
    for n, s in enumerate(raw_slides, start=1):
        if not isinstance(s, dict):
            problems.append(f"slide {n}: must be a mapping")
            continue
        text = str(s.get("text", "")).strip()
        _require(bool(text), problems, f"slide {n}: text is empty")
        _require(text.count("**") % 2 == 0, problems, f"slide {n}: unbalanced ** emphasis")
        position = s.get("position", "middle")
        _require(position in POSITIONS, problems, f"slide {n}: position must be {POSITIONS}")
        focus = s.get("focus", "center")
        _require(focus in FOCUSES, problems, f"slide {n}: focus must be {FOCUSES}")
        art = str(s.get("art", f"art-{n:02d}.png"))
        m = ART_RE.match(art)
        _require(
            bool(m) and m is not None and int(m.group(1)) == n,
            problems,
            f"slide {n}: art must be named art-{n:02d}.png/.jpg/.webp",
        )
        _brief_problems(n, s.get("brief"), problems)
        slides.append(
            SlideSpec(n, text, str(position), str(focus), art, dict(s.get("brief") or {}))
        )
    caption = str(data.get("caption", "")).strip()
    _require(bool(caption), problems, "caption is empty")
    tags = data.get("hashtags", [])
    _require(
        isinstance(tags, list) and all(isinstance(t, str) and HASHTAG_RE.match(t) for t in tags),
        problems,
        "hashtags must be a list like ['#kindness', '#selfcare']",
    )
    _require(isinstance(tags, list) and len(set(tags)) == len(tags), problems, "duplicate hashtags")
    _require("#" not in caption, problems, "put hashtags in 'hashtags', not in the caption")
    audio = str(data.get("audio", "voice+music"))
    _require(audio in AUDIO_MODES, problems, f"audio must be one of {AUDIO_MODES}")
    hold = data.get("hold", False)
    _require(isinstance(hold, bool | str), problems, "hold must be true/false or a short reason")
    if problems:
        return None, [f"{post_dir.name}: {p}" for p in problems]
    return (
        Post(
            id=pid,
            dir=post_dir,
            format=str(fmt),
            topic=str(data.get("topic", "")),
            kind=str(kind),
            slides=tuple(slides),
            caption=caption,
            hashtags=tuple(tags),
            priority=int(data.get("priority", 100)),
            audio=audio,
            template=str(data.get("template", "")),
            hold=_hold_reason(hold),
        ),
        [],
    )


def _hold_reason(value: object) -> str:
    if value is True:
        return "put on hold by the owner"
    return " ".join(value.split())[:200] if isinstance(value, str) else ""


def structure_problems(post: Post, slide_range: tuple[int, int], cover_max_words: int) -> list[str]:
    """Format rules that do not need rendering (slide count, cover length)."""
    problems: list[str] = []
    n = len(post.slides)
    lo, hi = slide_range
    if post.format == "carousel":
        _require(lo <= n <= hi, problems, f"carousel needs {lo}-{hi} slides, has {n}")
        words = len(strip_markup(post.slides[0].text).split()) if post.slides else 0
        _require(
            words <= cover_max_words,
            problems,
            f"first slide has {words} words; keep it to {cover_max_words} or fewer",
        )
    else:
        _require(n == 1, problems, f"{post.format} posts have exactly one slide, has {n}")
    return problems


def _sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in (".yaml", ".yml"):
        # Same hash on Windows and Linux checkouts; the hold line is not content (putting a post
        # on hold and releasing it keeps its approval). Any other edit changes the hash.
        lines = data.decode("utf-8").replace("\r\n", "\n").split("\n")
        text = "\n".join(ln for ln in lines if not HOLD_LINE_RE.match(ln)).rstrip("\n")
        data = text.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def input_hashes(post: Post) -> dict[str, str]:
    """SHA-256 of post.yaml, every art file that exists and the automatic art-check record."""
    files = {"post.yaml": _sha256(post.dir / "post.yaml")}
    for s in post.slides:
        art = post.dir / s.art
        if art.is_file():
            files[s.art] = _sha256(art)
    checks = post.dir / ART_CHECKS
    if checks.is_file():
        files[ART_CHECKS] = _sha256(checks)
    return files


def read_approval(post_dir: Path) -> Approval | None:
    """Parse approved.json if present (None if missing or malformed)."""
    path = post_dir / "approved.json"
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return Approval(
            id=str(d["id"]),
            approved_at=str(d["approved_at"]),
            format=str(d["format"]),
            slides=int(d["slides"]),
            inputs={str(k): str(v) for k, v in dict(d["inputs"]).items()},
            gates=str(d.get("gates", "")),
        )
    except (ValueError, KeyError, TypeError):
        return None


def approval_problems(post: Post, approval: Approval) -> list[str]:
    """Why an approval no longer holds (empty when it is still valid)."""
    problems: list[str] = []
    _require(approval.id == post.id, problems, "approval belongs to another post")
    _require(approval.format == post.format, problems, "format changed since approval")
    _require(approval.slides == len(post.slides), problems, "slide count changed since approval")
    _require(not post.missing_art(), problems, "art missing")
    current = input_hashes(post) if not post.missing_art() else {}
    _require(current == approval.inputs, problems, "files changed since approval: run Build again")
    return problems


def write_approval(post: Post, gates_summary: str, now: datetime | None = None) -> Approval:
    """Record that ``post`` passed every gate with its current files."""
    if post.missing_art():
        raise ContentError("cannot approve a post without its art")
    approval = Approval(
        id=post.id,
        approved_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        format=post.format,
        slides=len(post.slides),
        inputs=input_hashes(post),
        gates=gates_summary,
    )
    data = {
        "id": approval.id,
        "approved_at": approval.approved_at,
        "format": approval.format,
        "slides": approval.slides,
        "inputs": approval.inputs,
        "gates": approval.gates,
    }
    (post.dir / "approved.json").write_text(
        json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return approval


def load_queue(page_dir: Path) -> tuple[list[QueuedPost], list[str]]:
    """Every post folder of a page; returns (posts that parse, problems of those that do not)."""
    root = page_dir / "queue"
    items: list[QueuedPost] = []
    errors: list[str] = []
    if not root.is_dir():
        return items, errors
    for post_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        post, problems = parse_post(post_dir)
        if post is None:
            errors += problems
            continue
        approval = read_approval(post_dir)
        item = QueuedPost(post, approval)
        if approval is not None:
            item.problems = approval_problems(post, approval)
        if post.hold:
            item.problems.append(f"on hold: {post.hold}")
        items.append(item)
    return items, errors


def set_hold(post_dir: Path, reason: str | None) -> None:
    """Put a post on hold (``reason``) or release it (``None``) by editing its ``post.yaml``.

    Only the top-level ``hold:`` line changes, so the approval of an approved post is kept for
    hold/release (``post.yaml`` is hashed without that line).
    """
    path = post_dir / "post.yaml"
    before = path.read_text(encoding="utf-8")
    kept = [ln for ln in before.splitlines() if not HOLD_LINE_RE.match(ln)]
    if reason is not None:
        kept.append(f"hold: {json.dumps(' '.join(reason.split())[:200] or 'on hold')}")
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    post, problems = parse_post(post_dir)
    if post is None:
        path.write_text(before, encoding="utf-8")
        raise ContentError("; ".join(problems))


def find_post(page_dir: Path, post_id: str) -> Post:
    """Load one post by id or raise ContentError with the reasons."""
    if not POST_ID_RE.match(post_id):
        raise ContentError(f"bad post id {post_id!r}")
    post, problems = parse_post(page_dir / "queue" / post_id)
    if post is None:
        raise ContentError("; ".join(problems) or f"no post {post_id}")
    return post


def publishable(items: list[QueuedPost], used: set[str]) -> list[QueuedPost]:
    """Approved, unchanged, not yet published posts in posting order."""
    ready = [i for i in items if i.publishable and i.post.id not in used]
    return sorted(
        ready,
        key=lambda i: (i.post.priority, i.approval.approved_at if i.approval else "", i.post.id),
    )


def next_for_slot(items: list[QueuedPost], used: set[str], fmt: str) -> QueuedPost | None:
    """The next publishable post of format ``fmt`` (``any`` takes whatever is first)."""
    for item in publishable(items, used):
        if fmt in ("any", item.post.format):
            return item
    return None


def style_paragraph(characters: dict[str, Any]) -> str:
    """The locked art style (``style.paragraph`` in characters.yaml) that starts every prompt."""
    style = characters.get("style", {})
    text = str(style.get("paragraph") or style.get("look") or "Painterly storybook illustration")
    return " ".join(text.split())


def art_prompt(post: Post, slide: SlideSpec, characters: dict[str, Any]) -> str:
    """A copy-paste prompt for the owner's image tool, built from the slide's brief."""
    brief = slide.brief
    ratio = "9:16 portrait (1080x1920)" if post.format == "reel" else "4:5 portrait (1080x1350)"
    lines = [
        style_paragraph(characters),
        f"Canvas: {ratio}.",
        f"Scene: {brief.get('scene', '')}",
        f"Feeling: {brief.get('emotion', '')}",
        f"Composition: {brief.get('composition', '')} Keep the {slide.position} of the picture "
        "calm and plain (soft sky, wall, mist, water or cream light) for text added later.",
    ]
    for name in brief.get("characters", []):
        info = characters.get("characters", {}).get(name)
        if info:
            lines.append(f"{info['name']}: {'; '.join(info['must'])}.")
    if any(c in ("rowan", "wren") for c in brief.get("characters", [])):
        lines.append("Normal rounded human ears — not pointed, not elf ears.")
    lines.append(NO_TEXT)
    return "\n".join(lines)
