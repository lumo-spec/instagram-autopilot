"""Git helpers for the runtime branches (``state`` and ``media``).

Writes never force-push a shared branch: a change is applied to a fresh copy of the branch tip,
committed and pushed; when the push is rejected because someone else pushed first, the branch is
fetched again and the same change is re-applied on top (up to ``retries`` times, random delay).
Re-applying (instead of a textual rebase) means JSON state files never need a merge.
"""

from __future__ import annotations

import os
import random
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from autopilot.errors import AutopilotError

BOT = ("autopilot[bot]", "41898282+github-actions[bot]@users.noreply.github.com")


class GitError(AutopilotError):
    """A git command failed (message holds the command name only, never output)."""


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git quietly; output is captured and never printed (it could echo remote URLs)."""
    cmd = ["git", "-c", f"user.name={BOT[0]}", "-c", f"user.email={BOT[1]}", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise GitError(f"git {args[0]} failed (exit {proc.returncode})")
    return proc


def fetch_branch(repo: Path, branch: str) -> bool:
    """Fetch ``origin/<branch>``; False when the branch does not exist yet."""
    ref = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    return git(repo, "fetch", "--quiet", "--depth=1", "origin", ref, check=False).returncode == 0


def read_branch_file(repo: Path, branch: str, path: str) -> str | None:
    """Contents of ``path`` on ``origin/<branch>`` (None if the branch or file is missing)."""
    if not fetch_branch(repo, branch):
        return None
    proc = git(repo, "show", f"origin/{branch}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def open_worktree(repo: Path, branch: str, dest: Path) -> Path:
    """A working copy of ``branch`` at ``dest`` (an empty orphan branch if it does not exist)."""
    # Detached HEAD (pushed with HEAD:<branch>), so several copies of a branch can coexist.
    if dest.exists():
        git(repo, "worktree", "remove", "--force", str(dest), check=False)
    git(repo, "worktree", "prune", check=False)
    if fetch_branch(repo, branch):
        git(repo, "worktree", "add", "--quiet", "--detach", str(dest), f"origin/{branch}")
    else:
        git(repo, "worktree", "add", "--quiet", "--detach", str(dest))
        local = f"new-{branch}-{os.getpid()}-{dest.name}"
        git(dest, "checkout", "--quiet", "--orphan", local)
        git(dest, "rm", "-r", "--quiet", "--force", "--ignore-unmatch", ".")
    return dest


def _reset_to_remote(worktree: Path, branch: str) -> None:
    if fetch_branch(worktree, branch):
        git(worktree, "reset", "--quiet", "--hard", f"origin/{branch}")
    git(worktree, "clean", "-fdq")


def _unpushed(worktree: Path, branch: str) -> bool:
    """True when the local branch has commits the remote branch does not have."""
    if git(worktree, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
        return False  # empty orphan branch: nothing committed yet
    remote = f"refs/remotes/origin/{branch}"
    if git(worktree, "rev-parse", "--verify", "--quiet", remote, check=False).returncode != 0:
        return True  # the branch does not exist on the remote yet
    ahead = git(worktree, "rev-list", "--count", f"{remote}..HEAD").stdout.strip()
    return ahead != "0"


def commit_and_push(
    worktree: Path,
    branch: str,
    message: str,
    apply: Callable[[Path], None],
    retries: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Apply a change, commit and push; returns the pushed commit SHA ("" if nothing changed)."""
    for attempt in range(retries):
        apply(worktree)
        git(worktree, "add", "--all")
        if git(worktree, "diff", "--cached", "--quiet", check=False).returncode != 0:
            git(worktree, "commit", "--quiet", "-m", message)
        elif not _unpushed(worktree, branch):
            return ""
        push = git(worktree, "push", "--quiet", "origin", f"HEAD:{branch}", check=False)
        if push.returncode == 0:
            fetch_branch(worktree, branch)
            return git(worktree, "rev-parse", "HEAD").stdout.strip()
        if attempt == retries - 1:
            break
        sleep(random.uniform(1, 4) * (attempt + 1))
        _reset_to_remote(worktree, branch)
    raise GitError(f"could not push to {branch} after {retries} attempts")


def squash_branch(worktree: Path, branch: str, message: str) -> str:
    """Replace the branch history with one commit holding its current files (media cleanup).

    Uses ``--force-with-lease`` so files pushed meanwhile by another run are never lost: the
    push is refused and the caller tries again later.
    """
    tip = git(worktree, "rev-parse", "HEAD").stdout.strip()
    tree = git(worktree, "rev-parse", "HEAD^{tree}").stdout.strip()
    new = git(worktree, "commit-tree", tree, "-m", message).stdout.strip()
    lease = f"--force-with-lease=refs/heads/{branch}:{tip}"
    git(worktree, "push", "--quiet", lease, "origin", f"{new}:refs/heads/{branch}")
    git(worktree, "reset", "--quiet", "--hard", new)
    return new
