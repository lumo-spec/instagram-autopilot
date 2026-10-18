"""Alerts: plain-English titles that say what is wrong and what to do.

Alerts become public GitHub issues, so wording is generic (page name, problem, fix, run link)
and :func:`assert_public_safe` rejects anything that looks like a token, an ID or an error dump.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKENISH = re.compile(
    r"(IG[A-Za-z0-9_-]{20,}|EAA[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|[A-Za-z0-9_-]{40,})"
)
_LONG_NUMBER = re.compile(r"\d{9,}")
_DUMPISH = re.compile(r"(Traceback|\{\"|error_subcode|fbtrace|access_token|Bearer\s)", re.I)
# Links to this repository's own workflow runs are allowed (run ids are long numbers).
_RUN_LINK = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+(?:/job/\d+)?")


@dataclass(frozen=True)
class Alert:
    """One alert; ``key`` deduplicates (one open issue per key)."""

    key: str
    title: str
    body: str


def assert_public_safe(text: str) -> None:
    """Raise ValueError if ``text`` could leak something sensitive."""
    text = _RUN_LINK.sub("", text)
    if _TOKENISH.search(text) or _LONG_NUMBER.search(text) or _DUMPISH.search(text):
        raise ValueError("alert text failed the public-safety check")


def _slug(page: str) -> str:
    """Alert-key form of a page title, e.g. ``simple-story-mode``."""
    return re.sub(r"[^a-z0-9]+", "-", page.lower()).strip("-")


def _make(key: str, title: str, body: str) -> Alert:
    assert_public_safe(title)
    assert_public_safe(body)
    return Alert(key, title, body)


def low_stock(page: str, left: int, days: float) -> Alert:
    """Content bank running low."""
    return _make(
        f"low-stock-{_slug(page)}",
        f"ACTION NEEDED [{page}]: content running low — open Claude Code and type: Top up {page}",
        f"{page} has {left} unused entries left (about {days:.0f} days). "
        f"Open Claude Code in this repository and type: Top up {page}",
    )


def slot_skipped(page: str, slot: str, reason: str) -> Alert:
    """Every format failed its gates or no content was eligible."""
    return _make(
        f"skipped-{_slug(page)}-{slot}",
        f"[{page}] a post was skipped — check the Status page",
        f"The {slot} post was skipped: {reason}. Nothing flawed was posted. "
        "If this repeats, ask Claude Code to look at the latest run.",
    )


def no_recent_post(page: str, hours: int) -> Alert:
    """Nothing published for too long."""
    return _make(
        f"no-post-{_slug(page)}",
        f"[{page}] no post for {hours}+ hours — check the Status page",
        f"{page} has not published for over {hours} hours. Open the Status page and the latest "
        "Autopilot run.",
    )


def token_renewal_failed(page: str) -> Alert:
    """Weekly token refresh failed."""
    return _make(
        f"token-renewal-{_slug(page)}",
        f"[{page}] token renewal failed — will retry; "
        "if repeated, regenerate the token (README step 6)",
        "The old token is kept and still valid. The renewal is retried next week.",
    )


def token_expiring(page: str, days_left: int) -> Alert:
    """Page token close to expiry."""
    return _make(
        f"token-expiry-{_slug(page)}",
        f"ACTION NEEDED [{page}]: Instagram token expires in {days_left} days — "
        "regenerate it (README step 6)",
        "Automatic renewal has not succeeded. Follow README step 6 to set a fresh token.",
    )


def renewal_key_expiring(expires_on: str) -> Alert:
    """Fine-grained GitHub token close to expiry."""
    return _make(
        "renewal-key-expiry",
        f"ACTION NEEDED: renewal key expires {expires_on} — renew it (README step 5)",
        "Create a new fine-grained token exactly as in README step 5 "
        "and set it with the command shown there.",
    )


def api_version_retiring(retires_on: str) -> Alert:
    """Pinned Graph API version near retirement."""
    return _make(
        "api-version",
        f"Instagram API version retires on {retires_on} — ask Claude Code to upgrade",
        "Open Claude Code in this repository and ask it to follow the upgrade procedure "
        "in CLAUDE.md.",
    )


def queue_low(page: str, left: int) -> Alert:
    """Few approved posts left in a story page's queue."""
    return _make(
        f"queue-low-{_slug(page)}",
        f"ACTION NEEDED [{page}]: only {left} approved posts left — "
        "open Claude Code and type: New post: <topic>",
        f"{page} has {left} approved posts waiting. Write new ones in Claude Code "
        "(New post: <topic>), add the art, then type: Build <post-id>.",
    )


