"""``maintenance.yml`` jobs: token renewal (3 parts), health check, keepalive, media cleanup,
recording the results, and the one-off alert test.

The renewal parts and the health check hold secrets and use only the standard library, PyYAML
and PyNaCl. Recording (state, status page, alerts) holds no secrets.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from autopilot.config import Config
from autopilot.errors import AutopilotError, ValidationError
from autopilot.jobs.common import RunContext, parse_state, read_json, state_path, write_json
from autopilot.jobs.record import RENEWAL_FILE, page_states, update_state, write_status
from autopilot.ops import alerts
from autopilot.ops.alerts import AlertPlan
from autopilot.ops.github import GitHub
from autopilot.ops.gitstore import commit_and_push, open_worktree, read_branch_file, squash_branch
from autopilot.ops.issues import sync
from autopilot.ops.token import (
    PublicKey,
    environment_key,
    pat_expiry,
    refresh_token,
    seal,
    store_secret,
    urllib_fetch,
)
from autopilot.plan import missed_slots
from autopilot.publish.instagram import InstagramClient
from autopilot.state import PageState

TOKEN_SECRET = "IG_ACCESS_TOKEN"  # noqa: S105 (the secret's name, not a value)
SLOT_DATE_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})-")


def env_name(page: str) -> str:
    """GitHub environment holding a page's secrets."""
    return f"page-{page}"


# ----- renewal ------------------------------------------------------------------------------
def run_renew_keys(cfg: Config, gh: GitHub, work: Path) -> dict[str, Any]:
    """Part 1 (renewal key): public keys of every enabled page's environment + key expiry."""
    keys = {}
    for page in cfg.enabled_pages:
        k = environment_key(gh, env_name(page.name))
        keys[page.name] = {"key_id": k.key_id, "key": k.key}
    data = {"pages": keys, "pat_expires_on": pat_expiry(gh)}
    write_json(work / "keys.json", data)
    print(f"renewal: fetched {len(keys)} public key(s)")
    return data


def run_renew_refresh(
    cfg: Config,
    page_name: str,
    repo_dir: Path,
    work: Path,
    token: str,
    today: date,
    fetch: Callable[[str], tuple[int, dict[str, object]]] = urllib_fetch,
    mask: Callable[[str], None] = lambda v: print(f"::add-mask::{v}"),
) -> dict[str, Any]:
    """Part 2 (page token): refresh, mask, seal. Only ciphertext is written."""
    page = cfg.page(page_name)
    out = work / f"sealed-{page.name}.json"
    keys = read_json(work / "keys.json") or {}
    raw = (keys.get("pages") or {}).get(page.name)
    state = parse_state(
        read_branch_file(
            repo_dir, str(cfg.section("github", "state_branch")), state_path(page.name)
        ),
        page.name,
    )
    last = state.token.get("refreshed_on", "")
    data: dict[str, Any] = {"page": page.name}
    if last and date.fromisoformat(last) >= today:
        data["status"] = "skipped"  # Meta: a token must be at least 24 hours old to refresh
    elif not token or not isinstance(raw, dict):
        data["status"] = "failed"
        data["error"] = "token or public key missing"
    else:
        try:
            new = refresh_token(str(cfg.section("graph_api_host")), token, today, fetch)
            mask(new.token)
            data.update(
                status="sealed",
                key_id=PublicKey(str(raw["key_id"]), str(raw["key"])).key_id,
                encrypted=seal(PublicKey(str(raw["key_id"]), str(raw["key"])), new.token),
                expires_on=new.expires_on,
                refreshed_on=today.isoformat(),
            )
        except AutopilotError as exc:
            data.update(status="failed", error=str(exc))
    write_json(out, data)
    print(f"renewal {page.name}: {data['status']}")
    return data


