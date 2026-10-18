"""Schema checks for quote and fact banks (``pages/<page>/content.json``).

Entries that fail validation can never be posted: :func:`postable_entries` filters them out,
and ``python -m autopilot validate`` exits non-zero when any entry is invalid.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from autopilot.content.similarity import Match, find_duplicates
from autopilot.errors import ContentError

ID_RE = re.compile(r"^[a-z]{1,3}-[0-9a-f]{8}$")
TAG_RE = re.compile(r"^[a-z][a-z-]{1,30}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LENGTH_CLASSES = ("short", "medium", "long")
# Characters allowed in posted text: letters, digits, common punctuation and typographic quotes.
TEXT_RE = re.compile(r"^[A-Za-z0-9 .,;:!?'\"()\-—–‘’“”°%/]+$")


@dataclass
class BankReport:
    """Result of validating a whole bank."""

    total: int = 0
    errors: dict[str, list[str]] = field(default_factory=dict)
    duplicates: list[Match] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no entry is invalid and no near-duplicates remain."""
        return not self.errors and not self.duplicates


def length_class(text: str) -> str:
    """Classify text length for template choice."""
    n = len(text)
    return "short" if n <= 70 else "medium" if n <= 130 else "long"


def _check_common(entry: dict[str, Any], max_chars: int) -> list[str]:
    problems: list[str] = []
    if not isinstance(entry.get("id"), str) or not ID_RE.match(entry["id"]):
        problems.append("id missing or malformed")
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        problems.append("text empty")
    else:
        if len(text) > max_chars:
            problems.append(f"text longer than {max_chars} characters")
        if not TEXT_RE.match(text):
            problems.append("text contains characters outside the allowed set")
        if text != text.strip() or "  " in text:
            problems.append("text has stray whitespace")
    if entry.get("status") != "verified":
        problems.append("status is not 'verified'")
    tags = entry.get("tags")
    if (
        not isinstance(tags, list)
        or not tags
        or not all(isinstance(t, str) and TAG_RE.match(t) for t in tags)
    ):
        problems.append("tags missing or malformed")
    return problems


def check_quote(entry: dict[str, Any], max_chars: int) -> list[str]:
    """Return problems with one quote entry (empty list when valid)."""
    problems = _check_common(entry, max_chars)
    author = entry.get("author")
    if author is not None:
        if not isinstance(author, str) or not author.strip():
            problems.append("author must be null or a name")
        src = entry.get("source")
        if not isinstance(src, dict) or not src.get("work"):
            problems.append("attributed quote needs a source note")
    if entry.get("length_class") not in LENGTH_CLASSES:
        problems.append("length_class invalid")
    return problems


def _is_reference(src: Any) -> bool:
    if not isinstance(src, dict) or not src.get("title") or not src.get("url"):
        return False
    url = urlparse(str(src["url"]))
    return url.scheme == "https" and bool(url.netloc)


def check_fact(entry: dict[str, Any], max_chars: int) -> list[str]:
    """Return problems with one fact entry (empty list when valid)."""
    problems = _check_common(entry, max_chars)
    sources = entry.get("sources")
    good = [s for s in sources if _is_reference(s)] if isinstance(sources, list) else []
    hosts = {urlparse(str(s["url"])).netloc.removeprefix("www.") for s in good}
    if len(good) < 2 or len(hosts) < 2:
        problems.append("needs at least two independent sources (title + https URL)")
    if not isinstance(entry.get("checked_on"), str) or not DATE_RE.match(entry["checked_on"]):
        problems.append("checked_on must be YYYY-MM-DD")
    if not isinstance(entry.get("verification_note"), str) or not entry["verification_note"]:
        problems.append("verification_note missing")
    return problems


def load_bank(path: Path) -> dict[str, Any]:
    """Read a content bank JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentError(f"cannot read content bank {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ContentError(f"{path}: expected an object with an 'entries' list")
    return data


def validate_bank(data: dict[str, Any], max_chars: int, threshold: float) -> BankReport:
    """Validate every entry and look for near-duplicates across the whole bank."""
    checker = check_fact if data.get("type") == "facts" else check_quote
    report = BankReport(total=len(data["entries"]))
    seen: set[str] = set()
    for entry in data["entries"]:
        eid = str(entry.get("id"))
        problems = checker(entry, max_chars)
        if eid in seen:
            problems.append("duplicate id")
        seen.add(eid)
        if problems:
            report.errors[eid] = problems
    texts = [(str(e.get("id")), str(e.get("text", ""))) for e in data["entries"]]
    report.duplicates = find_duplicates(texts, threshold)
    return report


def postable_entries(data: dict[str, Any], max_chars: int) -> list[dict[str, Any]]:
    """Entries that pass validation; anything else can never be posted."""
    checker = check_fact if data.get("type") == "facts" else check_quote
    return [e for e in data["entries"] if not checker(e, max_chars)]
