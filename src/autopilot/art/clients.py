"""Image-generation clients and vision checkers (swappable in ``config.yaml``).

``provider`` picks who draws (Gemini or OpenAI, both accept reference images), ``checker`` picks
who inspects every result before it may be saved. Both talk plain HTTPS through
:mod:`autopilot.art.http`; keys go in a request header only, never in a URL or a message.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from autopilot.art.http import Transport, call, urllib_transport
from autopilot.errors import ArtError

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_API = "https://api.openai.com/v1"


@dataclass(frozen=True)
class RefImage:
    """A reference picture sent with a request (character sheet or an earlier slide)."""

    label: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class GeneratedImage:
    """Raw bytes as returned by the provider (cropped and resized afterwards)."""

    data: bytes
    mime: str


class NoImageError(ArtError):
    """The provider answered but drew nothing (for example its own safety filter)."""


@dataclass(frozen=True)
class CharacterSpec:
    """A character the checker must find in the picture."""

    name: str
    must: tuple[str, ...]


@dataclass(frozen=True)
class CheckSpec:
    """What the vision check is told about one slide."""

    characters: tuple[CharacterSpec, ...] = ()
    scene: str = ""


@dataclass(frozen=True)
class Verdict:
    """The vision model's answer; ``passed`` only when every rule holds."""

    pointed_ears: bool
    visible_text: bool
    characters_match: bool
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """No pointed ears, no text, and the expected characters match their sheets."""
        return not self.pointed_ears and not self.visible_text and self.characters_match

    @property
    def reasons(self) -> list[str]:
        """Plain-English reasons for a rejection."""
        out = []
        if self.pointed_ears:
            out.append("pointed or elf ears")
        if self.visible_text:
            out.append("visible text or letters")
        if not self.characters_match:
            out.append("characters do not match the reference sheets")
        return out + [p for p in self.problems if p]


class ImageClient(Protocol):
    """Draws one picture from a prompt and reference images."""

    name: str
    usd_per_image: float

    def generate(self, prompt: str, refs: Sequence[RefImage]) -> GeneratedImage:
        """One picture (raises NoImageError when the provider draws nothing)."""
        ...


class Checker(Protocol):
    """Inspects one finished picture."""

    name: str
    usd_per_check: float

    def check(self, image: bytes, refs: Sequence[RefImage], spec: CheckSpec) -> Verdict:
        """The verdict for ``image`` (PNG bytes)."""
        ...


# ----- shared prompt for every checker ---------------------------------------------------------
VERDICT_FIELDS = ("pointed_or_elf_ears", "visible_text", "characters_match", "problems")


def check_prompt(spec: CheckSpec, ref_labels: Sequence[str]) -> str:
    """Instructions for the vision model (the candidate image always comes first)."""
    lines = [
        "You are a strict quality checker for a watercolour storybook Instagram page.",
        "The FIRST image is the candidate illustration."
        + (
            " The other images are reference sheets, in this order: " + "; ".join(ref_labels) + "."
            if ref_labels
            else ""
        ),
        "Answer with JSON only, using exactly these fields:",
        "- pointed_or_elf_ears: true if ANY person or creature in the candidate has pointed, "
        "elf-like or otherwise non-human ears. Ears hidden by hair or turned away count as false.",
        "- visible_text: true if the candidate shows ANY letters, words, numbers, signatures, "
        "watermarks, logos, signs or writing, even tiny, blurred, fake or decorative ones.",
    ]
    if spec.characters:
        for c in spec.characters:
            lines.append(f"Expected character {c.name}: {'; '.join(c.must)}.")
        lines.append(
            "- characters_match: true only if every expected character appears and looks like "
            "the same person as on their reference sheet (face, skin tone, hair, beard, coat and "
            "scarf colours). Pose, angle, lighting and painting style may differ. A character seen "
            "from behind or small in the distance matches when hair, coat and scarf match."
        )
    else:
        lines.append(
            "- characters_match: no named character is expected; answer true unless a person in "
            "the picture is visibly deformed (extra limbs, broken face)."
        )
    if spec.scene:
        lines.append(f"(The slide shows: {spec.scene})")
    lines.append("- problems: a short list of every problem you found (empty list if none).")
    return "\n".join(lines)