def run_renew_store(cfg: Config, gh: GitHub, work: Path) -> dict[str, Any]:
    """Part 3 (renewal key): store each sealed token in its page environment."""
    results: dict[str, Any] = {}
    for page in cfg.enabled_pages:
        sealed = read_json(work / f"sealed-{page.name}.json")
        if sealed is None:
            results[page.name] = {"status": "failed", "error": "no sealed token"}
            continue
        if sealed.get("status") != "sealed" or sealed.get("page") != page.name:
            results[page.name] = {
                "status": sealed.get("status", "failed"),
                "error": sealed.get("error", ""),
            }
            continue
        try:
            store_secret(
                gh,
                env_name(page.name),
                TOKEN_SECRET,
                str(sealed["key_id"]),
                str(sealed["encrypted"]),
            )
            results[page.name] = {
                "status": "stored",
                "expires_on": sealed["expires_on"],
                "refreshed_on": sealed["refreshed_on"],
            }
        except AutopilotError as exc:
            results[page.name] = {"status": "failed", "error": str(exc)}
    data = {"pages": results}
    write_json(work / "renewal-result.json", data)
    print("renewal stored: " + ", ".join(f"{k} {v['status']}" for k, v in results.items()))
    return data


# ----- health -------------------------------------------------------------------------------
def run_health(cfg: Config, page_name: str, work: Path, client: InstagramClient) -> dict[str, Any]:
    """Page environment: is the token accepted and is the quota readable?"""
    page = cfg.page(page_name)
    data: dict[str, Any] = {"page": page.name, "token_ok": False, "quota_ok": False}
    try:
        data["token_ok"] = client.check_token()
        usage = client.publishing_usage()
        data["quota_ok"] = True
        data["quota_high"] = usage >= int(cfg.section("limits", "quota_stop_at"))
    except AutopilotError as exc:
        data["error"] = str(exc)
    write_json(work / f"health-{page.name}.json", data)
    print(f"health {page.name}: token {'ok' if data['token_ok'] else 'NOT ok'}")
    return data


# ----- recording ----------------------------------------------------------------------------
def maintenance_plan(
    cfg: Config,
    states: dict[str, PageState],
    work: Path,
    now: datetime,
    renewal: dict[str, Any],
) -> AlertPlan:
    """Alerts from renewal results, health checks, token/key expiry and missed slots."""
    plan = AlertPlan()
    today = now.astimezone(cfg.timezone).date()
    warn = cfg.section("alerts")
    result = (read_json(work / "renewal-result.json") or {}).get("pages", {})
    for page in cfg.enabled_pages:
        title, slug = page.title, alerts.page_slug(page.title)
        state = states.get(page.name, PageState(page.name))
        res = result.get(page.name)
        if res is not None:
            if res.get("status") in ("stored", "skipped"):
                plan.resolved.append(f"token-renewal-{slug}")
            else:
                plan.add(alerts.token_renewal_failed(title))
        expires = state.token.get("expires_on", "")
        if expires:
            left = (date.fromisoformat(expires) - today).days
            if left < int(warn["token_days_warning"]):
                plan.add(alerts.token_expiring(title, max(left, 0)))
            else:
                plan.resolved.append(f"token-expiry-{slug}")
        health = read_json(work / f"health-{page.name}.json")
        if health is not None:
            if health.get("token_ok"):
                plan.resolved.append(f"token-check-{slug}")
            else:
                plan.add(alerts.token_check_failed(title))
        since = cfg.section("autopilot", "schedule_enabled_on")
        if cfg.section("autopilot", "schedule_enabled") and since:
            missed = missed_slots(cfg, page, now, state, date.fromisoformat(str(since)))
            if missed:
                plan.add(alerts.missed_slots(title, len(missed)))
            else:
                plan.resolved.append(f"missed-slots-{slug}")
    pat = str(renewal.get("pat_expires_on", ""))
    if pat:
        if (date.fromisoformat(pat) - today).days < int(warn["pat_days_warning"]):
            plan.add(alerts.renewal_key_expiring(pat))
        else:
            plan.resolved.append("renewal-key-expiry")
    retires = cfg.section("graph_api_retires_on")
    if retires:
        days = (date.fromisoformat(str(retires)) - today).days
        if days < int(cfg.section("api_retire_warning_days")):
            plan.add(alerts.api_version_retiring(str(retires)))
    return plan


