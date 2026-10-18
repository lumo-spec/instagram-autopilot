"""Build step: pick content, render, run every gate, apply fallbacks, caption, host, manifest.

Runs without secrets. Fallback order when a gate fails: regenerate the narration once with
another configured voice -> music-only reel -> image post -> skip the slot (and alert).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from autopilot.audio.mix import mix
from autopilot.audio.music import make_music
from autopilot.audio.voice import Narrator, VoiceTrack, load_pronunciations
from autopilot.config import Config, Page
from autopilot.content.bank import choose_audio_mode, choose_template, select_entry, topic_tags
from autopilot.content.captions import Caption, build_caption, load_caption_kit
from autopilot.content.validate import length_class, load_bank, postable_entries
from autopilot.errors import AutopilotError, ContentError
from autopilot.gates import GateReport
from autopilot.gates.media import check_image_file, check_reel
from autopilot.gates.spelling import load_allowed, unknown_words
from autopilot.gates.visual import check_visual
from autopilot.manifest import Manifest
from autopilot.render.image import layout_for, render_image, text_box
from autopilot.render.reel import reel_duration, render_reel
from autopilot.render.theme import load_theme
from autopilot.state import PageState


class Hosting(Protocol):
    """Puts a media file somewhere Instagram can fetch it."""

    def host(self, page: str, slot_key: str, path: Path) -> str: ...
    def remove(self, page: str, slot_key: str, ext: str) -> None: ...


@dataclass
class Request:
    """What to build."""

    slot_key: str
    local_date: date
    format: str
    topic: str = ""
    exact_text: str = ""
    audio: str = "auto"
    inject: frozenset[str] = frozenset()  # rehearsal-only simulated failures


@dataclass
class BuildResult:
    """Outcome of a build."""

    manifest: Manifest | None
    media_path: Path | None
    entry: dict[str, Any] | None
    gates: list[tuple[str, GateReport]] = field(default_factory=list)  # (attempt, report)
    fallback: str = ""
    skipped_reason: str = ""
    unused_left: int = 0
    recycled: bool = False


def seed_int(key: str) -> int:
    """Stable integer seed from a slot key."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")


