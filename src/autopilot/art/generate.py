"""Generate checked, text-free art for a queued post (``generate-art``).

For every slide without art: build the prompt from the slide's brief and the locked style,
attach the character reference sheets (and the post's first finished slide, for a matching
look), draw at 4:5, crop and resize to the post size, and let a vision model check the result.
Only a picture that passes the check is saved as ``art-NN.png`` and recorded with its SHA-256 in
``art-check.json``. A rejected picture is kept in ``preview/rejected/`` (git-ignored) with the
reasons, and the next try is told what was wrong. After ``max_regenerations`` further failures
the post is skipped (no more slides are drawn) and an alert is raised.

Only the character sheets named in ``characters.yaml`` (``characters.<name>.reference``) are
ever read from ``references/`` and sent to the image API; nothing else in that folder is touched.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autopilot.art.budget import Ledger
from autopilot.art.clients import (
    CharacterSpec,
    Checker,
    CheckSpec,
    ImageClient,
    NoImageError,
    RefImage,
)
from autopilot.config import ROOT
from autopilot.content.queue import ART_CHECKS, Post, SlideSpec, art_prompt, read_approval
from autopilot.errors import ArtError, BudgetError
from autopilot.ops import alerts

CHECKS_FILE = ART_CHECKS
NAMED = ("rowan", "wren")
STANDARD_REASONS = (
    "pointed or elf ears",
    "visible text or letters",
    "characters do not match the reference sheets",
)


# ----- references ------------------------------------------------------------------------------
def references_dir(characters: dict[str, Any]) -> Path:
    """Where the owner's reference sheets live (``AP_REFERENCES_DIR`` overrides)."""
    override = os.environ.get("AP_REFERENCES_DIR", "").strip()
    return Path(override) if override else ROOT / str(characters.get("references_dir", ""))


def shrink(data: bytes, max_px: int) -> bytes:
    """A JPEG no larger than ``max_px`` on its longest side (smaller uploads, same look)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
    rgb.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class ReferenceLibrary:
    """The two character sheets, loaded only when a slide needs them."""

    def __init__(self, characters: dict[str, Any], folder: Path, max_px: int) -> None:
        self.characters, self.folder, self.max_px = characters, folder, max_px
        self._cache: dict[str, RefImage] = {}

    def sheet(self, name: str) -> RefImage:
        """The reference sheet of character ``name`` (ArtError when it is not on this PC)."""
        if name in self._cache:
            return self._cache[name]
        info = self.characters.get("characters", {}).get(name) or {}
        file = str(info.get("reference", ""))
        path = self.folder / Path(file).name if file else None
        if path is None or not path.is_file():
            raise ArtError(
                f"the reference sheet for {name} is not on this PC: run generate-art from the "
                "project folder that holds references/ (or set AP_REFERENCES_DIR)"
            )
        label = f"{info['name']} character sheet"
        ref = RefImage(label, "image/jpeg", shrink(path.read_bytes(), self.max_px))
        self._cache[name] = ref
        return ref


class PlaceholderReferences(ReferenceLibrary):
    """Rehearsals and tests: a plain stand-in picture instead of the private sheets."""

    def sheet(self, name: str) -> RefImage:
        """A small grey picture labelled like the real sheet."""
        from PIL import Image

        info = self.characters.get("characters", {}).get(name) or {"name": name}
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (128, 128, 128)).save(buf, "JPEG")
        return RefImage(f"{info['name']} character sheet", "image/jpeg", buf.getvalue())


# ----- prompts, sizes, records ------------------------------------------------------------------
def generation_prompt(
    post: Post,
    slide: SlideSpec,
    characters: dict[str, Any],
    names: list[str],
    earlier: bool,
    rejected: list[str],
) -> str:
    """The art prompt of the review page plus instructions for the attached references."""
    lines = [art_prompt(post, slide, characters)]
    if names:
        lines.append(
            f"Use the attached character sheet{'s' if len(names) > 1 else ''} for "
            + " and ".join(names)
            + ": same face, skin tone, hair and outfit as on the sheet. Draw one single scene, "
            "not a character sheet, with no labels or captions."
        )
    if earlier:
        lines.append(
            "The attached earlier slide of this story shows the painting style, light and palette "
            "to match; do not copy its scene."
        )
    if rejected:
        lines.append(
            "A previous attempt was rejected for: " + "; ".join(rejected) + ". Avoid this."
        )
    return "\n".join(lines)


def fit_to_canvas(data: bytes, size: tuple[int, int]) -> bytes:
    """Centre-crop to the target ratio and resize; returns PNG bytes."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            rgb = img.convert("RGB")
    except (OSError, ValueError):
        raise NoImageError("the image model returned something that is not a picture") from None
    w, h = rgb.size
    tw, th = size
    if w * th > h * tw:  # too wide: trim the sides
        nw = round(h * tw / th)
        rgb = rgb.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:  # too tall: trim top and bottom equally
        nh = round(w * th / tw)
        rgb = rgb.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    rgb = rgb.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def sha256(data: bytes) -> str:
    """Hex SHA-256."""
    return hashlib.sha256(data).hexdigest()


