"""Dependency licence check (CI): every installed package must have a permissive licence or be
on the reviewed list below with a reason. Unknown or new copyleft licences fail the check.

Usage: ``python scripts/check_licenses.py`` (inside the project environment).
"""

from __future__ import annotations

import re
import sys
from importlib import metadata

PERMISSIVE = re.compile(
    r"\b(MIT|BSD|Apache|ISC|PSF|Python Software Foundation|MPL|Mozilla|Unlicense|CC0|"
    r"Public Domain|Zlib|HPND|0BSD|Historical Permission|NVIDIA|Expat|Unicode|UPL)\b",
    re.I,
)
COPYLEFT = re.compile(r"GPL|Affero|EUPL|SSPL|copyleft", re.I)

# Reviewed exceptions: used unmodified at build time on GitHub's runners or the owner's PC; this
# repository does not redistribute them. Generated images/audio are not covered by these licences.
REVIEWED = {
    "espeakng-loader": "bundles espeak-ng (GPL-3.0-or-later): phoneme fallback for Kokoro TTS",
    "phonemizer-fork": "GPL-3.0-or-later: wrapper around espeak-ng used by misaki (Kokoro G2P)",
    "num2words": "LGPL-2.1: number-to-words in the TTS text front end",
}


def licence_of(dist: metadata.Distribution) -> str:
    """Best licence description from a package's metadata."""
    m = dist.metadata
    expr = (m.get("License-Expression") or "").strip()
    if expr:
        return expr
    classifiers = [
        c.split("::")[-1].strip()
        for c in (m.get_all("Classifier") or [])
        if c.startswith("License")
    ]
    if classifiers:
        return "; ".join(classifiers)
    raw = (m.get("License") or "").strip()
    return raw.splitlines()[0][:80] if raw else ""


def main() -> int:
    problems = []
    count = 0
    for dist in sorted(metadata.distributions(), key=lambda d: str(d.metadata["Name"]).lower()):
        name = str(dist.metadata["Name"]).lower().replace("_", "-")
        lic = licence_of(dist)
        count += 1
        if name in REVIEWED:
            continue
        if not lic or COPYLEFT.search(lic) or not PERMISSIVE.search(lic):
            problems.append(f"{name}: {lic or 'no licence metadata'}")
    for p in problems:
        print(f"REVIEW NEEDED {p}")
    print(
        f"licence check: {count} packages, {len(REVIEWED)} reviewed exceptions, "
        f"{len(problems)} problem(s)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
