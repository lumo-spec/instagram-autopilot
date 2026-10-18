"""Prove that none of the owner's private reference files is, or ever was, in git.

Run before every commit/push (and in CI, where ``references/`` does not exist):
1. ``references/`` is listed in ``.gitignore``;
2. nothing under ``references/`` (or named like a file listed in characters.yaml's references)
   is tracked or staged;
3. no commit on any branch ever contained such a path;
4. when the local ``references/`` folder exists: none of its files' contents appears as a git
   object anywhere in history, even under another name.

Exit code 1 with a plain message on any finding: then STOP and tell the owner, do not push.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def private_names() -> set[str]:
    """File names of the reference sheets listed in characters.yaml."""
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            names.add(Path(node).name)

    for chars in ROOT.glob("pages/*/characters.yaml"):
        walk(yaml.safe_load(chars.read_text(encoding="utf-8")) or {})
    return names


def is_private(path: str, names: set[str]) -> bool:
    p = path.strip().replace("\\", "/")
    return p.startswith("references/") or "/references/" in p or Path(p).name in names


def main() -> int:
    problems: list[str] = []
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if "references/" not in [line.strip() for line in ignore]:
        problems.append(".gitignore does not list references/")
    names = private_names()
    tracked = git("ls-files").splitlines()
    staged = git("diff", "--cached", "--name-only").splitlines()
    problems += [f"tracked: {p}" for p in tracked if is_private(p, names)]
    problems += [f"staged: {p}" for p in staged if is_private(p, names)]
    objects = git("rev-list", "--all", "--objects").splitlines()
    history_paths = {line.split(" ", 1)[1] for line in objects if " " in line}
    problems += [f"in history: {p}" for p in sorted(history_paths) if is_private(p, names)]
    refs = ROOT / "references"
    checked = 0
    if refs.is_dir():
        ids = {line.split(" ", 1)[0] for line in objects}
        for f in sorted(p for p in refs.rglob("*") if p.is_file()):
            blob = git("hash-object", "--", str(f)).strip()
            checked += 1
            if blob in ids:
                problems.append(f"the content of references/{f.name} is in git history")
    for p in problems:
        print(f"PRIVACY PROBLEM: {p}")
    if problems:
        print("STOP: do not push. Tell the owner before doing anything else.")
        return 1
    extra = f"; {checked} local reference files compared by content" if refs.is_dir() else ""
    print(f"privacy check passed: {len(tracked)} tracked files, {len(objects)} objects{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
