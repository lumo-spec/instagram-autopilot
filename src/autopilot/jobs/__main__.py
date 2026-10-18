"""``python -m autopilot.jobs <job>``: the commands the GitHub Actions workflows run.

Workflow inputs arrive only through environment variables (``AP_PAGE``, ``AP_SLOT_KEY``,
``AP_FORMAT``, ``AP_POST_ID``), never interpolated into shell lines, and are re-validated here.
Each command imports only the modules its job may use.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from autopilot.config import load_config
from autopilot.errors import AutopilotError


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _gh(var: str):  # type: ignore[no-untyped-def]
    from autopilot.ops.github import GitHub

    token = _env(var)
    if not token:
        raise AutopilotError(f"{var} is not available to this job")
    repo = _env("GITHUB_REPOSITORY") or str(load_config().section("github", "repo"))
    return GitHub(token, repo)


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915 (one dispatch table)
    """Parse and run one job command."""
    parser = argparse.ArgumentParser(prog="autopilot.jobs")
    parser.add_argument("--work", default="work", help="hand-over folder")
    parser.add_argument("--repo-dir", default=".", help="checkout of main")
    sub = parser.add_subparsers(dest="job", required=True)
    sub.add_parser("plan")
    p = sub.add_parser("build")
    p.add_argument("--page", default=_env("AP_PAGE"))
    p.add_argument("--slot-key", default=_env("AP_SLOT_KEY"))
    p.add_argument("--format", default=_env("AP_FORMAT") or "auto")
    p.add_argument("--post-id", default=_env("AP_POST_ID"))
    sub.add_parser("host")
    p = sub.add_parser("publish")
    p.add_argument("--phase", choices=("prepare", "finish"), required=True)
    sub.add_parser("record")
    sub.add_parser("renew-keys")
    p = sub.add_parser("renew-refresh")
    p.add_argument("--page", default=_env("AP_PAGE"))
    sub.add_parser("renew-store")
    p = sub.add_parser("health")
    p.add_argument("--page", default=_env("AP_PAGE"))
    p = sub.add_parser("record-maintenance")
    p.add_argument("--token-set", default=_env("AP_TOKEN_SET"))
    sub.add_parser("keepalive")
    sub.add_parser("cleanup")
    p = sub.add_parser("test-alert")
    p.add_argument("--close", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config()
    work, repo_dir = Path(args.work), Path(args.repo_dir)
    work.mkdir(parents=True, exist_ok=True)
    from autopilot.jobs.common import RunContext

    ctx = RunContext.from_env(cfg)
    try:
        if args.job == "plan":
            from autopilot.jobs.plan import run_plan

            run_plan(cfg, repo_dir, ctx)
        elif args.job == "build":
            from autopilot.jobs.build import run_build

            data = run_build(
                cfg, repo_dir, work, args.page, args.slot_key, args.format, args.post_id
            )
            print(f"build {data['slot_key']}: {data['status']} {data['post_id']} {data['reason']}")
            ctx.output("slot_key", data["slot_key"])
            ctx.output("status", data["status"])
        elif args.job == "host":
            from autopilot.jobs.host import run_host

            run_host(cfg, repo_dir, work, ctx.repo)
        elif args.job == "publish":
            from autopilot.jobs.publish import run_finish, run_prepare

            (run_prepare if args.phase == "prepare" else run_finish)(cfg, work, ctx.repo)
        elif args.job == "record":
            from autopilot.jobs.record import run_record

            gh = _gh("GITHUB_TOKEN") if _env("GITHUB_TOKEN") else None
            run_record(cfg, repo_dir, work, ctx, gh, publish_job=_env("AP_PUBLISH_JOB"))
        elif args.job == "renew-keys":
            from autopilot.jobs.maintenance import run_renew_keys

            run_renew_keys(cfg, _gh("GH_SECRETS_PAT"), work)
        elif args.job == "renew-refresh":
            from autopilot.jobs.maintenance import run_renew_refresh

            today = datetime.now(UTC).astimezone(cfg.timezone).date()
            run_renew_refresh(cfg, args.page, repo_dir, work, _env("IG_ACCESS_TOKEN"), today)
        elif args.job == "renew-store":
            from autopilot.jobs.maintenance import run_renew_store

            run_renew_store(cfg, _gh("GH_SECRETS_PAT"), work)
        elif args.job == "health":
            from autopilot.jobs.maintenance import run_health
            from autopilot.jobs.publish import client_from_env

            run_health(cfg, args.page, work, client_from_env(cfg))
        elif args.job == "record-maintenance":
            from autopilot.jobs.maintenance import run_record_maintenance

            gh = _gh("GITHUB_TOKEN") if _env("GITHUB_TOKEN") else None
            run_record_maintenance(cfg, repo_dir, work, ctx, gh, token_set=args.token_set)
        elif args.job == "keepalive":
            from autopilot.jobs.maintenance import run_keepalive

            run_keepalive(_gh("GITHUB_TOKEN"), ["autopilot.yml", "maintenance.yml"])
        elif args.job == "cleanup":
            from autopilot.jobs.maintenance import run_cleanup

            run_cleanup(cfg, repo_dir, work)
        elif args.job == "test-alert":
            from autopilot.jobs.maintenance import run_test_alert

            day = datetime.now(UTC).date().isoformat()
            run_test_alert(cfg, _gh("GITHUB_TOKEN"), ctx, args.close, day)
    except AutopilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
