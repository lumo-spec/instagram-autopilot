"""Fail CI on unsafe GitHub Actions workflow patterns (spec section 16).

Checks every ``.github/workflows/*.yml``:
- top-level ``permissions: {}``; job permissions only from the allowed set;
- no ``pull_request_target``; no ``schedule`` trigger while ``autopilot.schedule_enabled`` is
  false in config.yaml (schedules stay OFF until the live tests pass);
- every remote action pinned to a full commit SHA;
- ``step-security/harden-runner`` is the first step of every job with steps;
- no ``${{ inputs.* }}``, ``${{ github.event.* }}``, ``${{ github.head_ref }}`` or
  ``${{ secrets.* }}`` inside ``run:`` (pass them through ``env:``);
- secrets only in jobs whose environment holds them; no ``secrets: inherit``/``toJSON(secrets)``;
  a job calling ``_pipeline.yml`` may pass the page secrets by name (GitHub hands environment
  secrets to a called workflow only when the caller passes them) and nothing else;
- jobs with an environment install only the hashed minimal requirement files (never the render
  stack) and hold no write permission;
- ``actions/checkout`` keeps credentials only in jobs that need to push;
- no ``set -x``.

Usage: ``python scripts/audit_workflows.py [workflow-dir] [--config config.yaml]``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
HARDEN = "step-security/harden-runner@"
UNSAFE_EXPR = re.compile(
    r"\$\{\{\s*(inputs\.|github\.event\.|github\.head_ref|secrets\.|toJSON\(secrets)"
)
SECRET_REF = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
ENV_SECRETS = {
    "page-": {"IG_ACCESS_TOKEN", "IG_USER_ID"},
    "renewal": {"GH_SECRETS_PAT"},
    "art-generation": {"IMAGE_API_KEY", "CHECK_API_KEY"},
}
# Secrets a caller may pass by name to a local reusable workflow. They resolve only inside the
# called job whose environment holds them (GitHub: "The caller workflow must still pass the
# secret... You can pass a secret by name even if it only exists in the environment").
CALLED_SECRETS = {"./.github/workflows/_pipeline.yml": ENV_SECRETS["page-"]}
ALLOWED_PERMS = {
    ("contents", "read"),
    ("contents", "write"),
    ("issues", "write"),
    ("actions", "write"),
    ("actions", "read"),
    ("pull-requests", "read"),
}
WRITE_PERMS_JOBS = re.compile(r"^(record|host|keepalive|cleanup|alert)")
MINIMAL_REQS = ("requirements-publish.txt", "requirements-renew.txt")


def _triggers(wf: dict[Any, Any]) -> dict[str, Any]:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(t): None for t in on}
    return dict(on or {})


def _env_name(job: dict[str, Any]) -> str:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return str(env or "")


def _secrets_for(env: str) -> set[str]:
    for prefix, names in ENV_SECRETS.items():
        if env == prefix or (prefix.endswith("-") and env.startswith(prefix)):
            return names
    return set()


def audit_job(name: str, job: dict[str, Any], problems: list[str], where: str) -> None:
    """Checks for one job."""
    perms = job.get("permissions", {})
    if perms in ("read-all", "write-all") or not isinstance(perms, dict):
        problems.append(f"{where}: job {name} must list permissions explicitly")
        perms = {}
    for scope, level in perms.items():
        if (scope, level) not in ALLOWED_PERMS:
            problems.append(f"{where}: job {name} has disallowed permission {scope}: {level}")
        # A call to a local reusable workflow only caps the permissions; the called workflow's
        # own jobs are audited individually.
        if level == "write" and "uses" not in job and not WRITE_PERMS_JOBS.match(name):
            problems.append(f"{where}: job {name} may not have {scope}: write")
    env = _env_name(job)
    if env and any(v == "write" for v in perms.values()):
        problems.append(f"{where}: job {name} holds secrets and a write permission")
    text = yaml.safe_dump(job)
    if "secrets: inherit" in text or re.search(r"toJSON\(\s*secrets", text):
        problems.append(f"{where}: job {name} passes all secrets")
    passed = job.get("secrets") if "uses" in job else None
    if isinstance(passed, dict):
        allowed = CALLED_SECRETS.get(str(job["uses"]), set())
        for key, value in passed.items():
            if key not in allowed or value != f"${{{{ secrets.{key} }}}}":
                problems.append(f"{where}: job {name} may not pass secret {key} to {job['uses']}")
        text = yaml.safe_dump({k: v for k, v in job.items() if k != "secrets"})
    for used in SECRET_REF.findall(text):
        if used not in {"GITHUB_TOKEN", *_secrets_for(env)}:
            problems.append(f"{where}: job {name} uses secret {used} outside its environment")
    if "uses" in job:  # calls a reusable workflow
        if not str(job["uses"]).startswith("./"):
            problems.append(f"{where}: job {name} calls a remote reusable workflow")
        return
    steps = job.get("steps") or []
    if not steps or not str(steps[0].get("uses", "")).startswith(HARDEN):
        problems.append(f"{where}: job {name} must start with step-security/harden-runner")
    pushes = any(v == "write" for k, v in perms.items() if k == "contents")
    for i, step in enumerate(steps):
        uses = str(step.get("uses", ""))
        if uses and not uses.startswith("./") and not PINNED.match(uses):
            problems.append(f"{where}: job {name} step {i + 1} action not pinned to a SHA: {uses}")
        if uses.startswith("actions/checkout@"):
            persist = (step.get("with") or {}).get("persist-credentials", True)
            if persist not in (False, "false") and not pushes:
                problems.append(f"{where}: job {name} checkout must set persist-credentials: false")
        run = str(step.get("run", ""))
        if UNSAFE_EXPR.search(run):
            problems.append(f"{where}: job {name} step {i + 1} interpolates untrusted data in run")
        if re.search(r"(^|\s)set\s+-[a-z]*x", run):
            problems.append(f"{where}: job {name} step {i + 1} uses set -x")
        if env and run:
            installs = re.findall(r"(uv sync|uv pip install|pip install[^\n]*)", run)
            for cmd in installs:
                ok = "--require-hashes" in cmd and any(r in cmd for r in MINIMAL_REQS)
                if not ok:
                    problems.append(
                        f"{where}: job {name} (environment {env}) installs more than the "
                        "minimal hashed requirements"
                    )
            if re.search(r"requirements-render|pillow|torch|kokoro", run, re.I):
                problems.append(f"{where}: job {name} (environment {env}) installs render deps")


def audit(workflow_dir: Path, schedule_allowed: bool) -> list[str]:
    """All problems found (empty = pass)."""
    problems: list[str] = []
    files = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    if not files:
        return [f"no workflows found in {workflow_dir}"]
    for path in files:
        where = path.name
        try:
            wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            problems.append(f"{where}: not valid YAML")
            continue
        if wf.get("permissions") != {}:
            problems.append(f"{where}: top-level permissions must be {{}}")
        triggers = _triggers(wf)
        if "pull_request_target" in triggers:
            problems.append(f"{where}: pull_request_target is not allowed")
        if "schedule" in triggers and not schedule_allowed:
            problems.append(
                f"{where}: has a schedule but autopilot.schedule_enabled is false in config.yaml"
            )
        for name, job in (wf.get("jobs") or {}).items():
            audit_job(str(name), job or {}, problems, where)
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workflows", nargs="?", default=".github/workflows")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    allowed = bool((cfg.get("autopilot") or {}).get("schedule_enabled"))
    problems = audit(Path(args.workflows), allowed)
    for p in problems:
        print(f"FAIL {p}")
    print("workflow audit: " + ("passed" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
