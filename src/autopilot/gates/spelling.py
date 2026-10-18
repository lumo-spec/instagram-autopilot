"""Spelling gate: every word must be in a local English dictionary or the page's allow-list."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from spellchecker import SpellChecker

_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
_POSSESSIVE = ("'s", "’s")


def words_in(text: str) -> list[str]:
    """Words in ``text``; hyphenated and dashed compounds are split into parts."""
    return _WORD_RE.findall(text)


@lru_cache(maxsize=1)
def _checker() -> SpellChecker:
    return SpellChecker(language="en")


def load_allowed(path: Path) -> frozenset[str]:
    """Read a page's ``spellcheck-words.txt`` (one word per line, ``#`` comments)."""
    if not path.exists():
        return frozenset()
    words = (ln.split("#", 1)[0].strip().lower() for ln in path.read_text("utf-8").splitlines())
    return frozenset(w for w in words if w)


def unknown_words(texts: Iterable[str], allowed: frozenset[str]) -> set[str]:
    """Lower-cased words found in neither the dictionary nor ``allowed``."""
    candidates: set[str] = set()
    for text in texts:
        for word in words_in(text):
            w = word.lower().replace("’", "'")
            for suffix in _POSSESSIVE:
                if w.endswith(suffix):
                    w = w[: -len(suffix)]
            if w not in allowed:
                candidates.add(w)
    return {w for w in _checker().unknown(candidates) if w not in allowed}
