"""One sample per template (and per voice / audio mode) for the owner's look-and-voice review."""

from __future__ import annotations

import html
import json
import random
import shutil
from pathlib import Path
from typing import Any

from autopilot.config import Config, Page
from autopilot.pipeline import Builder
from autopilot.rehearsal.fakes import LocalHosting

MODES = ("voice+music", "music", "voice")


def media_tag(file: str, fmt: str) -> str:
    """An <img> or <video> element for a sample file."""
    src = html.escape(file, quote=True)
    if fmt == "image":
        return f'<img src="{src}" loading="lazy">'
    return f'<video src="{src}" controls preload="metadata"></video>'


def render_samples(cfg: Config, page: Page, out: Path, reels: bool = True) -> int:
    """Render samples into ``out`` with an index.html; returns a process exit code."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    builder = Builder(cfg, page, out, LocalHosting(out / "hosted"))
    rng = random.Random(7)
    entries = rng.sample(builder.entries, len(builder.theme.templates) * 2)
    voices = [str(v) for v in cfg.section("audio", "voices")]
    rows, failed = [], 0
    voiced = 0  # rotate voices across narrated samples only
    for i, tpl in enumerate(builder.theme.templates):
        jobs: list[tuple[str, dict[str, Any], str, str | None]] = [
            ("image", entries[2 * i], "", None)
        ]
        if reels:
            mode = MODES[i % len(MODES)]
            voice = voices[voiced % len(voices)] if "voice" in mode else None
            voiced += voice is not None
            jobs.append(
                (
                    "reel",
                    entries[2 * i + 1],
                    mode,
                    voice,
                )
            )
        for fmt, entry, mode, voice in jobs:
            key = f"{page.name}-2000-01-01-{tpl.name}-{fmt}"
            if fmt == "image":
                path, report = builder._image(entry, tpl.name, key)
            else:
                path, report = builder._reel(entry, tpl.name, key, mode, voice, False)
            failed += not report.passed
            rows.append(
                {
                    "template": tpl.name,
                    "format": fmt,
                    "mode": mode,
                    "voice": voice or "",
                    "file": path.name,
                    "gates": report.summary(),
                    "text": entry["text"],
                }
            )
            print(
                f"{tpl.name:10s} {fmt:5s} {mode:12s} {voice or '':10s} {report.summary()}",
                flush=True,
            )
    shutil.rmtree(out / "hosted", ignore_errors=True)
    (out / "samples.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    cards = "".join(
        f'<div class="c">{media_tag(r["file"], r["format"])}<b>{html.escape(r["template"])}</b> '
        f"{r['format']} {r['mode']} {r['voice']}<br><small>{html.escape(r['gates'])}</small></div>"
        for r in rows
    )
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Samples</title><style>'
        "body{font:14px system-ui;margin:20px}"
        ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}"
        "img,video{width:100%;border-radius:6px}small{color:#666}</style>"
        f'<h1>{page.title} samples by template</h1><div class="g">{cards}</div>',
        encoding="utf-8",
    )
    return 1 if failed else 0
