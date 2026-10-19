"""Preview and approve queued posts ("Build <post-id>"), and render review pages for the owner.

A preview renders a post exactly as it would be published (placeholder art stands in for any art
not supplied yet) and writes an HTML page with the slides, caption, gate results and the art
prompts. Approval additionally requires the owner's real art and every gate passing; it writes
``approved.json`` with the SHA-256 of every input file.
"""

from __future__ import annotations

import html
import shutil
from dataclasses import dataclass
from pathlib import Path

from autopilot.config import Config, Page
from autopilot.content.queue import Post, art_prompt, load_queue, style_paragraph, write_approval
from autopilot.errors import ContentError
from autopilot.gates import GateReport
from autopilot.pipeline import Caption, Composed, StoryBuilder
from autopilot.rehearsal.fakes import LocalHosting


def media_tag(file: str, fmt: str) -> str:
    """An <img> or <video> element for a sample file."""
    src = html.escape(file, quote=True)
    if fmt == "reel" or file.endswith(".mp4"):
        return f'<video src="{src}" controls preload="metadata"></video>'
    return f'<img src="{src}" loading="lazy">'


@dataclass
class Preview:
    """One rendered post."""

    post: Post
    composed: Composed
    caption: Caption
    report: GateReport
    files: list[str]  # relative to the review page

    @property
    def passed(self) -> bool:
        """True when every gate passed."""
        return self.report.passed and bool(self.composed.paths)


def render_post(builder: StoryBuilder, post: Post, out: Path, allow_placeholder: bool) -> Preview:
    """Compose ``post`` into ``out`` and collect its gate results."""
    work = out / post.id
    if work.exists():
        shutil.rmtree(work)
    composed, caption, text_report = builder.compose(
        post, work, f"preview-{post.id}", allow_placeholder
    )
    report = GateReport()
    report.extend(text_report)
    report.extend(composed.report)
    files = [f"{post.id}/{p.name}" for p in composed.paths]
    return Preview(post, composed, caption, report, files)


def _card(p: Preview, characters: dict[str, object]) -> str:
    esc = html.escape
    media = "".join(media_tag(f, p.post.format) for f in p.files) or "<p>(not rendered)</p>"
    status = "PASS" if p.passed else "FAIL"
    note = " · placeholder art" if p.composed.placeholder else ""
    if p.composed.slides:
        sizes = [s.px for s in p.composed.slides]
        note += f" · text {min(sizes)} px" + (
            f" (first slide {sizes[0]} px)" if sizes[0] != min(sizes) else ""
        )
    failures = "".join(f"<li>{esc(r.name)}: {esc(r.detail)}</li>" for r in p.report.failures)
    prompts = "".join(
        f"<details><summary>Art prompt for {esc(s.art)}</summary><pre>"
        f"{esc(art_prompt(p.post, s, characters))}</pre></details>"
        for s in p.post.slides
    )
    texts = "".join(f"<li>{esc(t)}</li>" for t in p.post.plain_texts)
    return (
        f'<section class="post {status.lower()}"><h2>{esc(p.post.id)} '
        f"<small>{esc(p.post.format)} · {esc(p.post.kind)} · {esc(p.post.topic)}{note} · "
        f"<b>{status}</b> ({esc(p.report.summary().split(';')[0])})</small></h2>"
        f'<div class="strip">{media}</div><ol class="texts">{texts}</ol>'
        f'<pre class="cap">{esc(p.caption.text)}</pre>'
        f"{'<ul class=fail>' + failures + '</ul>' if failures else ''}{prompts}</section>"
    )


def write_review_page(
    previews: list[Preview], builder: StoryBuilder, out: Path, title: str
) -> Path:
    """Write ``index.html`` for a set of previews."""
    cards = "".join(_card(p, builder.characters) for p in previews)
    ok = sum(p.passed for p in previews)
    page = f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:15px system-ui;margin:20px;background:#f6f1e7;color:#2b1d14}}
.post{{background:#fff;border-radius:10px;padding:14px;margin:0 0 22px;box-shadow:0 1px 3px #0002}}
.post.fail{{outline:3px solid #b3261e}} h2 small{{font-weight:400;color:#6b5a4a;font-size:13px}}
.strip{{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px}}
.strip img,.strip video{{height:420px;border-radius:6px;flex:none}}
pre{{white-space:pre-wrap;font:13px system-ui;background:#faf6ee;padding:8px;border-radius:6px}}
.fail li{{color:#b3261e}} .texts{{color:#4a3a2c;font-size:14px}}
.style{{background:#fff8e6;border:2px solid #c9a227}}</style>
<h1>{html.escape(title)}</h1><p>{ok} of {len(previews)} posts pass every gate.
Slides use placeholder art until real art is supplied.</p>
<section class="post style"><h2>Art style (locked) <small>paste this first in a new image chat;
every art prompt below already starts with it</small></h2>
<pre>{html.escape(style_paragraph(builder.characters))}</pre></section>{cards}"""
    out.mkdir(parents=True, exist_ok=True)
    index = out / "index.html"
    index.write_text(page, encoding="utf-8")
    return index


def preview_queue(
    cfg: Config, page: Page, out: Path, post_ids: list[str] | None = None
) -> tuple[Path, int]:
    """Render every (or the listed) queued post with placeholders; returns (index, failures)."""
    builder = StoryBuilder(cfg, page, out, LocalHosting(out / "hosted"))
    items, errors = load_queue(page.dir)
    for e in errors:
        print(f"INVALID {e}")
    posts = [i.post for i in items if not post_ids or i.post.id in post_ids]
    previews = []
    for post in posts:
        p = render_post(builder, post, out, allow_placeholder=True)
        print(f"{post.id:40s} {post.format:8s} {p.report.summary()}", flush=True)
        previews.append(p)
    index = write_review_page(previews, builder, out, f"{page.title} — queue preview")
    return index, sum(not p.passed for p in previews) + len(errors)


def build_and_approve(cfg: Config, page: Page, post_id: str) -> tuple[Preview, Path]:
    """``Build <post-id>``: compose with the real art, gate, and approve when everything passes."""
    builder = StoryBuilder(
        cfg, page, page.dir / "queue" / post_id / "preview", LocalHosting(Path())
    )
    items, _ = load_queue(page.dir)
    item = next((i for i in items if i.post.id == post_id), None)
    if item is None:
        raise ContentError(f"no valid post {post_id} in the queue")
    post = item.post
    missing = post.missing_art()
    out = post.dir / "preview"
    preview = render_post(builder, post, out, allow_placeholder=bool(missing))
    index = write_review_page([preview], builder, out, f"Build {post_id}")
    if missing:
        raise ContentError(
            f"art not supplied yet: {', '.join(missing)} (preview uses placeholders)"
        )
    if not preview.passed:
        raise ContentError(f"gates failed: {preview.report.summary()}")
    write_approval(post, preview.report.summary())
    return preview, index
