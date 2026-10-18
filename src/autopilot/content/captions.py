"""Caption building from ``pages/<page>/captions.json``.

Caption = reflective opener, the quote/fact itself (for accessibility), a rotated call to action,
the AI disclosure line for narrated reels, then 3-5 hashtags from rotating pools. The hashtag set
never repeats the previous day's set and never includes a banned tag.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopilot.errors import ContentError
from autopilot.render.layout import display_text

HASHTAG_RE = re.compile(r"^#[a-z0-9_]{2,40}$")


@dataclass(frozen=True)
class Caption:
    """A finished caption and the hashtags it uses."""

    text: str
    hashtags: tuple[str, ...]


def load_caption_kit(path: Path) -> dict[str, Any]:
    """Read and sanity-check a page's captions.json."""
    kit: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    banned = set(kit.get("banned_hashtags", []))
    for name, pool in kit["hashtag_pools"].items():
        for tag in pool:
            if not HASHTAG_RE.match(tag) or tag in banned:
                raise ContentError(f"captions.json pool {name}: bad or banned hashtag {tag}")
    if not kit.get("openers") or not kit.get("calls_to_action"):
        raise ContentError("captions.json needs openers and calls_to_action")
    return kit


def pick_hashtags(
    kit: dict[str, Any],
    entry: dict[str, Any],
    rng: random.Random,
    count: int,
    avoid: frozenset[str],
) -> tuple[str, ...]:
    """Choose ``count`` hashtags: themed ones first, then core; never the ``avoid`` set."""
    pools = kit["hashtag_pools"]
    themed: list[str] = []
    author_pool = kit.get("author_pools", {}).get(entry.get("author") or "")
    if author_pool:
        themed += pools[author_pool]
    for tag in entry.get("tags", []):
        pool = kit.get("tag_pools", {}).get(tag)
        if pool:
            themed += pools[pool]
    banned = set(kit.get("banned_hashtags", []))
    for _ in range(20):
        chosen: list[str] = []
        candidates = list(dict.fromkeys(themed))
        rng.shuffle(candidates)
        for tag in candidates[: max(count // 2, 1)]:
            chosen.append(tag)
        core = [t for t in pools["core"] if t not in chosen]
        rng.shuffle(core)
        chosen += core[: count - len(chosen)]
        result = tuple(sorted(t for t in chosen if t not in banned))
        if frozenset(result) != avoid:
            return result
    raise ContentError("could not find a hashtag set different from yesterday's")


def build_caption(
    kit: dict[str, Any],
    entry: dict[str, Any],
    seed: str,
    hashtag_count: int,
    narrated: bool,
    disclosure: str | None,
    previous_hashtags: frozenset[str],
) -> Caption:
    """Assemble the caption for ``entry`` (deterministic for a given ``seed``)."""
    rng = random.Random(seed)
    quote = display_text(entry["text"])
    if entry.get("author"):
        quote = f"“{quote}” — {entry['author']}"
    parts = [rng.choice(kit["openers"]), quote, rng.choice(kit["calls_to_action"])]
    if narrated and disclosure:
        parts.append(disclosure)
    tags = pick_hashtags(kit, entry, rng, hashtag_count, previous_hashtags)
    parts.append(" ".join(tags))
    return Caption("\n\n".join(parts), tags)
