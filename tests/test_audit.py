"""The workflow audit passes on the real workflows and catches every unsafe pattern."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from autopilot.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import audit_workflows

HARDEN = {
    "uses": "step-security/harden-runner@e14015d583714f6e62063499dc959a02595150a1",
    "with": {"egress-policy": "audit"},
}
CHECKOUT = {
    "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "with": {"persist-credentials": False},
}


def good_job(**extra: object) -> dict[str, object]:
    job: dict[str, object] = {
        "runs-on": "ubuntu-24.04",
        "permissions": {"contents": "read"},
        "steps": [HARDEN, CHECKOUT, {"run": "echo hi"}],
    }
    job.update(extra)
    return job


def write(tmp: Path, wf: dict[str, object]) -> Path:
    d = tmp / "wf"
    d.mkdir(exist_ok=True)
    (d / "x.yml").write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return d


def base(**jobs: object) -> dict[str, object]:
    return {"name": "x", "on": {"workflow_dispatch": None}, "permissions": {}, "jobs": jobs}


def test_real_workflows_pass_and_have_no_schedule() -> None:
    assert audit_workflows.audit(ROOT / ".github" / "workflows", schedule_allowed=False) == []
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        assert "schedule" not in (data.get(True) or data.get("on") or {}), wf.name


def test_good_fixture_passes(tmp_path: Path) -> None:
    assert audit_workflows.audit(write(tmp_path, base(a=good_job())), False) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda wf: wf.update(permissions={"contents": "read"}), "top-level permissions"),
        (lambda wf: wf.update(on={"pull_request_target": None}), "pull_request_target"),
        (lambda wf: wf.update(on={"schedule": [{"cron": "0 * * * *"}]}), "has a schedule"),
        (
            lambda wf: wf["jobs"]["a"]["steps"].insert(1, {"uses": "actions/cache@v4"}),
            "not pinned",
        ),
        (lambda wf: wf["jobs"]["a"]["steps"].pop(0), "harden-runner"),
        (
            lambda wf: wf["jobs"]["a"]["steps"].append({"run": "echo ${{ inputs.text }}"}),
            "untrusted data",
        ),
        (
            lambda wf: wf["jobs"]["a"]["steps"].append(
                {"run": "echo ${{ github.event.issue.title }}"}
            ),
            "untrusted data",
        ),
        (lambda wf: wf["jobs"]["a"]["steps"].append({"run": "set -x\necho"}), "set -x"),
        (
            lambda wf: wf["jobs"]["a"]["steps"].append(
                {"env": {"T": "${{ secrets.IG_ACCESS_TOKEN }}"}, "run": "x"}
            ),
            "outside its environment",
        ),
        (
            lambda wf: wf["jobs"]["a"].update(permissions={"contents": "write"}),
            "may not have contents: write",
        ),
        (
            lambda wf: wf["jobs"]["a"].update(permissions={"packages": "write"}),
            "disallowed permission",
        ),
        (
            lambda wf: wf["jobs"]["a"]["steps"].__setitem__(
                1, {"uses": CHECKOUT["uses"], "with": {}}
            ),
            "persist-credentials",
        ),
    ],
)
def test_unsafe_patterns_fail(tmp_path: Path, change, expected: str) -> None:  # type: ignore[no-untyped-def]
    wf = base(a=good_job())
    change(wf)
    problems = audit_workflows.audit(write(tmp_path, wf), False)
    assert any(expected in p for p in problems), problems


def caller_job(**secrets: str) -> dict[str, object]:
    return {
        "uses": "./.github/workflows/_pipeline.yml",
        "permissions": {"contents": "write", "issues": "write"},
        "with": {"page": "simplestorymode"},
        "secrets": secrets,
    }


def test_pipeline_caller_may_pass_page_secrets_by_name(tmp_path: Path) -> None:
    job = caller_job(
        IG_ACCESS_TOKEN="${{ secrets.IG_ACCESS_TOKEN }}", IG_USER_ID="${{ secrets.IG_USER_ID }}"
    )
    assert audit_workflows.audit(write(tmp_path, base(post=job)), False) == []


@pytest.mark.parametrize(
    "secrets",
    [
        {"GH_SECRETS_PAT": "${{ secrets.GH_SECRETS_PAT }}"},  # not a page secret
        {"IG_ACCESS_TOKEN": "${{ secrets.GH_SECRETS_PAT }}"},  # renamed on the way
        {"IG_ACCESS_TOKEN": "${{ toJSON(secrets) }}"},
    ],
)
def test_pipeline_caller_may_not_pass_other_secrets(
    tmp_path: Path, secrets: dict[str, str]
) -> None:
    problems = audit_workflows.audit(write(tmp_path, base(post=caller_job(**secrets))), False)
    assert problems, secrets


def test_only_the_pipeline_may_receive_secrets(tmp_path: Path) -> None:
    job = caller_job(IG_ACCESS_TOKEN="${{ secrets.IG_ACCESS_TOKEN }}")
    job["uses"] = "./.github/workflows/other.yml"
    problems = audit_workflows.audit(write(tmp_path, base(post=job)), False)
    assert any("may not pass secret IG_ACCESS_TOKEN" in p for p in problems), problems


def test_secrets_inherit_still_fails(tmp_path: Path) -> None:
    job = caller_job()
    job["secrets"] = "inherit"
    problems = audit_workflows.audit(write(tmp_path, base(post=job)), False)
    assert any("passes all secrets" in p for p in problems), problems


def test_secret_jobs_install_only_minimal_hashed_requirements(tmp_path: Path) -> None:
    env_job = good_job(
        environment="page-simplestorymode",
        steps=[
            HARDEN,
            CHECKOUT,
            {"run": "pip install --require-hashes --no-deps -r requirements-publish.txt"},
            {"env": {"T": "${{ secrets.IG_ACCESS_TOKEN }}"}, "run": "python -m x"},
        ],
    )
    assert audit_workflows.audit(write(tmp_path, base(publish=env_job)), False) == []
    bad = good_job(
        environment="page-simplestorymode", steps=[HARDEN, CHECKOUT, {"run": "uv sync --locked"}]
    )
    assert any(
        "minimal hashed" in p for p in audit_workflows.audit(write(tmp_path, base(p=bad)), False)
    )
    pat = good_job(
        environment="page-simplestorymode",
        steps=[HARDEN, CHECKOUT, {"env": {"T": "${{ secrets.GH_SECRETS_PAT }}"}, "run": "x"}],
    )
    assert any(
        "outside its environment" in p
        for p in audit_workflows.audit(write(tmp_path, base(p=pat)), False)
    )
    writer = good_job(environment="renewal", permissions={"contents": "write"})
    assert any(
        "secrets and a write permission" in p
        for p in audit_workflows.audit(write(tmp_path, base(record=writer)), False)
    )


def test_schedule_allowed_only_when_config_says_so(tmp_path: Path) -> None:
    wf = base(a=good_job())
    wf["on"] = {"schedule": [{"cron": "17,47 * * * *"}]}
    assert audit_workflows.audit(write(tmp_path, wf), schedule_allowed=True) == []
    assert (
        audit_workflows.main(
            [str(ROOT / ".github" / "workflows"), "--config", str(ROOT / "config.yaml")]
        )
        == 0
    )
