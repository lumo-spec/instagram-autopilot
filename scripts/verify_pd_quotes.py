"""Verify every public-domain quote against its Project Gutenberg source text.

Downloads each source once into a cache directory (default ``.cache/pd-sources``, git-ignored),
restricts the search to the author's own text where a line range or start marker is given, and
checks that each quote appears verbatim after normalising whitespace, dashes and quote marks.

Usage:  python scripts/verify_pd_quotes.py [--cache DIR]
Exit code 0 when every quote is found, 1 otherwise. Network access: www.gutenberg.org only.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUOTES_FILE = ROOT / "pages" / "wisdom" / "sources" / "public_domain.yaml"
GUTENBERG_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"


def normalise(text: str) -> str:
    """Collapse whitespace, drop footnote markers, unify quotes and dashes for comparison."""
    text = re.sub(r"\[\d+\]", "", text)
    text = text.replace("’", "'").replace("‘", "'").replace("�", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("—", "--").replace("–", "-")
    return " ".join(text.split()).lower()


def fetch(gid: int, cache: Path) -> str:
    """Return the Gutenberg text for ``gid``, downloading it into ``cache`` if needed."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"pg{gid}.txt"
    if not path.exists():
        url = GUTENBERG_URL.format(id=gid)
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https host
            path.write_bytes(resp.read())
    return path.read_text(encoding="utf-8", errors="replace")


def region(text: str, spec: dict[str, object]) -> str:
    """Cut the source down to the author's own words using ``lines`` or ``start_marker``."""
    lines = spec.get("lines")
    if isinstance(lines, list):
        start, end = int(lines[0]), int(lines[1])
        return "\n".join(text.splitlines()[start - 1 : end])
    marker = spec.get("start_marker")
    if isinstance(marker, str):
        idx = text.find(marker)
        if idx < 0:
            raise SystemExit(f"start marker not found: {marker!r}")
        return text[idx:]
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "pd-sources")
    args = parser.parse_args()
    data = yaml.safe_load(QUOTES_FILE.read_text(encoding="utf-8"))
    bodies: dict[str, str] = {}
    for key, spec in data["sources"].items():
        bodies[key] = normalise(region(fetch(int(spec["gutenberg_id"]), args.cache), spec))
    missing = [q for q in data["quotes"] if normalise(q["text"]) not in bodies[q["src"]]]
    total = len(data["quotes"])
    for q in missing:
        print(f"NOT FOUND [{q['src']}]: {q['text']}")
    print(f"{total - len(missing)}/{total} quotes verified against their sources")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
