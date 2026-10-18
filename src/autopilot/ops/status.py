"""The public status page (``STATUS.md`` on the ``state`` branch and in run summaries).

Shows, for every page: whether automatic posting is on, the last post, the next slots, how many
approved posts are ready (and roughly how long they last), token dates and the last 7 days.
It contains no secrets, IDs or error details.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from autopilot.config import Config, Page
from autopilot.content.queue import load_queue, publishable
from autopilot.plan import next_slots
from autopilot.state import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SKIPPED,
    PageState,
    used_post_ids,
)

FORMAT_NAMES = {"carousel": "carousels", "image": "single images", "reel": "reels"}


def ready_counts(page: Page, state: PageState) -> dict[str, int]:
    """Approved, unchanged, unused posts per format."""
    posts, _ = load_queue(page.dir)
    counts = {f: 0 for f in FORMAT_NAMES}
    for item in publishable(posts, used_post_ids(state)):
        counts[item.post.format] += 1
    return counts


def days_of_stock(page: Page, counts: dict[str, int]) -> float | None:
    """Days until the first scheduled format runs out (None when no slot needs stock)."""
    per_week: dict[str, int] = {}
    for slot in page.slots:
        per_week[slot.format] = per_week.get(slot.format, 0) + len(slot.days)
    spans = [counts.get(f, 0) / n * 7 for f, n in per_week.items() if n]
    return min(spans) if spans else None


def _token_line(state: PageState, today: date) -> str:
    expires = state.token.get("expires_on", "")
    if not expires:
        return "not set up yet"
    left = (date.fromisoformat(expires) - today).days
    refreshed = state.token.get("refreshed_on", "?")
    return f"renewed {refreshed}, expires {expires} ({left} days left)"


def _week(state: PageState, today: date) -> str:
    since = (today - timedelta(days=7)).isoformat()
    recent = [p for p in state.posts if p.date >= since]
    published = sum(p.status == STATUS_PUBLISHED for p in recent)
    skipped = sum(p.status == STATUS_SKIPPED for p in recent)
    failed = sum(p.status == STATUS_FAILED for p in recent)
    fallbacks = sorted({p.fallback for p in recent if p.fallback and p.status == STATUS_PUBLISHED})
    text = f"{published} posted, {skipped} skipped, {failed} failed (retried)"
    return text + (f"; fallbacks used: {', '.join(fallbacks)}" if fallbacks else "")


def page_section(
    cfg: Config, page: Page, state: PageState, now: datetime, schedule_on: bool
) -> list[str]:
    """Markdown lines for one page."""
    today = now.astimezone(cfg.timezone).date()
    lines = [f"## {page.title} ({page.handle})", ""]
    lines.append(f"- **Page enabled:** {'yes' if page.enabled else 'no'}")
    lines.append(f"- **Automatic posting:** {'ON' if schedule_on else 'OFF (not started yet)'}")
    pubs = state.published()
    if pubs:
        last = pubs[-1]
        link = f" — [open]({last.permalink})" if last.permalink.startswith("https://") else ""
        lines.append(f"- **Last post:** {last.date} · {last.format} · {last.content_id}{link}")
    else:
        lines.append("- **Last post:** none yet")
    if schedule_on and page.enabled:
        upcoming = ", ".join(
            t.astimezone(cfg.timezone).strftime("%a %d %b %H:%M")
            for t in next_slots(cfg, page, now.astimezone(UTC), 3)
        )
        lines.append(f"- **Next slots (UK time, ±10 min):** {upcoming}")
    counts = ready_counts(page, state)
    stock = ", ".join(f"{n} {FORMAT_NAMES[f]}" for f, n in counts.items())
    days = days_of_stock(page, counts)
    lasts = f" — lasts about {days:.0f} days" if days is not None else ""
    lines.append(f"- **Approved posts ready:** {stock}{lasts}")
    lines.append(f"- **Instagram token:** {_token_line(state, today)}")
    lines.append(f"- **Last 7 days:** {_week(state, today)}")
    return [*lines, ""]


def render_status(
    cfg: Config,
    states: dict[str, PageState],
    now: datetime,
    schedule_on: bool,
    renewal_key_expires: str = "",
) -> str:
    """The whole status page."""
    stamp = now.astimezone(cfg.timezone).strftime("%a %d %b %Y %H:%M %Z")
    lines = ["# Instagram Autopilot — Status", "", f"Updated {stamp}.", ""]
    for page in cfg.pages:
        lines += page_section(
            cfg, page, states.get(page.name, PageState(page.name)), now, schedule_on
        )
    key = renewal_key_expires or "not checked yet"
    lines += [
        f"**Renewal key expires:** {key}",
        "",
        "_This page is public and contains no secrets. Problems that need you arrive as "
        "e-mail alerts (GitHub issues labelled `autopilot-alert`)._",
        "",
    ]
    return "\n".join(lines)
