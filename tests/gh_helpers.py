"""Test helpers: a throwaway git 'origin' and an in-memory fake of the GitHub REST API."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autopilot.ops.github import GitHub, Response


def run_git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_origin(tmp: Path) -> tuple[str, Path]:
    """A bare 'origin' plus a clone of main with one commit; returns (origin url, clone)."""
    bare = tmp / "origin.git"
    run_git(tmp, "init", "--quiet", "--bare", "--initial-branch=main", str(bare))
    clone = tmp / "main"
    url = bare.resolve().as_uri()
    run_git(tmp, "clone", "--quiet", url, str(clone))
    (clone / "README.md").write_text("main\n", encoding="utf-8")
    run_git(clone, "add", "README.md")
    run_git(clone, "commit", "--quiet", "-m", "init")
    run_git(clone, "push", "--quiet", "origin", "HEAD:main")
    return url, clone


def clone_of(url: str, dest: Path) -> Path:
    run_git(dest.parent, "clone", "--quiet", url, str(dest))
    return dest


def branch_file(url: str, tmp: Path, branch: str, path: str) -> str | None:
    """Read a file from a branch of the origin (fresh clone)."""
    probe = tmp / f"probe-{branch}-{len(list(tmp.glob('probe-*')))}"
    run_git(tmp, "clone", "--quiet", "--branch", branch, url, str(probe))
    f = probe / path
    return f.read_text(encoding="utf-8") if f.is_file() else None


def commit_count(url: str, tmp: Path, branch: str) -> int:
    probe = tmp / f"count-{branch}-{len(list(tmp.glob('count-*')))}"
    run_git(tmp, "clone", "--quiet", "--branch", branch, url, str(probe))
    return int(run_git(probe, "rev-list", "--count", "HEAD").strip())


@dataclass
class FakeGitHubAPI:
    """Just enough of api.github.com for alerts, renewal and keepalive."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    keys: dict[str, tuple[str, str]] = field(default_factory=dict)  # env -> (key_id, key)
    secrets: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    labels: set[str] = field(default_factory=set)
    pat_expiry: str = "2027-10-01 12:00:00 UTC"
    today: str = "2026-10-18"
    fail: set[str] = field(default_factory=set)

    def client(self, repo: str = "lumo-spec/instagram-autopilot") -> GitHub:
        return GitHub("ghs_test", repo, self.transport)

    def transport(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response:
        assert headers["Authorization"].startswith("Bearer ")
        data = json.loads(body) if body else None
        path = url.split("/repos/lumo-spec/instagram-autopilot", 1)[-1]
        self.calls.append((method, path, data))
        for name in self.fail:
            if name in path:
                return Response(500, None, {})
        if method == "GET" and path == "":
            return Response(200, {}, {"github-authentication-token-expiration": self.pat_expiry})
        if method == "GET" and path.startswith("/issues?"):
            label = re.search(r"labels=([\w-]+)", path)
            found = [
                i
                for i in self.issues
                if i["state"] == "open" and (not label or label.group(1) in i["labels"])
            ]
            return Response(200, found, {})
        if method == "POST" and path == "/issues":
            assert data is not None
            issue = {
                "number": len(self.issues) + 1,
                "title": data["title"],
                "body": data["body"],
                "labels": data["labels"],
                "state": "open",
                "locked": False,
                "comments": [],
                "updated_at": f"{self.today}T10:00:00Z",
            }
            self.issues.append(issue)
            return Response(201, issue, {})
        m = re.match(r"^/issues/(\d+)(/lock|/comments)?$", path)
        if m:
            issue = self.issues[int(m.group(1)) - 1]
            if m.group(2) == "/lock":
                issue["locked"] = True
                return Response(204, None, {})
            if m.group(2) == "/comments":
                assert data is not None
                issue["comments"].append(data["body"])
                return Response(201, {}, {})
            assert data is not None
            issue.update(data)
            return Response(200, issue, {})
        if method == "POST" and path == "/labels":
            assert data is not None
            if data["name"] in self.labels:
                return Response(422, None, {})
            self.labels.add(data["name"])
            return Response(201, {}, {})
        m = re.match(r"^/environments/([\w-]+)/secrets/public-key$", path)
        if m and m.group(1) in self.keys:
            key_id, key = self.keys[m.group(1)]
            return Response(200, {"key_id": key_id, "key": key}, {})
        m = re.match(r"^/environments/([\w-]+)/secrets/(\w+)$", path)
        if m and method == "PUT":
            assert data is not None
            self.secrets[(m.group(1), m.group(2))] = data
            return Response(201, None, {})
        if re.match(r"^/actions/workflows/[\w.-]+/enable$", path) and method == "PUT":
            return Response(204, None, {})
        return Response(404, None, {})
