"""The inbox's memory, kept next to the videos on the owner's PC (``.autopilot/ledger.json``).

It records, per destination, which videos (by SHA-256) were posted or failed, unfinished
publishing attempts ("intents", so a crash never causes a second post), and which slots were
handled. Writes are atomic (temporary file + rename).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autopilot.errors import InboxError

VERSION = 1
_SECTIONS = ("posted", "failed", "attempts", "intents")


class Ledger:
    """Load, change and save the ledger."""

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        self.data: dict[str, Any] = data or {"version": VERSION}
        for key in (*_SECTIONS, "fingerprints", "first_seen", "slots", "invalid"):
            self.data.setdefault(key, {})

    @classmethod
    def load(cls, path: Path) -> Ledger:
        """Read the ledger (a new one when the file does not exist yet)."""
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InboxError(
                f"the ledger {path.name} cannot be read ({exc}); not posting"
            ) from None
        if not isinstance(data, dict) or data.get("version") != VERSION:
            raise InboxError(f"the ledger {path.name} has an unknown format; not posting")
        return cls(path, data)

    def save(self) -> None:
        """Write atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _section(self, name: str, dest: str) -> dict[str, Any]:
        return self.data[name].setdefault(dest, {})  # type: ignore[no-any-return]

    # --- per-destination facts -------------------------------------------------------------
    def posted(self, dest: str) -> dict[str, Any]:
        """Videos posted to ``dest`` (SHA-256 -> details)."""
        return self._section("posted", dest)

    def failed(self, dest: str) -> dict[str, Any]:
        """Videos that failed for ``dest`` (SHA-256 -> details)."""
        return self._section("failed", dest)

    def intent(self, dest: str, sha: str) -> dict[str, Any] | None:
        """The unfinished publishing attempt for this video, if any."""
        value = self._section("intents", dest).get(sha)
        return dict(value) if value is not None else None

    def set_intent(self, dest: str, sha: str, intent: dict[str, Any]) -> None:
        """Record (and immediately save) what an attempt has created so far."""
        self._section("intents", dest)[sha] = intent
        self.save()

    def attempts(self, dest: str, sha: str) -> int:
        """How many slots already tried this video and hit a temporary error."""
        return int(self._section("attempts", dest).get(sha, 0))

    def add_attempt(self, dest: str, sha: str) -> int:
        """Count one more temporary failure; returns the new count."""
        n = self.attempts(dest, sha) + 1
        self._section("attempts", dest)[sha] = n
        return n

    def mark_posted(self, dest: str, sha: str, details: dict[str, Any]) -> None:
        """The video is on ``dest``: never post it there again."""
        self.posted(dest)[sha] = details
        self._section("intents", dest).pop(sha, None)
        self._section("attempts", dest).pop(sha, None)
        self.failed(dest).pop(sha, None)

    def mark_failed(self, dest: str, sha: str, details: dict[str, Any]) -> None:
        """The video cannot go to ``dest`` (until the owner deletes its failed folder)."""
        self.failed(dest)[sha] = details
        self._section("intents", dest).pop(sha, None)
        self._section("attempts", dest).pop(sha, None)

    def forget_cleared_failures(self) -> list[str]:
        """Failures whose failed folder the owner deleted become postable again."""
        cleared = []
        for dest, entries in self.data["failed"].items():
            for sha, info in list(entries.items()):
                if not Path(str(info.get("folder", ""))).exists():
                    del entries[sha]
                    cleared.append(f"{dest}:{info.get('name', sha[:8])}")
        return cleared

    # --- slots -------------------------------------------------------------------------------
    def slot_done(self, key: str) -> bool:
        """True when the slot was already handled (posted, empty, missed, paused, ...)."""
        return key in self.data["slots"]

    def mark_slot(self, key: str, outcome: str, sha: str = "") -> None:
        """Remember how a slot was handled."""
        self.data["slots"][key] = {"outcome": outcome, "video": sha[:16]}

    @property
    def fingerprints(self) -> dict[str, str]:
        """Cache of video hashes (folder/file|size|mtime -> SHA-256)."""
        return self.data["fingerprints"]  # type: ignore[no-any-return]

    @property
    def first_seen(self) -> dict[str, float]:
        """Arrival time of every video seen (SHA-256 -> epoch seconds)."""
        return self.data["first_seen"]  # type: ignore[no-any-return]

    @property
    def invalid(self) -> dict[str, Any]:
        """Invalid drop folders already reported (name -> signature, reason, folder)."""
        return self.data["invalid"]  # type: ignore[no-any-return]
