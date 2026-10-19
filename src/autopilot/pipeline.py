"""Build step for story pages: next approved post -> compose -> every gate -> host -> manifest.

Runs without secrets. Only owner-approved posts whose files are unchanged since approval are
used; if none fits the slot, the slot is skipped (and an alert raised) — never filler.
Reels keep the narration fallbacks: another configured voice -> music-only reel -> skip.
Carousels and images have no format fallback: what the owner approved is posted, or nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import yaml
from PIL import Image

from autopilot.audio.mix import mix
from autopilot.audio.music import licence_problems, licensed_track, make_music, procedural
from autopilot.audio.voice import Narrator, VoiceTrack, load_pronunciations
from autopilot.config import Config, Page
from autopilot.content.captions import HASHTAG_RE
from autopilot.content.queue import (
    Approval,
    Post,
    QueuedPost,
    load_queue,
    next_for_slot,
    publishable,
    strip_markup,
    structure_problems,
)
from autopilot.errors import AutopilotError, ContentError
from autopilot.gates import GateReport
from autopilot.gates.media import check_image_file, check_reel
from autopilot.gates.spelling import load_allowed, unknown_words
from autopilot.gates.story import check_slides
from autopilot.gates.visual import check_visual
from autopilot.manifest import Manifest, media_names
from autopilot.render.image import layout_for, save_jpeg, text_box
from autopilot.render.layout import TextElement
from autopilot.render.reel import reel_duration, render_reel
from autopilot.render.story import (
    ComposedSlide,
    StoryStyle,
    compose_slide,
    load_art,
    max_fit_px,
    open_art,
    panel_mask,
    placeholder_art,
    story_style,
    story_text_box,
)
from autopilot.render.story_reel import (
    ReelSlide,
    SlideTiming,
    build_slide,
    plan_story_timing,
    reel_settings,
    render_story_reel,
    text_sizes,
)
from autopilot.render.theme import Template, load_theme
from autopilot.state import PageState, used_post_ids


class Hosting(Protocol):
    """Puts a media file somewhere Instagram can fetch it."""

    def host(self, page: str, name: str, path: Path) -> str: ...
    def remove(self, page: str, name: str) -> None: ...


@dataclass
class Request:
    """What to build for a slot."""

    slot_key: str
    local_date: date
    format: str  # carousel | image | reel | any
    post_id: str = ""  # on-demand: this approved post instead of the next one
    inject: frozenset[str] = frozenset()  # rehearsal-only simulated failures


@dataclass(frozen=True)
class Caption:
    """A finished caption and the hashtags it uses."""

    text: str
    hashtags: tuple[str, ...]


@dataclass
class Composed:
    """Rendered media for one post plus its gate report."""

    paths: list[Path]
    report: GateReport
    placeholder: bool
    slides: list[ComposedSlide] = field(default_factory=list)
    audio_mode: str = ""
    voice: str = ""
    fallback: str = ""
    cover: Path | None = None  # story reels: the 9:16 cover image
    music: str = ""  # story reels: "procedural" or the licensed track's file name
    seconds: float = 0.0  # story reels: length


@dataclass
class BuildResult:
    """Outcome of a build."""

    manifest: Manifest | None
    media_paths: list[Path]
    post: Post | None
    gates: list[tuple[str, GateReport]] = field(default_factory=list)  # (attempt, report)
    fallback: str = ""
    skipped_reason: str = ""
    queue_left: int = 0  # publishable posts left after this one
    changed: list[str] = field(default_factory=list)  # approved posts whose files changed


def seed_int(key: str) -> int:
    """Stable integer seed from a key."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")


