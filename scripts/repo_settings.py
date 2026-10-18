"""Apply and verify the repository's security settings (spec section 16) with the GitHub CLI.

    python scripts/repo_settings.py check   # verify only (exit 1 if anything is wrong)
    python scripts/repo_settings.py apply   # create/update settings, then verify

Uses the owner's own ``gh`` login; no token is read or printed by this script. Settings:
rulesets (main: pull request + passing CI, no force-push/deletion; state: no force-push or
deletion; media: no deletion), environments limited to ``main``, read-only default workflow
token, Actions may not approve pull requests, SHA-pinned actions required, fork pull requests
need approval, secret scanning + push protection, Dependabot alerts, wiki/projects off,
issues on, runtime branches and GitHub Pages (reels) on the media branch.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

REPO = "lumo-spec/instagram-autopilot"
ENVIRONMENTS = ("page-simplestorymode", "renewal")
REQUIRED_CHECK = "ci-ok"
RUNTIME_BRANCHES = {
    "state": "Runtime records and STATUS.md (written by the record job).\n",
    "media": "Temporary media hosting. Files are removed after publishing.\n",
}
RULESETS: dict[str, dict[str, Any]] = {
    "main-protection": {
        "include": ["~DEFAULT_BRANCH"],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [{"context": REQUIRED_CHECK}],
                },
            },
        ],
    },
    "state-branch": {
        "include": ["refs/heads/state"],
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    },
    "media-branch": {"include": ["refs/heads/media"], "rules": [{"type": "deletion"}]},
}


def gh(method: str, path: str, body: dict[str, Any] | None = None, ok404: bool = False) -> Any:
    """Call the GitHub API through the gh CLI (JSON in, JSON out)."""
    cmd = ["gh", "api", "--method", method, path, "-H", "Accept: application/vnd.github+json"]
    if body is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        if ok404 and "404" in proc.stderr + proc.stdout:
            return None
        raise RuntimeError(f"gh api {method} {path} failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def r(path: str) -> str:
    return f"repos/{REPO}{path}"


# ----- apply ----------------------------------------------------------------------------------
def ensure_branch(name: str, readme: str) -> None:
    """Create an orphan branch with one README if it does not exist."""
    if gh("GET", r(f"/git/ref/heads/{name}"), ok404=True):
        return
    blob = gh(
        "POST",
        r("/git/blobs"),
        {"content": base64.b64encode(readme.encode()).decode(), "encoding": "base64"},
    )
    tree = gh(
        "POST",
        r("/git/trees"),
        {
            "tree": [
                {"path": "README.md", "mode": "100644", "type": "blob", "sha": blob["sha"]},
                {
                    "path": ".nojekyll",
                    "mode": "100644",
                    "type": "blob",
                    "sha": gh("POST", r("/git/blobs"), {"content": "", "encoding": "utf-8"})["sha"],
                },
            ]
        },
    )
    commit = gh(
        "POST", r("/git/commits"), {"message": f"create {name}", "tree": tree["sha"], "parents": []}
    )
    gh("POST", r("/git/refs"), {"ref": f"refs/heads/{name}", "sha": commit["sha"]})
    print(f"created branch {name}")


def apply() -> None:
    """Create or update every setting."""
    gh(
        "PATCH",
        r(""),
        {
            "has_wiki": False,
            "has_projects": False,
            "has_issues": True,
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            },
        },
    )
    gh("PUT", r("/vulnerability-alerts"))
    gh(
        "PUT",
        r("/actions/permissions"),
        {"enabled": True, "allowed_actions": "all", "sha_pinning_required": True},
    )
    gh(
        "PUT",
        r("/actions/permissions/workflow"),
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
    )
    gh(
        "PUT",
        r("/actions/permissions/fork-pr-contributor-approval"),
        {"approval_policy": "all_external_contributors"},
    )
    for env in ENVIRONMENTS:
        gh(
            "PUT",
            r(f"/environments/{env}"),
            {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            },
        )
        policies = gh("GET", r(f"/environments/{env}/deployment-branch-policies"))
        names = {p["name"] for p in policies.get("branch_policies", [])}
        for p in policies.get("branch_policies", []):
            if p["name"] != "main":
                gh("DELETE", r(f"/environments/{env}/deployment-branch-policies/{p['id']}"))
        if "main" not in names:
            gh(
                "POST",
                r(f"/environments/{env}/deployment-branch-policies"),
                {"name": "main", "type": "branch"},
            )
    for name, readme in RUNTIME_BRANCHES.items():
        ensure_branch(name, readme)
    existing = {rs["name"]: rs["id"] for rs in gh("GET", r("/rulesets"))}
    for name, spec in RULESETS.items():
        body = {
            "name": name,
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {"ref_name": {"include": spec["include"], "exclude": []}},
            "rules": spec["rules"],
        }
        if name in existing:
            gh("PUT", r(f"/rulesets/{existing[name]}"), body)
        else:
            gh("POST", r("/rulesets"), body)
    pages = gh("GET", r("/pages"), ok404=True)
    source = {"source": {"branch": "media", "path": "/"}}
    if pages is None:
        gh("POST", r("/pages"), source)
    elif pages.get("source") != source["source"]:
        gh("PUT", r("/pages"), source)
    print("settings applied")


# ----- check ----------------------------------------------------------------------------------
def checks() -> list[tuple[str, Callable[[], bool]]]:
    """(description, test) pairs."""

    def repo() -> dict[str, Any]:
        return dict(gh("GET", r("")))

    def sa(key: str) -> bool:
        return repo().get("security_and_analysis", {}).get(key, {}).get("status") == "enabled"

    def ruleset(name: str) -> dict[str, Any] | None:
        for rs in gh("GET", r("/rulesets")):
            if rs["name"] == name:
                return dict(gh("GET", r(f"/rulesets/{rs['id']}")))
        return None

    def rules_ok(name: str) -> bool:
        rs = ruleset(name)
        if not rs or rs.get("enforcement") != "active" or rs.get("bypass_actors"):
            return False
        want = {x["type"] for x in RULESETS[name]["rules"]}
        have = {x["type"] for x in rs.get("rules", [])}
        include = rs.get("conditions", {}).get("ref_name", {}).get("include", [])
        return want <= have and include == RULESETS[name]["include"]

    def required_check() -> bool:
        rs = ruleset("main-protection") or {}
        for rule in rs.get("rules", []):
            if rule["type"] == "required_status_checks":
                ctx = [c["context"] for c in rule["parameters"]["required_status_checks"]]
                return REQUIRED_CHECK in ctx
        return False

    def env_main_only(env: str) -> bool:
        e = gh("GET", r(f"/environments/{env}"), ok404=True)
        if not e or not (e.get("deployment_branch_policy") or {}).get("custom_branch_policies"):
            return False
        pol = gh("GET", r(f"/environments/{env}/deployment-branch-policies"))
        return [(p["name"], p.get("type", "branch")) for p in pol["branch_policies"]] == [
            ("main", "branch")
        ]

    wf = lambda: gh("GET", r("/actions/permissions/workflow"))  # noqa: E731
    out: list[tuple[str, Callable[[], bool]]] = [
        ("repository is public", lambda: repo()["visibility"] == "public"),
        (
            "wiki off, projects off, issues on",
            lambda: not repo()["has_wiki"] and not repo()["has_projects"] and repo()["has_issues"],
        ),
        ("secret scanning on", lambda: sa("secret_scanning")),
        ("push protection on", lambda: sa("secret_scanning_push_protection")),
        (
            "Dependabot alerts on",
            lambda: gh("GET", r("/vulnerability-alerts"), ok404=True) is not None,
        ),
        (
            "default workflow token is read-only",
            lambda: wf()["default_workflow_permissions"] == "read",
        ),
        (
            "Actions cannot approve pull requests",
            lambda: wf()["can_approve_pull_request_reviews"] is False,
        ),
        (
            "actions must be pinned to a full SHA",
            lambda: gh("GET", r("/actions/permissions")).get("sha_pinning_required") is True,
        ),
        (
            "fork pull requests need approval",
            lambda: (
                gh("GET", r("/actions/permissions/fork-pr-contributor-approval"))["approval_policy"]
                == "all_external_contributors"
            ),
        ),
        ("ruleset main: PR + CI, no force-push/deletion", lambda: rules_ok("main-protection")),
        (f"ruleset main requires the '{REQUIRED_CHECK}' check", required_check),
        ("ruleset state: no force-push/deletion", lambda: rules_ok("state-branch")),
        ("ruleset media: no deletion", lambda: rules_ok("media-branch")),
        (
            "GitHub Pages serves the media branch",
            lambda: (
                ((gh("GET", r("/pages"), ok404=True) or {}).get("source") or {}).get("branch")
                == "media"
            ),
        ),
    ]
    out += [
        (f"environment {e} limited to main", lambda e=e: env_main_only(e)) for e in ENVIRONMENTS
    ]
    out += [
        (f"branch {b} exists", lambda b=b: bool(gh("GET", r(f"/git/ref/heads/{b}"), ok404=True)))
        for b in RUNTIME_BRANCHES
    ]
    return out


def secrets_report() -> None:
    """Which environment secrets exist (names only; values are never readable)."""
    for env in ENVIRONMENTS:
        data = gh("GET", r(f"/environments/{env}/secrets"), ok404=True) or {}
        names = sorted(s["name"] for s in data.get("secrets", []))
        print(f"info  {env} secrets set: {', '.join(names) or 'none yet'}")


def check() -> int:
    failed = 0
    for desc, test in checks():
        try:
            ok = bool(test())
        except (RuntimeError, KeyError, TypeError):
            ok = False
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc}")
    secrets_report()
    print(f"repository settings: {'all passed' if not failed else f'{failed} failed'}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["apply"]:
        apply()
        return check()
    if argv[1:] == ["check"]:
        return check()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
