"""Quote/fact images: 1080x1350 JPEG (4:5), sRGB."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw

from autopilot.config import Config
from autopilot.render.background import render_background
from autopilot.render.layout import Box, TextElement, fit_quote, place_watermark
from autopilot.render.theme import FontSpec, Template

WATERMARK_FONT = FontSpec("inter", 500, False)


@dataclass
class RenderedImage:
    """A rendered image plus what the gates need to check it."""

    path: Path
    background: Image.Image
    elements: list[TextElement]
    template: str


def _srgb_profile() -> bytes:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    return bytes(profile.tobytes())


def draw_elements(
    img: Image.Image, elements: list[TextElement], alphas: dict[int, float] | None = None
) -> Image.Image:
    """Draw text elements onto a copy of ``img``.

    ``alphas`` optionally maps a body/attribution word index (in reading order across elements,
    watermark excluded) to an opacity in [0, 1]; missing words are fully visible.
    """
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    idx = 0
    for el in elements:
        font = el.pil_font()
        for word in el.words:
            alpha = el.opacity
            if el.role != "watermark":
                if alphas is not None:
                    alpha *= alphas.get(idx, 1.0)
                idx += 1
            if alpha <= 0.0:
                continue
            draw.text(
                (word.x, word.y),
                word.text,
                font=font,
                fill=(*el.color, round(255 * alpha)),
                anchor="ls",
            )
    return Image.alpha_composite(base, overlay).convert("RGB")


def text_box(cfg: Config, size: tuple[int, int], reel: bool) -> tuple[Box, Box]:
    """Return (safe box, body text box) in pixels for an image or reel frame."""
    w, h = size
    sz = cfg.safe_zones
    if reel:
        safe = (
            round(w * sz.reel_sides),
            round(h * sz.reel_top),
            round(w * (1 - sz.reel_sides)),
            round(h * (1 - sz.reel_bottom)),
        )
    else:
        mx, my = (1 - sz.image_width) / 2, (1 - sz.image_height) / 2
        safe = (round(w * mx), round(h * my), round(w * (1 - mx)), round(h * (1 - my)))
    inset = float(cfg.section("render", "text_inset"))
    wm_px = int(cfg.section("render", "watermark_px"))
    body = (
        round(safe[0] + w * inset),
        round(safe[1] + h * inset / 2),
        round(safe[2] - w * inset),
        round(safe[3] - wm_px * 2.6),
    )
    return safe, body


def layout_for(
    cfg: Config,
    template: Template,
    text: str,
    author: str | None,
    handle: str,
    size: tuple[int, int],
    reel: bool,
    opacity: float,
) -> list[TextElement]:
    """Fit quote, attribution and watermark for a frame of ``size``."""
    safe, body_box = text_box(cfg, size, reel)
    fp = cfg.section("render", "font_px")
    elements = fit_quote(
        text,
        author,
        body_box,
        template.font,
        template.attribution_font,
        (template.text_color, template.accent_color),
        (int(fp["max"]), int(fp["min"]), int(fp["step"])),
        float(cfg.section("render", "line_spacing")),
    )
    wm = place_watermark(
        handle,
        WATERMARK_FONT,
        int(cfg.section("render", "watermark_px")),
        template.accent_color,
        (safe[0] + safe[2]) / 2,
        safe[3] - 4,
        opacity,
    )
    return [*elements, wm]


def render_image(
    cfg: Config,
    template: Template,
    text: str,
    author: str | None,
    handle: str,
    seed: int,
    out: Path,
    watermark_opacity: float,
) -> RenderedImage:
    """Render and save the JPEG; returns the pieces the gates inspect."""
    w, h = (int(v) for v in cfg.section("render", "image_size"))
    bg = render_background(template, w, h, seed)
    elements = layout_for(cfg, template, text, author, handle, (w, h), False, watermark_opacity)
    img = draw_elements(bg, elements)
    save_jpeg(img, out, int(cfg.section("render", "jpeg_quality")))
    return RenderedImage(out, bg, elements, template.name)


def save_jpeg(img: Image.Image, out: Path, quality: int) -> None:
    """Save a baseline sRGB JPEG (the only image format Instagram's API accepts)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.convert("RGB").save(
        buf,
        "JPEG",
        quality=quality,
        optimize=True,
        progressive=False,
        subsampling="4:2:0",
        icc_profile=_srgb_profile(),
    )
    out.write_bytes(buf.getvalue())
