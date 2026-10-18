"""Gates for story posts: every slide's text, art and file, plus the carousel as a whole."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from autopilot.config import Config
from autopilot.gates import GateReport
from autopilot.gates.media import check_image_file
from autopilot.gates.visual import check_visual
from autopilot.render.story import ComposedSlide, StoryStyle, safe_box


def check_slides(
    cfg: Config,
    style: StoryStyle,
    fmt: str,
    slides: list[ComposedSlide],
    paths: list[Path],
    cover_words: int,
    require_art: bool,
) -> GateReport:
    """Run the per-slide and carousel gates."""
    report = GateReport()
    story_gates = dataclasses.replace(
        cfg.gates,
        min_font_px=cfg.gates.story_min_font_px,
        min_contrast=cfg.gates.story_min_contrast,
    )
    safe = safe_box(cfg, style.size)
    for slide, path in zip(slides, paths, strict=True):
        n = slide.index
        for r in check_visual(slide.elements, [slide.background], safe, story_gates).results:
            report.add(f"slide{n}:{r.name}", r.passed, r.detail)
        for r in check_image_file(path, cfg).results:
            report.add(f"slide{n}:{r.name}", r.passed, r.detail)
        if require_art:
            report.add(
                f"slide{n}:art-supplied", not slide.placeholder, "placeholder art is never posted"
            )
        if not slide.placeholder:
            report.add(
                f"slide{n}:art-resolution",
                slide.upscale <= style.max_upscale,
                f"art enlarged x{slide.upscale:.2f}, maximum x{style.max_upscale:.2f}",
            )
    if fmt != "carousel":
        return report
    lo, hi = (
        int(v)
        for v in (cfg.section("story", "slides", "min"), cfg.section("story", "slides", "max"))
    )
    report.add("carousel:slide-count", lo <= len(slides) <= hi, f"{len(slides)} slides ({lo}-{hi})")
    report.add(
        "carousel:order",
        [s.index for s in slides] == list(range(1, len(slides) + 1))
        and [p.name for p in paths] == sorted(p.name for p in paths),
        "slides numbered 1..n in order",
    )
    body = {s.px for s in slides[1:]}
    report.add(
        "carousel:same-size",
        len(body) <= 1,
        f"text sizes {sorted(body)} px"
        if len(body) > 1
        else f"all slides {next(iter(body), 0)} px",
    )
    cover = slides[0].px if slides else 0
    report.add(
        "carousel:strong-cover",
        all(cover >= px for px in body)
        and cover_words <= int(cfg.section("story", "cover_max_words")),
        f"cover {cover} px, {cover_words} words",
    )
    sizes = {s.image.size for s in slides}
    report.add("carousel:same-shape", len(sizes) == 1, ", ".join(f"{w}x{h}" for w, h in sizes))
    return report