def queue_empty(page: str, slot: str) -> Alert:
    """Nothing approved for a slot: the slot was skipped (never filled with anything else)."""
    return _make(
        f"queue-empty-{_slug(page)}",
        f"ACTION NEEDED [{page}]: no approved post for the {slot} slot — it was skipped",
        f"The {slot} slot had no approved post of the right kind, so nothing was posted. "
        "Add posts in Claude Code (New post: <topic>, then Build <post-id>).",
    )


def approval_changed(page: str, post_id: str) -> Alert:
    """An approved post's files changed after approval, so it will not be posted."""
    return _make(
        f"approval-changed-{_slug(page)}-{post_id}",
        f"[{page}] post {post_id} changed after approval — run Build {post_id} again",
        f"The text or art of {post_id} changed after it was approved, so it is held back. "
        f"Open Claude Code and type: Build {post_id}",
    )


def publish_failed(page: str, slot: str, retried: bool) -> Alert:
    """Instagram refused or failed the post (details stay in the private run log only)."""
    then = "it will be retried at the next run" if retried else "run Post now again"
    return _make(
        f"publish-failed-{_slug(page)}-{slot}",
        f"[{page}] a post could not be published — {then}",
        f"Instagram did not accept the {slot} post. Nothing was posted twice. "
        "If this repeats, open the latest run and ask Claude Code to look at it.",
    )


def hosting_failed(page: str, slot: str) -> Alert:
    """The media could not be made reachable for Instagram."""
    return _make(
        f"hosting-failed-{_slug(page)}-{slot}",
        f"[{page}] media hosting failed for the {slot} post — it will be retried",
        "The post's images or video could not be served to Instagram, so nothing was posted. "
        "The next run tries again; if this repeats, ask Claude Code to check media hosting.",
    )


def missed_slots(page: str, count: int) -> Alert:
    """Scheduled slots passed with no run at all (schedule switched off or delayed)."""
    return _make(
        f"missed-slots-{_slug(page)}",
        f"[{page}] {count} scheduled posts were missed — check the Status page",
        "No run handled these slots. Check that the Autopilot workflow is enabled "
        "(Actions → Autopilot) and look at the Status page.",
    )


def token_check_failed(page: str) -> Alert:
    """The weekly health check could not use the page's token."""
    return _make(
        f"token-check-{_slug(page)}",
        f"ACTION NEEDED [{page}]: Instagram token not working — regenerate it (README step 6)",
        "The health check could not use the Instagram token. Follow README step 6 to create a "
        "fresh token and set it with scripts/set-token.ps1.",
    )


def setup_test_alert() -> Alert:
    """Harmless alert used once during setup to prove the e-mail path works."""
    return _make(
        "test-alert",
        "Test alert — no action needed (it closes itself)",
        "This is the setup test of the alert path. If you received it by e-mail, alerts work.",
    )


@dataclass
class AlertPlan:
    """Alerts to open (or keep open) and alert keys whose condition has cleared.

    ``scopes``: key prefixes this run is responsible for; any open alert under one of them
    that is not raised again is closed (e.g. a post that no longer fails its gates).
    """

    raise_: list[Alert] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)

    def add(self, alert: Alert) -> None:
        """Raise ``alert`` (once per key)."""
        if all(a.key != alert.key for a in self.raise_):
            self.raise_.append(alert)

    def merge(self, other: AlertPlan) -> None:
        """Combine two plans."""
        for a in other.raise_:
            self.add(a)
        self.resolved += other.resolved
        self.scopes += other.scopes

    def to_dict(self) -> dict[str, object]:
        """JSON form (alert texts are public-safe by construction)."""
        return {
            "raise": [{"key": a.key, "title": a.title, "body": a.body} for a in self.raise_],
            "resolved": sorted(set(self.resolved)),
            "scopes": sorted(set(self.scopes)),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AlertPlan:
        """Parse and re-check (every text must still pass the public-safety check)."""
        raw = data.get("raise", [])
        alerts = [
            _make(str(a["key"]), str(a["title"]), str(a["body"]))
            for a in (raw if isinstance(raw, list) else [])
        ]
        resolved = data.get("resolved", [])
        scopes = data.get("scopes", [])
        return cls(
            alerts,
            [str(k) for k in (resolved if isinstance(resolved, list) else [])],
            [str(k) for k in (scopes if isinstance(scopes, list) else [])],
        )


def page_slug(page: str) -> str:
    """Public alias of the key slug (used for alert scopes)."""
    return _slug(page)
