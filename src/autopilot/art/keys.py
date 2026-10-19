"""API keys for art generation: the environment first, then the git-ignored ``.env`` file.

The key is typed by the owner at a hidden prompt (``scripts/set-image-key.ps1``). It is never
printed, logged, put in a URL or written anywhere else by this package.
"""

from __future__ import annotations

import os
from pathlib import Path

from autopilot.config import ROOT
from autopilot.errors import ArtError

IMAGE_KEY = "IMAGE_API_KEY"
CHECK_KEY = "CHECK_API_KEY"  # only needed when the checker uses another provider


def read_env_file(path: Path) -> dict[str, str]:
    """``NAME=value`` lines of a .env file (comments and blank lines ignored)."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_key(name: str, env_file: Path | None = None) -> str:
    """The key called ``name``; raises ArtError with set-up advice when it is missing."""
    value = os.environ.get(name, "").strip()
    if not value:
        value = read_env_file(env_file or ROOT / ".env").get(name, "")
    if not value:
        raise ArtError(
            f"{name} is not set: run scripts\\set-image-key.ps1 in your own PowerShell window"
        )
    return value
