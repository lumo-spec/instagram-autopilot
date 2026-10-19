"""``Export <post-id>``: a folder the owner can post or schedule by hand (Meta Business Suite).

Only an approved post whose files are unchanged since ``Build`` can be exported. The slides are
composed again from the approved art and every gate runs again, so the exported files are exactly
what Build checked. The folder holds the finished media in order (``01.jpg``, ``02.jpg``, ... or
``reel.mp4``), ``caption.txt`` (caption, disclosure lines and hashtags, ready to paste) and
``NOTES.txt`` (posting order, AI label advice, suggested time). Nothing is uploaded anywhere.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autopilot.config import Config, Page
from autopilot.content.queue import load_queue
from autopilot.errors import ContentError
from autopilot.pipeline import StoryBuilder
from autopilot.rehearsal.fakes import LocalHosting
from autopilot.rehearsal.preview import render_post

NOTES = "NOTES.txt"
CAPTION = "caption.txt"
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
FORMAT_NAMES = {"carousel": "Carousel", "image": "Single image", "reel": "Reel"}


@dataclass(frozen=True)
class Export:
    """What ``export_post`` wrote."""

    folder: Path
    media: list[str]  # file names in posting order
    ai_label: list[str]  # why the AI label is needed (empty: not needed)


def suggested_times(cfg: Config, page: Page, fmt: str) -> list[str]:
    """The page's posting slots for ``fmt``, e.g. ``Mon, Wed, Fri at 19:00``."""
    return [
        f"{', '.join(DAY_NAMES[d] for d in s.days)} at {s.time}"
        for s in page.slots
        if s.format == fmt
    ]


def notes_text(
    page: Page,
    post_id: str,
    fmt: str,
    media: list[str],
    ai_label: list[str],
    times: list[str],
    approved_at: str,
    gates: str,
    tz: str,
    now: datetime,
) -> str:
    """Plain-English instructions that go with the exported files."""
    count = f"{len(media)} slides" if fmt == "carousel" else "1 file"
    lines = [
        f"{page.title} ({page.handle}) - {post_id}",
        f"{FORMAT_NAMES.get(fmt, fmt)}, {count}. Exported {now:%Y-%m-%d %H:%M} UTC "
        f"from the build approved at {approved_at}.",
        "",
        "FILES - upload them in this order:",
    ]
    for n, name in enumerate(media, start=1):
        role = " (the cover - first slide)" if fmt == "carousel" and n == 1 else ""
        lines.append(f"  {name}{role}")
    lines += [
        f"  {CAPTION} - paste all of it as the caption (the hashtags are already at the end).",
        "",
    ]
    if ai_label:
        lines += [
            "AI LABEL: YES - switch it on.",
            f"  Why: {'; '.join(ai_label)}.",
            "  Switch on the AI label setting before you schedule"
            ' (Instagram shows it as "AI info").',
            "  Its name and place differ between app versions; see the README section",
            '  "How to schedule in Meta Business Suite". Instagram may also add it by itself.',
        ]
    else:
        lines += ["AI LABEL: not needed (no AI-generated art or voice in this post)."]
    lines.append("")
    if times:
        lines.append(f"SUGGESTED TIME ({tz}): " + " / ".join(times) + ".")
    lines += [
        f"CHECKS: every quality check passed again at export ({gates}).",
        "",
        "Post or schedule it once only. If you change the text or art, run Build and Export again.",
    ]
    return "\n".join(lines) + "\n"


def export_post(
    cfg: Config, page: Page, post_id: str, out_root: Path, now: datetime | None = None
) -> Export:
    """Write ``out_root/<post-id>/`` for an approved, unchanged post (re-gated)."""
    items, _ = load_queue(page.dir)
    item = next((i for i in items if i.post.id == post_id), None)
    if item is None:
        raise ContentError(f"no valid post {post_id} in the queue")
    if item.approval is None:
        raise ContentError(f"{post_id} is not approved yet: run Build first")
    if item.problems:
        raise ContentError(f"{post_id} cannot be exported: {'; '.join(item.problems)}")
    post = item.post
    folder = out_root / post_id
    if folder.exists() and any(folder.iterdir()) and not (folder / NOTES).is_file():
        raise ContentError(f"{folder} exists and is not an earlier export; not overwriting it")

    staging = out_root / f".{post_id}.partial"
    if staging.exists():
        shutil.rmtree(staging)
    render = staging / "_render"
    builder = StoryBuilder(cfg, page, render, LocalHosting(Path()))
    preview = render_post(builder, post, render, allow_placeholder=False)
    if not preview.passed or preview.composed.placeholder:
        shutil.rmtree(staging)
        raise ContentError(f"{post_id} failed its quality gates: {preview.report.summary()}")

    media = []
    for n, path in enumerate(preview.composed.paths, start=1):
        name = "reel.mp4" if post.format == "reel" else f"{n:02d}{path.suffix}"
        shutil.copyfile(path, staging / name)
        media.append(name)
    shutil.rmtree(render)
    ai_label = builder.ai_label_reasons(post, preview.composed)
    (staging / CAPTION).write_text(preview.caption.text + "\n", encoding="utf-8")
    (staging / NOTES).write_text(
        notes_text(
            page,
            post_id,
            post.format,
            media,
            ai_label,
            suggested_times(cfg, page, post.format),
            item.approval.approved_at,
            preview.report.summary(),
            cfg.timezone.key,
            now or datetime.now(UTC),
        ),
        encoding="utf-8",
    )
    if folder.exists():
        shutil.rmtree(folder)
    staging.rename(folder)
    return Export(folder, media, ai_label)