def run_record_maintenance(
    cfg: Config,
    repo_dir: Path,
    work: Path,
    ctx: RunContext,
    gh: GitHub | None,
    now: datetime | None = None,
    token_set: str = "",
) -> AlertPlan:
    """Write token dates, renewal-key expiry and a heartbeat to the state branch; sync alerts.

    ``token_set``: a page whose token the owner has just set (dates start today, 60 days).
    """
    now = now or datetime.now(UTC)
    today = now.astimezone(cfg.timezone).date()
    result = (read_json(work / "renewal-result.json") or {}).get("pages", {})
    keys = read_json(work / "keys.json") or {}
    tree = open_worktree(
        repo_dir, str(cfg.section("github", "state_branch")), work / "state-branch"
    )
    for page in cfg.enabled_pages:
        res = result.get(page.name, {})
        dates: dict[str, str] = {}
        if res.get("status") == "stored":
            dates = {"refreshed_on": str(res["refreshed_on"]), "expires_on": str(res["expires_on"])}
        elif token_set == page.name:
            dates = {
                "refreshed_on": today.isoformat(),
                "expires_on": (today + timedelta(days=60)).isoformat(),
            }
        if dates:
            update_state(
                cfg,
                tree,
                page.name,
                now,
                lambda s, d=dates: s.token.update(d),
                f"token dates {page.name}",
            )
    renewal_path = tree / RENEWAL_FILE
    renewal = json.loads(renewal_path.read_text(encoding="utf-8")) if renewal_path.is_file() else {}
    if keys.get("pat_expires_on"):
        renewal = {"pat_expires_on": str(keys["pat_expires_on"]), "checked_on": today.isoformat()}

    def heartbeat(root: Path) -> None:
        (root / "status").mkdir(exist_ok=True)
        text = json.dumps(renewal, indent=1, sort_keys=True) + "\n"
        (root / RENEWAL_FILE).write_text(text, encoding="utf-8")
        (root / "status" / "heartbeat.txt").write_text(f"{today.isoformat()}\n", encoding="utf-8")
        write_status(cfg, root, now)

    commit_and_push(
        tree, str(cfg.section("github", "state_branch")), "maintenance heartbeat", heartbeat
    )
    plan = maintenance_plan(cfg, page_states(cfg, tree), work, now, renewal)
    if gh is not None:
        r = sync(
            gh, plan, str(cfg.section("github", "owner_mention")), ctx.run_url, today.isoformat()
        )
        print(f"alerts: opened {len(r.opened)}, closed {len(r.closed)}")
    found = "\n".join(f"- {a.title}" for a in plan.raise_) or "All healthy."
    ctx.summary(f"### Maintenance\n\n{found}")
    return plan


# ----- keepalive, cleanup, alert test --------------------------------------------------------
def run_keepalive(gh: GitHub, workflows: list[str]) -> list[str]:
    """Re-enable the scheduled workflows (GitHub switches them off after 60 idle days)."""
    for wf in workflows:
        if not re.match(r"^[a-z0-9-]+\.yml$", wf):
            raise ValidationError("bad workflow file name")
        gh.call("PUT", f"actions/workflows/{wf}/enable", ok=(204,))
    print(f"keepalive: {len(workflows)} workflow(s) enabled")
    return workflows


def run_cleanup(cfg: Config, repo_dir: Path, work: Path, now: datetime | None = None) -> int:
    """Delete hosted files older than ``hosting.media_keep_days`` and squash the media branch."""
    now = now or datetime.now(UTC)
    keep = int(cfg.section("hosting", "media_keep_days"))
    cutoff = (now.astimezone(cfg.timezone).date() - timedelta(days=keep)).isoformat()
    branch = str(cfg.section("github", "media_branch"))
    tree = open_worktree(repo_dir, branch, work / "media-branch")
    removed = 0

    def apply(root: Path) -> None:
        nonlocal removed
        for f in root.glob("*/*"):
            m = SLOT_DATE_RE.search(f.name)
            if f.is_file() and m and m.group(1) < cutoff:
                f.unlink()
                removed += 1

    commit_and_push(tree, branch, "remove old media", apply)
    squash_branch(tree, branch, "media (history squashed)")
    print(f"cleanup: removed {removed} old file(s); media history squashed")
    return removed


def run_test_alert(cfg: Config, gh: GitHub, ctx: RunContext, close: bool, today: str) -> None:
    """Open (or close) the harmless setup test alert."""
    a = alerts.setup_test_alert()
    plan = AlertPlan(resolved=[a.key]) if close else AlertPlan([a])
    r = sync(gh, plan, str(cfg.section("github", "owner_mention")), ctx.run_url, today)
    print(f"test alert: opened {len(r.opened)}, closed {len(r.closed)}")
