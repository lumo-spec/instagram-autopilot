"""Alerts as GitHub issues: one open issue per alert key, owner @mentioned, locked, auto-closed.

Healthy runs send no e-mail: an issue is only opened when the owner needs to act or know. An
alert that is already open gets at most one comment per day (no duplicate issues). Every text
passes the public-safety check again right before it is sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autopilot.ops.alerts import AlertPlan, assert_public_safe
from autopilot.ops.github import GitHub

LABEL = "autopilot-alert"
_MARK = "<!-- autopilot-alert-key: {key} -->"
_MARK_RE = re.compile(r"<!-- autopilot-alert-key: ([a-z0-9-]+) -->")


@dataclass
class SyncResult:
    """What the sync did (alert keys only)."""

    opened: list[str] = field(default_factory=list)
    commented: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)


def open_alerts(gh: GitHub) -> dict[str, dict[str, Any]]:
    """Open alert issues by key."""
    found: dict[str, dict[str, Any]] = {}
    for page in range(1, 6):
        resp = gh.call("GET", f"issues?state=open&labels={LABEL}&per_page=100&page={page}")
        batch = resp.data or []
        for issue in batch:
            if "pull_request" in issue:
                continue
            m = _MARK_RE.search(issue.get("body") or "")
            if m:
                found[m.group(1)] = issue
        if len(batch) < 100:
            break
    return found


def _ensure_label(gh: GitHub) -> None:
    body = {"name": LABEL, "color": "b3261e", "description": "Autopilot needs attention"}
    gh.call("POST", "labels", body, ok=(201, 422))  # 422: already exists


def sync(gh: GitHub, plan: AlertPlan, mention: str, run_url: str, today: str) -> SyncResult:
    """Open, refresh and close alert issues so they match ``plan``."""
    result = SyncResult()
    current = open_alerts(gh)
    raised = {a.key for a in plan.raise_}
    link = f"[the run]({run_url})" if run_url else "the latest run"
    if plan.raise_:
        _ensure_label(gh)
    for alert in plan.raise_:
        text = f"{alert.body}\n\n{mention} — see {link}."
        assert_public_safe(alert.title)
        assert_public_safe(text)
        body = f"{text}\n\n{_MARK.format(key=alert.key)}"  # our own marker (key slug)
        issue = current.get(alert.key)
        if issue is None:
            resp = gh.call(
                "POST", "issues", {"title": alert.title, "body": body, "labels": [LABEL]}
            )
            number = int(resp.data["number"])
            gh.call("PUT", f"issues/{number}/lock", {"lock_reason": "resolved"}, ok=(204,))
            result.opened.append(alert.key)
        elif not str(issue.get("updated_at", "")).startswith(today):
            note = f"Still happening ({today}) — see {link}."
            assert_public_safe(note)
            gh.call("POST", f"issues/{issue['number']}/comments", {"body": note})
            result.commented.append(alert.key)
    to_close = set(plan.resolved) | {
        k for k in current if any(k.startswith(s) for s in plan.scopes)
    }
    for key in sorted(to_close - raised):
        issue = current.get(key)
        if issue is None:
            continue
        number = issue["number"]
        gh.call("POST", f"issues/{number}/comments", {"body": "Resolved automatically."})
        gh.call("PATCH", f"issues/{number}", {"state": "closed", "state_reason": "completed"})
        result.closed.append(key)
    return result
