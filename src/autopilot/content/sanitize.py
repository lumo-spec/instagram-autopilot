"""Sanitising untrusted free text (on-demand inputs, feed text) before any other use."""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")
_ALLOWED = re.compile(r"[^A-Za-z0-9 .,;:!?'\"()\-—–‘’“”]")


def sanitise_text(raw: str, max_chars: int) -> str:
    """NFKC-normalise, drop control/zero-width characters and anything outside the allowed set,
    collapse whitespace and truncate to ``max_chars`` (at a word boundary)."""
    text = unicodedata.normalize("NFKC", raw)
    text = _INVISIBLE.sub("", text)
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in text)
    text = _ALLOWED.sub("", text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0]
    return text.strip()