def read_checks(post_dir: Path) -> dict[str, dict[str, Any]]:
    """``art-check.json`` entries by art file name (empty when there is none)."""
    try:
        data = json.loads((post_dir / CHECKS_FILE).read_text(encoding="utf-8"))
        files = data.get("files", {}) if isinstance(data, dict) else {}
        return {str(k): dict(v) for k, v in files.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def write_checks(post_dir: Path, files: dict[str, dict[str, Any]]) -> None:
    """Rewrite ``art-check.json``."""
    text = json.dumps({"files": dict(sorted(files.items()))}, indent=1, sort_keys=True)
    (post_dir / CHECKS_FILE).write_text(text + "\n", encoding="utf-8")


def check_problems(post: Post) -> list[str]:
    """Generated art that is not exactly the picture that passed its automatic check."""
    problems = []
    for name, entry in read_checks(post.dir).items():
        path = post.dir / name
        if not path.is_file():
            continue
        if entry.get("result") != "pass" or entry.get("sha256") != sha256(path.read_bytes()):
            problems.append(
                f"{name} is not the picture that passed its automatic check: regenerate it "
                f"(generate-art --slides {name[4:6].lstrip('0')}) or remove its entry from "
                f"{CHECKS_FILE} if you drew it yourself"
            )
    return problems


# ----- the generator ---------------------------------------------------------------------------
@dataclass
class SlideResult:
    """What happened to one slide."""

    art: str
    status: str  # generated | failed
    attempts: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class PostResult:
    """What happened to one post."""

    post_id: str
    status: str  # done | nothing-to-do | skipped | failed | budget
    message: str = ""
    slides: list[SlideResult] = field(default_factory=list)
    alerts: alerts.AlertPlan = field(default_factory=alerts.AlertPlan)


class ArtGenerator:
    """Draws, checks and saves art for queued posts."""

    def __init__(
        self,
        page_title: str,
        characters: dict[str, Any],
        painter: ImageClient,
        checker: Checker,
        ledger: Ledger,
        references: ReferenceLibrary,
        size: tuple[int, int],
        max_regenerations: int,
        log: Callable[[str], None] = print,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.page_title, self.characters = page_title, characters
        self.painter, self.checker, self.ledger, self.references = (
            painter,
            checker,
            ledger,
            references,
        )
        self.size, self.max_regenerations, self.log, self._now = (
            size,
            max_regenerations,
            log,
            now,
        )

    def _spec(self, slide: SlideSpec) -> CheckSpec:
        chars = []
        for name in slide.brief.get("characters", []):
            info = self.characters.get("characters", {}).get(name)
            if name in NAMED and info:
                chars.append(CharacterSpec(str(info["name"]), tuple(str(m) for m in info["must"])))
        return CheckSpec(tuple(chars), " ".join(str(slide.brief.get("scene", "")).split()))

    def slides_to_draw(self, post: Post, redo: set[int]) -> list[SlideSpec]:
        """Slides without art, plus any the owner asked to redo."""
        return [s for s in post.slides if s.index in redo or not (post.dir / s.art).is_file()]

    def generate(self, post: Post, redo: set[int] | None = None) -> PostResult:
        """Generate every missing (or redone) slide of ``post``; never raises for one slide."""
        redo = redo or set()
        result = PostResult(post.id, "done")
        key = f"art-failed-{alerts.page_slug(self.page_title)}-{post.id}"
        if post.hold:
            return PostResult(post.id, "skipped", f"on hold: {post.hold}")
        if read_approval(post.dir) is not None and not redo:
            return PostResult(post.id, "nothing-to-do", "already approved")
        todo = self.slides_to_draw(post, redo)
        if not todo:
            return PostResult(post.id, "nothing-to-do", "every slide already has art")
        checks = read_checks(post.dir)
        for slide in todo:
            try:
                outcome = self._slide(post, slide, checks)
            except BudgetError as exc:
                result.status, result.message = "budget", str(exc)
                result.alerts.add(alerts.art_budget_reached(self.page_title, self.ledger.cap_gbp))
                return result
            except ArtError as exc:
                outcome = SlideResult(slide.art, "failed", 0, [str(exc)])
            result.slides.append(outcome)
            if outcome.status == "failed":
                result.status = "failed"
                result.message = f"slide {slide.index}: {'; '.join(outcome.reasons)}"
                fixed = [r for r in STANDARD_REASONS if r in outcome.reasons]
                reason = ", ".join(fixed) if fixed else "the image service failed"
                result.alerts.add(alerts.art_failed(self.page_title, post.id, slide.index, reason))
                return result  # skip the post: no more money spent on it
        result.alerts.resolved.append(key)
        return result

    def _refs(self, post: Post, slide: SlideSpec) -> tuple[list[RefImage], list[str], bool]:
        names: list[str] = []
        refs: list[RefImage] = []
        for name in slide.brief.get("characters", []):
            if name in NAMED and name not in names:
                refs.append(self.references.sheet(name))
                names.append(str(self.characters["characters"][name]["name"]))
        earlier = next(
            (s for s in post.slides if s.index != slide.index and (post.dir / s.art).is_file()),
            None,
        )
        style: list[RefImage] = []
        if earlier is not None:
            style = [
                RefImage(
                    "earlier slide of this story (style only)",
                    "image/jpeg",
                    shrink((post.dir / earlier.art).read_bytes(), 1024),
                )
            ]
        return refs + style, names, bool(style)

    def _slide(
        self, post: Post, slide: SlideSpec, checks: dict[str, dict[str, Any]]
    ) -> SlideResult:
        refs, names, earlier = self._refs(post, slide)
        char_refs = refs[: len(names)]
        spec = self._spec(slide)
        rejected: list[str] = []
        all_reasons: list[str] = []
        tries = 1 + self.max_regenerations
        for attempt in range(1, tries + 1):
            self.ledger.ensure(self.painter.usd_per_image + self.checker.usd_per_check)
            prompt = generation_prompt(post, slide, self.characters, names, earlier, rejected)
            self.log(f"  {post.id} slide {slide.index}: drawing (try {attempt} of {tries})")
            try:
                drawn = self.painter.generate(prompt, refs)
            except NoImageError as exc:
                rejected = [str(exc)]
                all_reasons.append(str(exc))
                continue
            self.ledger.add(self.painter.usd_per_image, "images")
            try:
                png = fit_to_canvas(drawn.data, self.size)
            except NoImageError as exc:
                rejected = [str(exc)]
                all_reasons.append(str(exc))
                continue
            verdict = self.checker.check(png, char_refs, spec)
            self.ledger.add(self.checker.usd_per_check, "checks")
            if verdict.passed:
                self._save(post, slide, png, attempt, checks)
                self.log(f"  {post.id} slide {slide.index}: passed the check -> {slide.art}")
                return SlideResult(slide.art, "generated", attempt)
            rejected = verdict.reasons
            all_reasons += [r for r in verdict.reasons if r not in all_reasons]
            self._keep_rejected(post, slide, png, attempt, verdict.reasons)
            self.log(f"  {post.id} slide {slide.index}: rejected ({'; '.join(verdict.reasons)})")
        return SlideResult(slide.art, "failed", tries, all_reasons)

    def _save(
        self,
        post: Post,
        slide: SlideSpec,
        png: bytes,
        attempts: int,
        checks: dict[str, dict[str, Any]],
    ) -> None:
        path = post.dir / slide.art
        if path.suffix.lower() != ".png":
            raise ArtError(f"{slide.art}: generated art is saved as PNG; name the slide art-NN.png")
        path.write_bytes(png)
        checks[slide.art] = {
            "sha256": sha256(png),
            "result": "pass",
            "generated_by": self.painter.name,
            "checked_by": self.checker.name,
            "checked_at": self._now().isoformat(timespec="seconds"),
            "attempts": attempts,
        }
        write_checks(post.dir, checks)

    def _keep_rejected(
        self, post: Post, slide: SlideSpec, png: bytes, attempt: int, reasons: list[str]
    ) -> None:
        folder = post.dir / "preview" / "rejected"  # git-ignored
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{Path(slide.art).stem}-try{attempt}"
        (folder / f"{stem}.png").write_bytes(png)
        (folder / f"{stem}.txt").write_text("\n".join(reasons) + "\n", encoding="utf-8")