class Builder:
    """Builds posts for one page."""

    def __init__(self, cfg: Config, page: Page, out_dir: Path, hosting: Hosting) -> None:
        self.cfg, self.page, self.out_dir, self.hosting = cfg, page, out_dir, hosting
        self.max_chars = int(cfg.section("limits", "text_max_chars", page.type))
        bank = load_bank(page.dir / "content.json")
        self.entries = postable_entries(bank, self.max_chars)
        self.synonyms: dict[str, list[str]] = bank.get("synonyms", {})
        self.theme = load_theme(page.dir / "theme.yaml")
        self.kit = load_caption_kit(page.dir / "captions.json")
        self.allowed = load_allowed(page.dir / "spellcheck-words.txt")
        self._narrator: Narrator | None = None

    @property
    def narrator(self) -> Narrator:
        """Load the TTS model on first use only."""
        if self._narrator is None:
            self._narrator = Narrator(
                self.cfg, load_pronunciations(self.page.dir / "pronunciations.yaml")
            )
        return self._narrator

    # ----- content ------------------------------------------------------------------------
    def _entry(self, req: Request, state: PageState) -> tuple[dict[str, Any], bool, int]:
        if req.exact_text:
            cid = "custom-" + hashlib.sha256(req.exact_text.encode()).hexdigest()[:8]
            entry: dict[str, Any] = {
                "id": cid,
                "text": req.exact_text,
                "author": None,
                "tags": [],
                "length_class": length_class(req.exact_text),
                "status": "verified",
            }
            return entry, False, len(self.entries) - len(state.last_used())
        wanted = None
        if req.topic:
            known = {t for e in self.entries for t in e.get("tags", [])}
            wanted = topic_tags(req.topic, self.synonyms, known) or None
            if wanted is None:
                raise ContentError("topic matches no tag")
        nd = self.cfg.section("near_duplicate")
        sel = select_entry(
            self.entries,
            state,
            req.local_date,
            req.slot_key,
            int(self.cfg.section("recycle_min_days", self.page.type)),
            float(nd["threshold"]),
            int(nd["recent_posts"]),
            wanted,
        )
        return sel.entry, sel.recycled, sel.unused_left

    def _text_gates(self, entry: dict[str, Any], caption: Caption) -> GateReport:
        report = GateReport()
        limits = self.cfg.section("limits")
        body = caption.text.replace(" ".join(caption.hashtags), "")
        unknown = unknown_words([entry["text"], body], self.allowed)
        report.add("text:spelling", not unknown, ", ".join(sorted(unknown)) or "all words known")
        report.add(
            "text:length", 0 < len(entry["text"]) <= self.max_chars, f"{len(entry['text'])} chars"
        )
        report.add(
            "caption:length",
            0 < len(caption.text) <= int(limits["caption_max_chars"]),
            f"{len(caption.text)} chars",
        )
        n_tags = len(caption.hashtags)
        report.add(
            "caption:hashtags",
            int(limits["min_hashtags"]) <= n_tags <= int(limits["max_hashtags"]),
            f"{n_tags} hashtags",
        )
        return report

    def _caption(
        self, entry: dict[str, Any], key: str, narrated: bool, state: PageState, day: date
    ) -> Caption:
        disc = self.cfg.section("ai_disclosure")
        return build_caption(
            self.kit,
            entry,
            key,
            int(self.cfg.section("limits", "max_hashtags")),
            narrated,
            str(disc["caption_text"]) if disc["caption"] else None,
            state.previous_hashtags(day),
        )

    # ----- rendering ----------------------------------------------------------------------
    def _image(self, entry: dict[str, Any], template: str, key: str) -> tuple[Path, GateReport]:
        tpl = self.theme.get(template)
        out = self.out_dir / f"{key}.jpg"
        r = render_image(
            self.cfg,
            tpl,
            entry["text"],
            entry.get("author"),
            self.page.handle,
            seed_int(key),
            out,
            self.theme.watermark_opacity,
        )
        safe, _ = text_box(self.cfg, r.background.size, False)
        report = check_visual(r.elements, [r.background], safe, self.cfg.gates)
        report.extend(check_image_file(out, self.cfg))
        return out, report

    def _reel(
        self,
        entry: dict[str, Any],
        template: str,
        key: str,
        mode: str,
        voice_name: str | None,
        sabotage_voice: bool,
    ) -> tuple[Path, GateReport]:
        tpl = self.theme.get(template)
        sr = int(self.cfg.section("audio", "sample_rate"))
        seed = seed_int(key)
        voice: VoiceTrack | None = None
        if "voice" in mode and voice_name:
            voice = self.narrator.synthesise(entry["text"], voice_name, seed)
            if sabotage_voice:  # rehearsal: simulate a silent/broken TTS output
                voice.audio = np.zeros_like(voice.audio)
        size_cfg = self.cfg.section("render", "reel_size")
        size = (int(size_cfg[0]), int(size_cfg[1]))
        elements = layout_for(
            self.cfg,
            tpl,
            entry["text"],
            entry.get("author"),
            self.page.handle,
            size,
            True,
            self.theme.watermark_opacity,
        )
        duration = reel_duration(self.cfg, elements, voice, tpl)
        music = None
        if "music" in mode:
            music = make_music(str(self.cfg.section("audio", "music_source")), duration, sr, seed)
        audio = mix(
            voice.audio if voice is not None and voice.audio.any() else None,
            music,
            sr,
            duration,
            float(self.cfg.section("audio", "duck_db")),
            float(self.cfg.section("audio", "target_lufs")),
        )
        out = self.out_dir / f"{key}.mp4"
        r = render_reel(
            self.cfg,
            tpl,
            entry["text"],
            entry.get("author"),
            self.page.handle,
            seed,
            out,
            audio,
            voice,
            mode,
            self.theme.watermark_opacity,
        )
        safe, _ = text_box(self.cfg, size, True)
        report = check_reel(
            out, self.cfg, mode, voice, r.reveal_times[: len(r.spoken_starts)], r.spoken_starts
        )
        report.extend(check_visual(r.elements, r.sample_backgrounds, safe, self.cfg.gates))
        return out, report

    def _attempts(self, req: Request, key: str) -> list[tuple[str, str, str, str | None]]:
        """(fallback label, format, audio mode, voice) in fallback order."""
        if req.format == "image":
            return [("", "image", "", None)]
        weights = {k: float(v) for k, v in self.cfg.section("audio", "weights").items()}
        mode = req.audio if req.audio != "auto" else choose_audio_mode(weights, key)
        voices = [str(v) for v in self.cfg.section("audio", "voices")]
        first = voices[seed_int(key) % len(voices)]
        second = voices[(voices.index(first) + 1) % len(voices)]
        steps: list[tuple[str, str, str, str | None]] = []
        if "voice" in mode:
            steps += [("", "reel", mode, first), ("second-voice", "reel", mode, second)]
            steps.append(("music-only", "reel", "music", None))
        else:
            steps.append(("", "reel", mode, None))
        steps.append(("image", "image", "", None))
        return steps

    def build(
        self, req: Request, state: PageState, log: Callable[[str], None] = print
    ) -> BuildResult:
        """Build one post; never returns a manifest for media that failed a gate."""
        key = req.slot_key
        try:
            entry, recycled, unused_left = self._entry(req, state)
        except ContentError as exc:
            return BuildResult(None, None, None, skipped_reason=str(exc))
        template = choose_template([t.name for t in self.theme.templates], state, entry["id"], key)
        result = BuildResult(None, None, entry, unused_left=unused_left, recycled=recycled)
        for n, (fallback, fmt, mode, voice) in enumerate(self._attempts(req, key)):
            label = f"{fmt}{'/' + mode if mode else ''}{'/' + voice if voice else ''}"
            narrated = "voice" in mode
            caption = self._caption(entry, key, narrated, state, req.local_date)
            report = self._text_gates(entry, caption)
            if not report.passed:
                result.gates.append((label, report))
                result.skipped_reason = "text gates failed"
                return result  # text problems affect every format: skip, never post
            try:
                if fmt == "image":
                    path, media_report = self._image(entry, template, key)
                else:
                    sabotage = (
                        "voice_gate" in req.inject and n == 0
                    ) or "voice_gate_all" in req.inject
                    path, media_report = self._reel(entry, template, key, mode, voice, sabotage)
            except AutopilotError as exc:
                media_report = GateReport()
                media_report.add("render", False, str(exc)[:80])
                path = None
            report.extend(media_report)
            result.gates.append((label, report))
            log(f"  attempt {label}: {report.summary()}")
            if path is None or not report.passed:
                continue
            url = self.hosting.host(self.page.name, key, path)
            disc = self.cfg.section("ai_disclosure")
            result.manifest = Manifest(
                page=self.page.name,
                slot_key=key,
                content_id=entry["id"],
                format=fmt,
                audio_mode=mode,
                fallback=fallback,
                template=template,
                caption=caption.text,
                hashtags=caption.hashtags,
                media_url=url,
                ai_generated=bool(narrated and disc["api_flag"]),
            )
            result.media_path, result.fallback = path, fallback
            return result
        result.fallback = "skipped"
        result.skipped_reason = "every format failed its quality gates"
        return result