def parse_verdict(data: Any) -> Verdict:
    """Strictly parse the checker's JSON; anything missing or odd counts as a failure."""
    if not isinstance(data, dict) or any(k not in data for k in VERDICT_FIELDS[:3]):
        return Verdict(True, True, False, ("the checker's answer was incomplete",))
    problems = data.get("problems") or []
    if isinstance(problems, str):
        problems = [problems]
    return Verdict(
        pointed_ears=data["pointed_or_elf_ears"] is not False,
        visible_text=data["visible_text"] is not False,
        characters_match=data["characters_match"] is True,
        problems=tuple(" ".join(str(p).split())[:200] for p in problems if str(p).strip())[:6],
    )


def _json_text(text: str) -> Any:
    try:
        return json.loads(text.strip().removeprefix("```json").removesuffix("```"))
    except ValueError:
        return None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ----- Google Gemini ---------------------------------------------------------------------------
def _gemini_headers(key: str) -> dict[str, str]:
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def _gemini_parts(refs: Sequence[RefImage]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for ref in refs:
        parts.append({"text": f"Reference image: {ref.label}"})
        parts.append({"inline_data": {"mime_type": ref.mime, "data": _b64(ref.data)}})
    return parts


def _gemini_candidate_parts(data: Any) -> tuple[list[dict[str, Any]], str]:
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not cands:
        block = (data or {}).get("promptFeedback", {}).get("blockReason", "no answer")
        return [], str(block)
    first = cands[0] or {}
    parts = (first.get("content") or {}).get("parts") or []
    return [p for p in parts if isinstance(p, dict)], str(first.get("finishReason", ""))


class GeminiImageClient:
    """Gemini image models ("Nano Banana"), via ``models/<model>:generateContent``."""

    def __init__(
        self,
        key: str,
        model: str,
        aspect_ratio: str,
        image_size: str,
        usd_per_image: float,
        transport: Transport = urllib_transport,
    ) -> None:
        self._key = key
        self.model, self.aspect_ratio, self.image_size = model, aspect_ratio, image_size
        self.name = f"gemini/{model}"
        self.usd_per_image = usd_per_image
        self._transport = transport

    def __repr__(self) -> str:  # never show the key
        return f"GeminiImageClient({self.model}, <redacted>)"

    def generate(self, prompt: str, refs: Sequence[RefImage]) -> GeneratedImage:
        """One picture at the configured aspect ratio and size."""
        body = {
            "contents": [{"role": "user", "parts": [*_gemini_parts(refs), {"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": self.aspect_ratio, "imageSize": self.image_size},
            },
        }
        resp = call(
            self._transport,
            "POST",
            f"{GEMINI_API}/models/{self.model}:generateContent",
            _gemini_headers(self._key),
            json.dumps(body).encode(),
            "image generation",
        )
        parts, reason = _gemini_candidate_parts(resp.json())
        images = [
            p.get("inlineData") or p.get("inline_data")
            for p in parts
            if not p.get("thought") and (p.get("inlineData") or p.get("inline_data"))
        ]
        if not images:
            raise NoImageError(f"the image model drew nothing ({reason or 'no reason given'})")
        last: dict[str, Any] = images[-1] or {}
        try:
            raw = base64.b64decode(str(last.get("data", "")), validate=True)
        except ValueError:
            raise NoImageError("the image model returned unreadable image data") from None
        return GeneratedImage(raw, str(last.get("mimeType") or last.get("mime_type") or ""))


class GeminiChecker:
    """Gemini vision model answering with schema-checked JSON."""

    def __init__(
        self, key: str, model: str, usd_per_check: float, transport: Transport = urllib_transport
    ) -> None:
        self._key = key
        self.model = model
        self.name = f"gemini/{model}"
        self.usd_per_check = usd_per_check
        self._transport = transport

    def __repr__(self) -> str:
        return f"GeminiChecker({self.model}, <redacted>)"

    def check(self, image: bytes, refs: Sequence[RefImage], spec: CheckSpec) -> Verdict:
        """Ask the vision model about ``image``."""
        schema = {
            "type": "OBJECT",
            "properties": {
                "pointed_or_elf_ears": {"type": "BOOLEAN"},
                "visible_text": {"type": "BOOLEAN"},
                "characters_match": {"type": "BOOLEAN"},
                "problems": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": list(VERDICT_FIELDS),
        }
        parts = [
            {"text": check_prompt(spec, [r.label for r in refs])},
            {"text": "Candidate illustration:"},
            {"inline_data": {"mime_type": "image/png", "data": _b64(image)}},
            *_gemini_parts(refs),
        ]
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
        }
        resp = call(
            self._transport,
            "POST",
            f"{GEMINI_API}/models/{self.model}:generateContent",
            _gemini_headers(self._key),
            json.dumps(body).encode(),
            "image check",
        )
        parts_out, _ = _gemini_candidate_parts(resp.json())
        text = "".join(str(p.get("text", "")) for p in parts_out if not p.get("thought"))
        return parse_verdict(_json_text(text))


# ----- OpenAI ----------------------------------------------------------------------------------
def _multipart(fields: dict[str, str], files: Sequence[RefImage]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    for n, ref in enumerate(files, start=1):
        ext = "png" if ref.mime == "image/png" else "jpg"
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; '
            f'filename="ref-{n}.{ext}"\r\nContent-Type: {ref.mime}\r\n\r\n'
        ).encode()
        out += ref.data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class OpenAIImageClient:
    """OpenAI GPT Image models: ``images/edits`` with reference images, else ``generations``."""

    def __init__(
        self,
        key: str,
        model: str,
        size: str,
        quality: str,
        usd_per_image: float,
        transport: Transport = urllib_transport,
    ) -> None:
        self._key = key
        self.model, self.size, self.quality = model, size, quality
        self.name = f"openai/{model}"
        self.usd_per_image = usd_per_image
        self._transport = transport

    def __repr__(self) -> str:
        return f"OpenAIImageClient({self.model}, <redacted>)"

    def generate(self, prompt: str, refs: Sequence[RefImage]) -> GeneratedImage:
        """One picture; reference images are described at the start of the prompt."""
        if refs:
            labels = "; ".join(f"image {n}: {r.label}" for n, r in enumerate(refs, start=1))
            prompt = f"Reference images ({labels}).\n{prompt}"
        fields = {
            "model": self.model,
            "prompt": prompt,
            "size": self.size,
            "quality": self.quality,
            "n": "1",
        }
        headers = {"Authorization": f"Bearer {self._key}"}
        if refs:
            body, ctype = _multipart(fields, refs)
            url = f"{OPENAI_API}/images/edits"
        else:
            body, ctype = json.dumps({**fields, "n": 1}).encode(), "application/json"
            url = f"{OPENAI_API}/images/generations"
        resp = call(
            self._transport,
            "POST",
            url,
            {**headers, "Content-Type": ctype},
            body,
            "image generation",
        )
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        b64 = (items[0] or {}).get("b64_json") if items else None
        if not b64:
            raise NoImageError("the image model drew nothing")
        try:
            return GeneratedImage(base64.b64decode(str(b64), validate=True), "image/png")
        except ValueError:
            raise NoImageError("the image model returned unreadable image data") from None


class OpenAIChecker:
    """OpenAI vision model answering in JSON mode."""

    def __init__(
        self, key: str, model: str, usd_per_check: float, transport: Transport = urllib_transport
    ) -> None:
        self._key = key
        self.model = model
        self.name = f"openai/{model}"
        self.usd_per_check = usd_per_check
        self._transport = transport

    def __repr__(self) -> str:
        return f"OpenAIChecker({self.model}, <redacted>)"

    def check(self, image: bytes, refs: Sequence[RefImage], spec: CheckSpec) -> Verdict:
        """Ask the vision model about ``image``."""
        content: list[dict[str, Any]] = [
            {"type": "text", "text": check_prompt(spec, [r.label for r in refs])},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(image)}"}},
        ]
        for ref in refs:
            url = f"data:{ref.mime};base64,{_b64(ref.data)}"
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        resp = call(
            self._transport,
            "POST",
            f"{OPENAI_API}/chat/completions",
            {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            json.dumps(body).encode(),
            "image check",
        )
        data = resp.json()
        try:
            text = str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            text = ""
        return parse_verdict(_json_text(text))


def model_exists(provider: str, model: str, key: str, transport: Transport) -> None:
    """Free call that proves the key works and the model name exists (ArtError otherwise)."""
    if provider == "gemini":
        url, headers = f"{GEMINI_API}/models/{model}", {"x-goog-api-key": key}
    elif provider == "openai":
        url, headers = f"{OPENAI_API}/models/{model}", {"Authorization": f"Bearer {key}"}
    else:
        return
    call(transport, "GET", url, headers, None, f"{provider} model {model}", waits=(5.0,))