class StoryBuilder:
    """Composes and gates posts for one story page."""

    def __init__(
        self, cfg: Config, page: Page, out_dir: Path, hosting: Hosting, rehearsal: bool = False
    ) -> None:
        if not page.is_story:
            raise ContentError(f"{page.name} is not a story page")
        self.cfg, self.page, self.out_dir, self.hosting = cfg, page, out_dir, hosting
        self.rehearsal = rehearsal
        self.style: StoryStyle = story_style(cfg)
        self.theme = load_theme(page.dir / "theme.yaml")
        self.allowed = load_allowed(page.dir / "spellcheck-words.txt")
        kit_path = page.dir / "captions.json"
        kit = json.loads(kit_path.read_text(encoding="utf-8")) if kit_path.is_file() else {}
        self.banned = frozenset(kit.get("banned_hashtags", []))
        chars = page.dir / "characters.yaml"
        self.characters: dict[str, Any] = (
            yaml.safe_load(chars.read_text(encoding="utf-8")) if chars.is_file() else {}
        )
        self._narrator: Narrator | None = None

    @property
    def narrator(self) -> Narrator:
        """Load the TTS model on first use only."""
        if self._narrator is None:
            self._narrator = Narrator(
                self.cfg, load_pronunciations(self.page.dir / "pronunciations.yaml")
            )
        return self._narrator

    # ----- queue --------------------------------------------------------------------------
    def queue(self) -> list[QueuedPost]:
        """Posts in the queue. In a rehearsal, valid drafts count as approved."""
        items, _ = load_queue(self.page.dir)
        if self.rehearsal:
            for item in items:
                if item.approval is None:
                    item.approval = _rehearsal_approval(item.post)
                    item.problems = []
        return items

    # ----- text ---------------------------------------------------------------------------
    def caption(self, post: Post, narrated: bool) -> Caption:
        """Caption = the post's own words, disclosure lines, then its hashtags."""
        disc = self.cfg.section("ai_disclosure")
        parts = [post.caption]
        lines = []
        has_art = post.format != "reel" or not post.missing_art()
        if self.page.ai_generated_art and has_art and disc.get("art_caption"):
            lines.append(str(disc["art_caption_text"]))
        if narrated and disc.get("caption"):
            lines.append(str(disc["caption_text"]))
        if lines:
            parts.append("\n".join(lines))
        parts.append(" ".join(post.hashtags))
        return Caption("\n\n".join(parts), post.hashtags)

    def duplicate_captions(self, post: Post) -> list[str]:
        """Other queued posts with the same caption (reconciliation matches by caption)."""
        items, _ = load_queue(self.page.dir)
        mine = " ".join(post.caption.lower().split())
        return [
            i.post.id
            for i in items
            if i.post.id != post.id and " ".join(i.post.caption.lower().split()) == mine
        ]

    def ai_label_reasons(self, post: Post, composed: Composed) -> list[str]:
        """Why the post needs Instagram's AI label (empty when it does not)."""
        reasons = []
        if self.page.ai_generated_art and (post.format != "reel" or not composed.placeholder):
            reasons.append("the illustrations are AI-generated")
        if "voice" in composed.audio_mode:
            reasons.append("the voiceover is AI-generated")
        return reasons

    def text_gates(self, post: Post, caption: Caption) -> GateReport:
        """Spelling, lengths, hashtags and structure (no rendering needed)."""
        report = GateReport()
        limits = self.cfg.section("limits")
        max_chars = int(limits["text_max_chars"]["story"])
        texts = post.plain_texts
        unknown = unknown_words([*texts, post.caption], self.allowed)
        report.add("text:spelling", not unknown, ", ".join(sorted(unknown)) or "all words known")
        longest = max((len(t) for t in texts), default=0)
        report.add("text:length", 0 < longest <= max_chars, f"longest slide {longest} chars")
        report.add(
            "caption:length",
            0 < len(caption.text) <= int(limits["caption_max_chars"]),
            f"{len(caption.text)} chars",
        )
        n_tags = len(caption.hashtags)
        report.add(
            "caption:hashtags",
            int(limits["min_hashtags"]) <= n_tags <= int(limits["max_hashtags"])
            and all(HASHTAG_RE.match(t) for t in caption.hashtags),
            f"{n_tags} hashtags",
        )
        twins = self.duplicate_captions(post)
        report.add(
            "caption:unique",
            not twins,
            f"same caption as {', '.join(twins)}" if twins else "unique in the queue",
        )
        banned = sorted(set(caption.hashtags) & self.banned)
        report.add("caption:banned-hashtags", not banned, ", ".join(banned) or "none")
        sp = self.cfg.section("story", "slides")
        problems = structure_problems(
            post,
            (int(sp["min"]), int(sp["max"])),
            int(self.cfg.section("story", "cover_max_words")),
        )
        report.add("post:structure", not problems, "; ".join(problems) or "ok")
        return report

    # ----- slides -------------------------------------------------------------------------
    def _template(self, post: Post) -> Template:
        names = [t.name for t in self.theme.templates]
        name = post.template or names[seed_int(post.id) % len(names)]
        return self.theme.get(name)

    def _art(
        self, post: Post, n: int, size: tuple[int, int], focus: str, allow_placeholder: bool
    ) -> tuple[Image.Image, float, bool]:
        path = post.dir / post.slides[n - 1].art
        if path.is_file():
            img, scale = load_art(path, size, focus)
            return img, scale, False
        if not allow_placeholder:
            raise ContentError(f"{post.id}: {path.name} has not been supplied yet")
        return placeholder_art(self._template(post), size, seed_int(f"{post.id}-{n}")), 1.0, True

    def _sizes(self, post: Post) -> tuple[int, int]:
        """(cover px, body px): one size for every slide after the first; cover never smaller."""
        st = self.style
        hi, lo, step = st.px_range
        body = hi
        for s in post.slides[1:]:
            box = story_text_box(self.cfg, st, s.position)
            px = max_fit_px(s.text, box, st.font, hi, lo, step, st)
            if px is None:
                raise ContentError(f"slide {s.index}: text too long for {lo}px — shorten it")
            body = min(body, px)
        first = post.slides[0]
        cover_lo = body if post.format == "carousel" else lo
        box = story_text_box(self.cfg, st, first.position)
        cover = max_fit_px(first.text, box, st.cover_font, st.cover_px_max, cover_lo, step, st)
        if cover is None:
            raise ContentError("slide 1: text too long — shorten it")
        return cover, body

    def compose_slides(self, post: Post, out_dir: Path, allow_placeholder: bool) -> Composed:
        """Render every slide of a carousel/image post and run the slide gates."""
        st = self.style
        report = GateReport()
        try:
            cover_px, body_px = self._sizes(post)
        except ContentError as exc:
            report.add("text:fit", False, str(exc)[:100])
            return Composed([], report, False)
        slides: list[ComposedSlide] = []
        paths: list[Path] = []
        for spec in post.slides:
            art, scale, placeholder = self._art(
                post, spec.index, st.size, spec.focus, allow_placeholder
            )
            first = spec.index == 1
            image, bg, elements, opacity = compose_slide(
                self.cfg,
                st,
                art,
                spec.text,
                spec.position,
                st.cover_font if first else st.font,
                cover_px if first else body_px,
                self.page.handle,
            )
            path = out_dir / f"{post.id}-{spec.index:02d}.jpg"
            save_jpeg(image, path, int(self.cfg.section("render", "jpeg_quality")))
            slides.append(
                ComposedSlide(
                    spec.index,
                    image,
                    bg,
                    elements,
                    opacity,
                    cover_px if first else body_px,
                    scale,
                    placeholder,
                )
            )
            paths.append(path)
        cover_words = len(strip_markup(post.slides[0].text).split())
        report.extend(
            check_slides(
                self.cfg, st, post.format, slides, paths, cover_words, not allow_placeholder
            )
        )
        return Composed(paths, report, any(s.placeholder for s in slides), slides)

    # ----- reels --------------------------------------------------------------------------
    def _reel_attempts(self, post: Post, key: str) -> list[tuple[str, str, str | None]]:
        """(fallback label, audio mode, voice) in fallback order."""
        voices = [str(v) for v in self.cfg.section("audio", "voices")]
        first = voices[seed_int(key) % len(voices)]
        second = voices[(voices.index(first) + 1) % len(voices)]
        if "voice" in post.audio:
            return [
                ("", post.audio, first),
                ("second-voice", post.audio, second),
                ("music-only", "music", None),
            ]
        return [("", post.audio, None)]

    def _reel_overlay(self, elements: list[TextElement], size: tuple[int, int]) -> Image.Image:
        """Static cream panels behind the reel's words and watermark (for art backgrounds)."""
        st = self.style
        overlay = Image.new("RGBA", size, (*st.panel_color, 0))
        body = [e for e in elements if e.role != "watermark"]
        boxes = [e.bbox for e in body]
        union = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        mask = panel_mask(
            size, union, st.pad_px + 30, st.feather_px
        )  # extra room for the slow zoom
        wm = [e for e in elements if e.role == "watermark"]
        if wm:
            wm_mask = panel_mask(size, wm[0].bbox, max(12, st.watermark_px // 2), 6)
            mask = Image.fromarray(np.maximum(np.asarray(mask), np.asarray(wm_mask)))
        alpha = mask.point(lambda v: round(v * st.panel_opacities[-1]))
        overlay.putalpha(alpha)
        return overlay

    def compose_reel(
        self, post: Post, out_dir: Path, key: str, allow_placeholder: bool, inject: frozenset[str]
    ) -> Composed:
        """Render the reel, trying the narration fallbacks in order."""
        spec = post.slides[0]
        text = strip_markup(spec.text)
        size_cfg = self.cfg.section("render", "reel_size")
        size = (int(size_cfg[0]), int(size_cfg[1]))
        has_art = (post.dir / spec.art).is_file()
        tpl = self._template(post)
        background: Image.Image | None = None
        upscale = 1.0
        if has_art:
            background, upscale = load_art(post.dir / spec.art, size, spec.focus)
            tpl = replace(
                tpl,
                text_color=self.style.text_color,
                accent_color=self.style.accent_color,
                font=self.style.font,
                motion="zoom",
                text_anim="word",
            )
        sr = int(self.cfg.section("audio", "sample_rate"))
        seed = seed_int(key)
        opacity = self.theme.watermark_opacity
        elements = layout_for(self.cfg, tpl, text, None, self.page.handle, size, True, opacity)
        overlay = self._reel_overlay(elements, size) if has_art else None
        safe, _ = text_box(self.cfg, size, True)
        attempts = self._reel_attempts(post, key)
        last = Composed([], GateReport(), False)
        for n, (fallback, mode, voice_name) in enumerate(attempts):
            report = GateReport()
            if has_art:
                report.add(
                    "art-resolution",
                    upscale <= self.style.max_upscale,
                    f"art enlarged x{upscale:.2f}, maximum x{self.style.max_upscale:.2f}",
                )
            voice: VoiceTrack | None = None
            try:
                if "voice" in mode and voice_name:
                    voice = self.narrator.synthesise(text, voice_name, seed)
                    sabotage = ("voice_gate" in inject and n == 0) or "voice_gate_all" in inject
                    if sabotage:  # rehearsal: simulate a silent/broken TTS output
                        voice.audio = np.zeros_like(voice.audio)
                duration = reel_duration(self.cfg, elements, voice, tpl)
                music = None
                if "music" in mode:
                    music = make_music(
                        str(self.cfg.section("audio", "music_source")), duration, sr, seed
                    )
                audio = mix(
                    voice.audio if voice is not None and voice.audio.any() else None,
                    music,
                    sr,
                    duration,
                    float(self.cfg.section("audio", "duck_db")),
                    float(self.cfg.section("audio", "target_lufs")),
                )
                out = out_dir / f"{post.id}.mp4"
                r = render_reel(
                    self.cfg,
                    tpl,
                    text,
                    None,
                    self.page.handle,
                    seed,
                    out,
                    audio,
                    voice,
                    mode,
                    opacity,
                    background,
                    overlay,
                )
                report.extend(
                    check_reel(
                        out,
                        self.cfg,
                        mode,
                        voice,
                        r.reveal_times[: len(r.spoken_starts)],
                        r.spoken_starts,
                    )
                )
                report.extend(check_visual(r.elements, r.sample_backgrounds, safe, self.cfg.gates))
            except AutopilotError as exc:
                report.add("render", False, str(exc)[:80])
                out = None
            last = Composed(
                [out] if out else [],
                report,
                not has_art,
                audio_mode=mode,
                voice=voice_name or "",
                fallback=fallback,
            )
            if out is not None and report.passed:
                return last
        last.fallback = "skipped"
        return last

    # ----- story reels (a carousel as a 9:16 reel) -----------------------------------------
    def story_reel_music(self, duration: float, seed: int, choice: str = "") -> tuple[Any, str]:
        """(audio, source label): our own generated music or a licensed CC0 track by name."""
        sr = int(self.cfg.section("audio", "sample_rate"))
        name = choice or str(self.cfg.section("story_reel", "music"))
        if name == "procedural":
            lead = str(self.cfg.section("story_reel", "music_lead"))
            return procedural(duration, sr, seed, lead), "procedural"
        return licensed_track(name, duration, sr), name

    def compose_story_reel(
        self, post: Post, out_dir: Path, allow_placeholder: bool, music: str = ""
    ) -> Composed:
        """Turn a carousel into a 9:16 reel: slow zoom/pan art, words fading in, gentle music."""
        rs = reel_settings(self.cfg)
        st = self.style
        report = GateReport()
        if post.format != "carousel":
            report.add("story-reel:format", False, "only carousels become story reels")
            return Composed([], report, False)
        texts = [s.text for s in post.slides]
        try:
            cover_px, body_px = text_sizes(texts, [s.position for s in post.slides], st, rs)
        except ContentError as exc:
            report.add("story-reel:text-fit", False, str(exc)[:100])
            return Composed([], report, False)
        words = [len(strip_markup(t).split()) for t in texts]
        timings, duration = plan_story_timing(words, rs)
        try:
            slides, placeholder = self._story_reel_slides(
                post, (cover_px, body_px), words, timings, allow_placeholder, report
            )
        except ContentError as exc:
            report.add("story-reel:art-supplied", False, str(exc)[:100])
            return Composed([], report, False)
        if not allow_placeholder:
            report.add(
                "story-reel:art-supplied", not placeholder, "placeholder art is never posted"
            )
        seed = seed_int(f"{post.id}-story-reel")
        source = music or str(self.cfg.section("story_reel", "music"))
        if source != "procedural":
            problems = licence_problems(source)
            report.add("music:licence", not problems, "; ".join(problems) or f"{source}: CC0")
            if problems:
                return Composed([], report, placeholder)
        try:
            track, label = self.story_reel_music(duration, seed, source)
        except AutopilotError as exc:
            report.add("music:source", False, str(exc)[:100])
            return Composed([], report, placeholder)
        return self._finish_story_reel(
            post, out_dir, slides, duration, track, label, placeholder, report
        )

    def _story_reel_slides(
        self,
        post: Post,
        sizes: tuple[int, int],
        words: list[int],
        timings: list[SlideTiming],
        allow_placeholder: bool,
        report: GateReport,
    ) -> tuple[list[ReelSlide], bool]:
        """Lay out and gate every slide of a story reel; returns (slides, any placeholder)."""
        rs, st, gates = reel_settings(self.cfg), self.style, self.cfg.gates
        cover_px, body_px = sizes
        story_gates = replace(
            gates, min_font_px=gates.story_min_font_px, min_contrast=gates.story_min_contrast
        )
        slides: list[ReelSlide] = []
        placeholder = False
        for spec, timing, n_words in zip(post.slides, timings, words, strict=True):
            path = post.dir / spec.art
            if path.is_file():
                art = open_art(path)
            elif allow_placeholder:
                art = placeholder_art(
                    self._template(post), st.size, seed_int(f"{post.id}-{spec.index}")
                )
                placeholder = True
            else:
                raise ContentError(f"{spec.art} has not been supplied yet")
            slide = build_slide(
                spec.index,
                art,
                spec.text,
                spec.position,
                spec.focus,
                cover_px if spec.index == 1 else body_px,
                n_words,
                timing,
                st,
                rs,
                gates.story_min_contrast + st.contrast_margin,
                gates.contrast_percentile,
            )
            slides.append(slide)
            n = spec.index
            for r in check_visual(slide.elements, slide.samples, rs.safe, story_gates).results:
                report.add(f"reel-slide{n}:{r.name}", r.passed, r.detail)
            if path.is_file():
                up = slide.motion.upscale
                report.add(
                    f"reel-slide{n}:art-resolution",
                    up <= st.max_upscale,
                    f"art enlarged x{up:.2f}, maximum x{st.max_upscale:.2f}",
                )
            need = n_words / rs.words_per_second
            shown = timing.reading_time(rs, n == len(post.slides))
            report.add(
                f"reel-slide{n}:reading-time",
                shown >= need,
                f"words fully shown {shown:.1f}s, need {need:.1f}s",
            )
        return slides, placeholder

    def _finish_story_reel(
        self,
        post: Post,
        out_dir: Path,
        slides: list[ReelSlide],
        duration: float,
        track: Any,
        label: str,
        placeholder: bool,
        report: GateReport,
    ) -> Composed:
        """Mix the music, encode the reel and cover, and run the file and audio gates."""
        rs = reel_settings(self.cfg)
        sr = int(self.cfg.section("audio", "sample_rate"))
        audio = mix(
            None,
            track,
            sr,
            duration,
            float(self.cfg.section("audio", "duck_db")),
            float(self.cfg.section("audio", "target_lufs")),
        )
        out = out_dir / f"{post.id}-story-reel.mp4"
        cover = out_dir / f"{post.id}-story-reel-cover.jpg"
        try:
            render_story_reel(
                slides,
                duration,
                audio,
                sr,
                rs,
                out,
                cover,
                int(self.cfg.section("render", "jpeg_quality")),
            )
        except AutopilotError as exc:
            report.add("render", False, str(exc)[:80])
            return Composed([], report, placeholder)
        report.extend(check_reel(out, self.cfg, "music", None, [], [], rs.allowed_seconds))
        size = (int(rs.size[0]), int(rs.size[1]))
        cover_report = check_image_file(cover, self.cfg, size)
        for r in cover_report.results:
            report.add(f"cover:{r.name}", r.passed, r.detail)
        return Composed(
            [out],
            report,
            placeholder,
            audio_mode="music",
            cover=cover,
            music=label,
            seconds=duration,
        )

    # ----- whole post ---------------------------------------------------------------------
    def compose(
        self,
        post: Post,
        out_dir: Path,
        key: str,
        allow_placeholder: bool,
        inject: frozenset[str] = frozenset(),
    ) -> tuple[Composed, Caption, GateReport]:
        """Text gates, then rendering + media gates. Returns (composed, caption, text report)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        narrated = post.format == "reel" and "voice" in post.audio
        caption = self.caption(post, narrated)
        text_report = self.text_gates(post, caption)
        if not text_report.passed:
            return Composed([], GateReport(), False), caption, text_report
        if post.format == "reel":
            composed = self.compose_reel(post, out_dir, key, allow_placeholder, inject)
            if composed.fallback == "music-only":
                caption = self.caption(post, narrated=False)
        else:
            composed = self.compose_slides(post, out_dir, allow_placeholder)
        return composed, caption, text_report

    def build(
        self, req: Request, state: PageState, log: Callable[[str], None] = print
    ) -> BuildResult:
        """Build the slot's post; never returns a manifest for media that failed a gate."""
        key = req.slot_key
        items = self.queue()
        changed = [i.post.id for i in items if i.approval is not None and i.problems]
        used = used_post_ids(state, except_slot=key)
        if "queue_empty" in req.inject:
            used |= {i.post.id for i in items}
        prev = state.get(key)
        item: QueuedPost | None = None
        if prev is not None and prev.content_id:  # retry of this slot: same post, same caption
            item = next((i for i in items if i.post.id == prev.content_id and i.publishable), None)
            if item is None:
                result = BuildResult(None, [], None, changed=changed)
                result.skipped_reason = f"{prev.content_id} is no longer approved or has changed"
                return result
        elif req.post_id:
            item = next((i for i in publishable(items, used) if i.post.id == req.post_id), None)
        else:
            item = next_for_slot(items, used, req.format)
        result = BuildResult(None, [], item.post if item else None, changed=changed)
        if item is None:
            what = req.post_id or ("post" if req.format == "any" else f"{req.format} post")
            result.skipped_reason = f"queue empty: no approved {what} ready"
            return result
        post = item.post
        result.queue_left = len(publishable(items, used | {post.id}))
        out_dir = self.out_dir / key
        composed, caption, text_report = self.compose(
            post, out_dir, key, allow_placeholder=self.rehearsal, inject=req.inject
        )
        label = f"{post.format}/{post.id}" + (f"/{composed.voice}" if composed.voice else "")
        report = GateReport()
        report.extend(text_report)
        report.extend(composed.report)
        result.gates.append((label, report))
        log(f"  {label}: {report.summary()}")
        if not report.passed or not composed.paths or composed.fallback == "skipped":
            result.fallback = "skipped"
            result.skipped_reason = f"{post.id} failed its quality gates (nothing was posted)"
            return result
        urls = []
        for path, name in zip(
            composed.paths, media_names(key, post.format, len(composed.paths)), strict=True
        ):
            urls.append(self.hosting.host(self.page.name, name, path))
        disc = self.cfg.section("ai_disclosure")
        result.manifest = Manifest(
            page=self.page.name,
            slot_key=key,
            content_id=post.id,
            format=post.format,
            audio_mode=composed.audio_mode,
            fallback=composed.fallback,
            template="art" if not composed.placeholder else "placeholder",
            caption=caption.text,
            hashtags=caption.hashtags,
            media_urls=tuple(urls),
            ai_generated=bool(disc["api_flag"]) and bool(self.ai_label_reasons(post, composed)),
        )
        result.media_paths, result.fallback = composed.paths, composed.fallback
        return result


def _rehearsal_approval(post: Post) -> Approval:
    return Approval(post.id, "rehearsal", post.format, len(post.slides), {}, "rehearsal")
