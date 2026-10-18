"""Build ``<bank-dir>/content.json`` for a quote bank from its reviewed source files.

Sources (both under ``<bank-dir>/sources/``):
  originals.txt        "primary-tag, other-tags | text" lines (no attribution)
  public_domain.yaml   verified public-domain quotes (see scripts/verify_pd_quotes.py)

IDs are derived from the normalised text, so rebuilding keeps them stable. Near-duplicates are
removed (public-domain quotes win, then earlier lines) and reported. The synonym map in
``sources/synonyms.yaml`` is copied into the bank.

Usage:  python scripts/build_bank.py <bank-dir> [--name NAME] [--status verified|archived]
e.g.    python scripts/build_bank.py pages/simplestorymode/archive --name wisdom --status archived
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autopilot.config import load_config  # noqa: E402
from autopilot.content.similarity import compare, normalise  # noqa: E402
from autopilot.content.validate import length_class, validate_bank  # noqa: E402


def entry_id(prefix: str, text: str) -> str:
    """Stable id from the normalised text."""
    return f"{prefix}-{hashlib.sha256(normalise(text).encode()).hexdigest()[:8]}"


def read_originals(path: Path, prefix: str) -> list[dict[str, Any]]:
    """Parse originals.txt into entries."""
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tags_part, text = (part.strip() for part in line.split("|", 1))
        tags = [t.strip() for t in tags_part.split(",") if t.strip()]
        entries.append(
            {
                "id": entry_id(prefix, text),
                "text": text,
                "author": None,
                "tags": tags,
                "length_class": length_class(text),
                "status": "verified",
                "source": None,
            }
        )
    return entries


def read_public_domain(path: Path, prefix: str) -> list[dict[str, Any]]:
    """Parse public_domain.yaml into entries with source notes."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = []
    for q in data["quotes"]:
        src = data["sources"][q["src"]]
        entries.append(
            {
                "id": entry_id(prefix, q["text"]),
                "text": q["text"],
                "author": src["author"],
                "tags": q["tags"],
                "length_class": length_class(q["text"]),
                "status": "verified",
                "source": {
                    "work": src["work"],
                    "gutenberg_id": src["gutenberg_id"],
                    "note": "Verified verbatim against the Project Gutenberg text",
                },
            }
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank_dir", type=Path)
    parser.add_argument(
        "--name", default="wisdom", help="bank name (its first letter prefixes ids)"
    )
    parser.add_argument("--status", default="verified", choices=("verified", "archived"))
    args = parser.parse_args()
    cfg = load_config()
    bank_dir = args.bank_dir if args.bank_dir.is_absolute() else ROOT / args.bank_dir
    page_name, page_type = args.name, "quotes"
    max_chars = int(cfg.section("limits", "text_max_chars", page_type))
    threshold = float(cfg.section("near_duplicate", "threshold"))
    src_dir = bank_dir / "sources"
    prefix = page_name[0]
    candidates = read_public_domain(src_dir / "public_domain.yaml", prefix) + read_originals(
        src_dir / "originals.txt", prefix
    )
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for cand in candidates:
        hit = next((k for k in kept if compare(cand["text"], k["text"], threshold)), None)
        if hit:
            removed.append(f"  - {cand['text']}\n      ~ {hit['text']}")
        else:
            kept.append(cand)
    synonyms = yaml.safe_load((src_dir / "synonyms.yaml").read_text(encoding="utf-8"))
    bank: dict[str, Any] = {"page": page_name, "type": page_type, "synonyms": synonyms}
    report = validate_bank({**bank, "entries": kept}, max_chars, threshold)
    for entry in kept:
        entry["status"] = args.status
    if args.status == "archived":
        bank["note"] = "ARCHIVED 2026-10-18: not postable on any page."
    bank["entries"] = kept
    for eid, problems in report.errors.items():
        text = next(e["text"] for e in kept if e["id"] == eid)
        print(f"INVALID {eid}: {', '.join(problems)} :: {text}")
    out = bank_dir / "content.json"
    out.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    attributed = sum(1 for e in kept if e["author"])
    print(f"Near-duplicates removed: {len(removed)}")
    print("\n".join(removed))
    print(
        f"Wrote {out.relative_to(ROOT)}: {len(kept)} entries "
        f"({len(kept) - attributed} original, {attributed} public-domain); "
        f"invalid={len(report.errors)}"
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
