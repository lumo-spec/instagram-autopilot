"""Fake image client and checker for tests and rehearsals (no network, no cost)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field

from autopilot.art.clients import CheckSpec, GeneratedImage, NoImageError, RefImage, Verdict


@dataclass
class FakeImageClient:
    """Draws a quiet cream-and-olive picture; ``refuse`` lists attempt numbers that draw nothing."""

    size: tuple[int, int] = (1600, 2000)
    refuse: set[int] = field(default_factory=set)
    name: str = "fake/painter"
    usd_per_image: float = 0.14
    prompts: list[str] = field(default_factory=list)
    refs_seen: list[list[str]] = field(default_factory=list)

    def generate(self, prompt: str, refs: Sequence[RefImage]) -> GeneratedImage:
        """A plain picture (the attempt number shades it, so each one differs)."""
        from PIL import Image

        self.prompts.append(prompt)
        self.refs_seen.append([r.label for r in refs])
        n = len(self.prompts)
        if n in self.refuse:
            raise NoImageError("the image model drew nothing (fake refusal)")
        w, h = self.size
        img = Image.new("RGB", (w, h), (246, 238, 222 - n % 20))
        img.paste((120, 110, 70), (0, h // 2, w, h))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return GeneratedImage(buf.getvalue(), "image/png")


@dataclass
class FakeChecker:
    """Passes everything unless ``script`` says otherwise (one entry per check, in order)."""

    script: list[str] = field(default_factory=list)  # "pass", "ears", "text", "mismatch"
    name: str = "fake/inspector"
    usd_per_check: float = 0.01
    seen: list[CheckSpec] = field(default_factory=list)

    def check(self, image: bytes, refs: Sequence[RefImage], spec: CheckSpec) -> Verdict:
        """The next scripted verdict (``pass`` when the script runs out)."""
        self.seen.append(spec)
        outcome = self.script.pop(0) if self.script else "pass"
        return Verdict(
            pointed_ears=outcome == "ears",
            visible_text=outcome == "text",
            characters_match=outcome != "mismatch",
            problems=() if outcome == "pass" else (f"fake {outcome} problem",),
        )
