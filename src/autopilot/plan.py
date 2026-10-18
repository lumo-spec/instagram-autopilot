"""Which page/slot pairs are due now.

GitHub cron runs in UTC and can be late, so the workflow runs every 30 minutes and this module
works in the owner's timezone itself (clock changes need no workflow edits). Each slot gets a
deterministic per-day offset of up to +/- ``jitter_minutes`` so posting times look natural.
A slot is due once its target time has passed, while it is still within the catch-up window and
not too close to the next slot. Slots from earlier days are never back-filled. Slots may be limited
to certain weekdays (e.g. carousels on Monday, Wednesday and Friday).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from autopilot.config import Config, Page, Slot
from autopilot.state import PageState


@dataclass(frozen=True)
class DueSlot:
    """A slot that should be posted now."""

    page: str
    slot: str
    format: str
    slot_key: str
    local_date: date
    target_utc: datetime


def slot_key(page: str, day: date, slot: str) -> str:
    """Idempotency key such as ``simplestorymode-2026-09-18-carousel``."""
    return f"{page}-{day.isoformat()}-{slot}"


def jitter_minutes(page: str, day: date, slot: str, spread: int) -> int:
    """Deterministic offset in [-spread, +spread] from a hash of page, date and slot."""
    digest = hashlib.sha256(f"{page}|{day.isoformat()}|{slot}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % (2 * spread + 1) - spread


def target_time(page: str, slot: Slot, day: date, tz: ZoneInfo, spread: int) -> datetime:
    """Target UTC time for ``slot`` on local date ``day`` (DST-aware)."""
    hh, mm = (int(x) for x in slot.time.split(":"))
    local = datetime.combine(day, time(hh, mm), tzinfo=tz)
    # Normalise through UTC so a time inside a DST gap resolves to a real instant.
    utc = local.astimezone(ZoneInfo("UTC"))
    return utc + timedelta(minutes=jitter_minutes(page, day, slot.name, spread))


def due_slots(cfg: Config, page: Page, now_utc: datetime, state: PageState) -> list[DueSlot]:
    """Slots of ``page`` that are due at ``now_utc`` and not yet done."""
    tz = cfg.timezone
    spread = int(cfg.section("jitter_minutes"))
    window = timedelta(hours=float(cfg.section("catchup_window_hours")))
    gap = timedelta(minutes=float(cfg.section("min_minutes_before_next_slot")))
    today = now_utc.astimezone(tz).date()
    targets = [
        (s, target_time(page.name, s, today, tz, spread))
        for s in page.slots
        if s.runs_on(today.weekday())
    ]
    due: list[DueSlot] = []
    for i, (slot, target) in enumerate(targets):
        key = slot_key(page.name, today, slot.name)
        if state.is_done(key) or now_utc < target or now_utc >= target + window:
            continue
        if i + 1 < len(targets) and now_utc >= targets[i + 1][1] - gap:
            continue  # too close to the next slot: skip rather than post twice in a row
        due.append(DueSlot(page.name, slot.name, slot.format, key, today, target))
    return due


def plan(cfg: Config, now_utc: datetime, states: dict[str, PageState]) -> list[DueSlot]:
    """Due slots across every enabled page."""
    out: list[DueSlot] = []
    for page in cfg.enabled_pages:
        out += due_slots(cfg, page, now_utc, states.get(page.name, PageState(page.name)))
    return out


def next_slots(cfg: Config, page: Page, now_utc: datetime, count: int = 2) -> list[datetime]:
    """Upcoming target times (UTC) for the status page."""
    tz = cfg.timezone
    spread = int(cfg.section("jitter_minutes"))
    day = now_utc.astimezone(tz).date()
    found: list[datetime] = []
    while len(found) < count:
        for s in page.slots:
            if not s.runs_on(day.weekday()):
                continue
            t = target_time(page.name, s, day, tz, spread)
            if t > now_utc and len(found) < count:
                found.append(t)
        day += timedelta(days=1)
    return found


def missed_slots(
    cfg: Config, page: Page, now_utc: datetime, state: PageState, since: date, days: int = 7
) -> list[str]:
    """Slot keys from the last ``days`` days (not before ``since``) that no run ever handled."""
    tz = cfg.timezone
    spread = int(cfg.section("jitter_minutes"))
    window = timedelta(hours=float(cfg.section("catchup_window_hours")))
    today = now_utc.astimezone(tz).date()
    day = max(since, today - timedelta(days=days))
    out: list[str] = []
    while day <= today:
        for s in page.slots:
            if not s.runs_on(day.weekday()):
                continue
            key = slot_key(page.name, day, s.name)
            if (
                target_time(page.name, s, day, tz, spread) + window < now_utc
                and state.get(key) is None
            ):
                out.append(key)
        day += timedelta(days=1)
    return out
