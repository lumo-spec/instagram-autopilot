"""Per-page runtime state (``pages/<page>/state.json`` on the ``state`` branch).

State holds only public, non-secret facts: what was posted when, with which content and format,
plus token dates. Never tokens, user IDs, API error bodies or anything else sensitive.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

STATUS_INTENT = "intent"
STATUS_PUBLISHED = "published"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclass
class PostRecord:
    """One slot's outcome."""

    slot_key: str
    date: str
    slot: str
    status: str
    format: str = ""
    audio_mode: str = ""
    fallback: str = ""
    content_id: str = ""
    template: str = ""
    hashtags: list[str] = field(default_factory=list)
    container_id: str = ""
    media_id: str = ""
    permalink: str = ""
    recorded_at: str = ""
    text: str = ""  # only for exact-text on-demand posts (not in the bank)
    slides: int = 0  # carousel slide count


@dataclass
class PageState:
    """All runtime state for one page."""

    page: str
    posts: list[PostRecord] = field(default_factory=list)
    token: dict[str, str] = field(default_factory=dict)  # dates only: refreshed_on, expires_on

    # ----- queries -------------------------------------------------------------------------
    def get(self, slot_key: str) -> PostRecord | None:
        """Latest record for a slot key."""
        for rec in reversed(self.posts):
            if rec.slot_key == slot_key:
                return rec
        return None

    def is_done(self, slot_key: str) -> bool:
        """True when the slot was published or deliberately skipped."""
        rec = self.get(slot_key)
        return rec is not None and rec.status in (STATUS_PUBLISHED, STATUS_SKIPPED)

    def published(self) -> list[PostRecord]:
        """Published posts in order."""
        return [p for p in self.posts if p.status == STATUS_PUBLISHED]

    def last_used(self) -> dict[str, str]:
        """Content id -> most recent publish date."""
        used: dict[str, str] = {}
        for p in self.published():
            if p.content_id:
                used[p.content_id] = p.date
        return used

    def on_date(self, day: date) -> list[PostRecord]:
        """Published posts on a local date."""
        iso = day.isoformat()
        return [p for p in self.published() if p.date == iso]

    def previous_hashtags(self, day: date) -> frozenset[str]:
        """Hashtags of the most recent post before ``day`` (to avoid repeating the set)."""
        earlier = [p for p in self.published() if p.date < day.isoformat() and p.hashtags]
        return frozenset(earlier[-1].hashtags) if earlier else frozenset()

    def last_template(self) -> str:
        """Template of the latest published post."""
        pubs = self.published()
        return pubs[-1].template if pubs else ""

    def template_for(self, content_id: str) -> str:
        """Template used the last time ``content_id`` was posted."""
        for p in reversed(self.published()):
            if p.content_id == content_id:
                return p.template
        return ""

    # ----- updates -------------------------------------------------------------------------
    def upsert(self, rec: PostRecord) -> None:
        """Replace the record for ``rec.slot_key`` or append it."""
        for i, existing in enumerate(self.posts):
            if existing.slot_key == rec.slot_key:
                self.posts[i] = rec
                return
        self.posts.append(rec)

    # ----- persistence ---------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialise deterministically."""
        return json.dumps(asdict(self), indent=1, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PageState:
        """Build from parsed JSON, ignoring unknown fields."""
        known = set(PostRecord.__dataclass_fields__)
        posts = [
            PostRecord(**{k: v for k, v in p.items() if k in known}) for p in data.get("posts", [])
        ]
        return cls(page=str(data["page"]), posts=posts, token=dict(data.get("token", {})))


def used_post_ids(state: PageState, except_slot: str = "") -> set[str]:
    """Posts that are published or may be (an attempt whose outcome is uncertain)."""
    used: set[str] = set()
    for rec in state.posts:
        if not rec.content_id or rec.slot_key == except_slot or rec.status == STATUS_SKIPPED:
            continue
        if rec.status == STATUS_FAILED and not rec.container_id:
            continue  # nothing reached Instagram: the post may be used again
        used.add(rec.content_id)
    return used


def load_state(path: Path, page: str) -> PageState:
    """Read a state file, or start empty when it does not exist yet."""
    if not path.exists():
        return PageState(page=page)
    return PageState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_state(path: Path, state: PageState) -> None:
    """Write a state file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(state.to_json(), encoding="utf-8")
    tmp.replace(path)
