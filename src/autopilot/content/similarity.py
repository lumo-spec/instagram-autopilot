"""Near-duplicate detection for short texts.

Two texts are near-duplicates when, after normalising case, punctuation and whitespace, either
their token-set similarity is at or above the threshold (and their lengths are comparable, so
a short phrase is not "contained" in every long one), or their word-trigram shingles overlap
heavily.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

_WORD_RE = re.compile(r"[a-z0-9']+")
MIN_LENGTH_RATIO = 0.6
SHINGLE_JACCARD = 0.5


def normalise(text: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'")
    return " ".join(_WORD_RE.findall(text))


def _shingles(words: Sequence[str], k: int = 3) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + k]) for i in range(max(len(words) - k + 1, 0))}


@dataclass(frozen=True)
class Match:
    """A detected near-duplicate pair."""

    a: str
    b: str
    score: float
    reason: str


def compare(a: str, b: str, threshold: float) -> Match | None:
    """Return a Match if ``a`` and ``b`` are near-duplicates, else None."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return None
    if na == nb:
        return Match(a, b, 100.0, "identical")
    wa, wb = na.split(), nb.split()
    ratio = min(len(wa), len(wb)) / max(len(wa), len(wb))
    score = float(fuzz.token_set_ratio(na, nb))
    if score >= threshold and ratio >= MIN_LENGTH_RATIO:
        return Match(a, b, score, "token-set")
    sa, sb = _shingles(wa), _shingles(wb)
    if sa and sb:
        jac = len(sa & sb) / len(sa | sb)
        if jac >= SHINGLE_JACCARD:
            return Match(a, b, round(jac * 100, 1), "shingles")
    return None


def find_duplicates(texts: Sequence[tuple[str, str]], threshold: float) -> list[Match]:
    """Pairwise check of ``(id, text)`` items; Match.a / .b hold the ids."""
    found: list[Match] = []
    for i, (id_a, text_a) in enumerate(texts):
        for id_b, text_b in texts[i + 1 :]:
            m = compare(text_a, text_b, threshold)
            if m:
                found.append(Match(id_a, id_b, m.score, m.reason))
    return found


def is_near_any(text: str, others: Iterable[str], threshold: float) -> bool:
    """True when ``text`` is a near-duplicate of any of ``others``."""
    return any(compare(text, o, threshold) for o in others)
