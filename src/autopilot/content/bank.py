"""Content selection with tag matching and duplicate prevention.

Rules: never repeat an entry until the whole bank has been used, then recycle the ones used
longest ago (facts only after ``recycle_min_days``) with a different template; never post two
entries with the same primary tag on the same day; skip anything that is a near-copy of one of
the last ``recent_posts`` posts.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from autopilot.content.similarity import is_near_any, normalise
from autopilot.errors import ContentError
from autopilot.state import PageState


@dataclass(frozen=True)
class Selection:
    """The chosen entry and how it was chosen."""

    entry: dict[str, Any]
    recycled: bool
    unused_left: int


def primary_tag(entry: dict[str, Any]) -> str:
    """First tag of an entry."""
    tags = entry.get("tags") or [""]
    return str(tags[0])


def topic_tags(topic: str, synonyms: dict[str, list[str]], known_tags: set[str]) -> set[str]:
    """Tags matching a typed topic (1-4 words) via direct tag names and the synonym map."""
    norm = normalise(topic)
    words = set(norm.split())
    tags = {t for t in known_tags if t in words or t.replace("-", " ") in norm}
    for phrase, mapped in synonyms.items():
        if re.search(rf"\b{re.escape(phrase)}\b", norm):
            tags.update(mapped)
    return tags & known_tags


def select_entry(
    entries: list[dict[str, Any]],
    state: PageState,
    day: date,
    seed: str,
    recycle_min_days: int,
    near_threshold: float,
    recent_posts: int,
    wanted_tags: set[str] | None = None,
) -> Selection:
    """Pick the next entry for ``day``; raises ContentError when nothing is eligible."""
    rng = random.Random(seed)
    by_id = {e["id"]: e for e in entries}
    used = state.last_used()
    unused = [e for e in entries if e["id"] not in used]
    tags_today = {
        primary_tag(by_id[p.content_id]) for p in state.on_date(day) if p.content_id in by_id
    }
    recent = [
        p.text or by_id[p.content_id]["text"]
        for p in state.published()[-recent_posts:]
        if p.text or p.content_id in by_id
    ]

    def eligible(pool: list[dict[str, Any]], tag_rule: bool = True) -> list[dict[str, Any]]:
        out = [e for e in pool if not tag_rule or primary_tag(e) not in tags_today]
        if wanted_tags:
            out = [e for e in out if wanted_tags & set(e.get("tags", []))]
        return out

    # Unused entries first; only near the very end of a bank may the same-day tag rule have
    # to be relaxed, which is still better than repeating an entry early.
    for tag_rule in (True, False):
        candidates = eligible(unused, tag_rule)
        rng.shuffle(candidates)
        for entry in candidates:
            if not is_near_any(entry["text"], recent, near_threshold):
                return Selection(entry, False, len(unused) - 1)
    # Bank exhausted (or no unused entry matches the topic): recycle the oldest used ones.
    # Every recycled entry is in the history by definition, so compare against a window small
    # enough (half the bank) that the oldest entries can come back.
    window = recent[-min(recent_posts, max(len(entries) // 2, 1)) :]
    old = sorted(eligible([e for e in entries if e["id"] in used]), key=lambda e: used[e["id"]])
    for entry in old:
        age = (day - date.fromisoformat(used[entry["id"]])).days
        if age >= recycle_min_days and not is_near_any(entry["text"], window, near_threshold):
            return Selection(entry, True, len(unused))
    raise ContentError("no eligible content entry")


def choose_template(names: list[str], state: PageState, content_id: str, seed: str) -> str:
    """Rotate templates: one of the two least recently used, never the previous post's and
    never the one this entry had last time (so recycled entries look different)."""
    last_seen = {name: -1 for name in names}
    for i, post in enumerate(state.published()):
        if post.template in last_seen:
            last_seen[post.template] = i
    avoid = {state.last_template(), state.template_for(content_id)}
    options = sorted((n for n in names if n not in avoid), key=lambda n: (last_seen[n], n)) or names
    return random.Random(seed + "|template").choice(options[:2])


def choose_audio_mode(weights: dict[str, float], seed: str) -> str:
    """Weighted audio mode for a reel, deterministic per slot."""
    modes = sorted(weights)
    return random.Random(seed + "|audio").choices(modes, [weights[m] for m in modes])[0]
