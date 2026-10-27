"""Posting slots in the owner's time zone (exact times, no jitter, clock changes handled).

A slot is due from its time until ``catchup_minutes`` later (the PC may have been asleep).
When several slots of one destination are due at once, only the newest is posted and the older
ones are recorded as missed, so a PC waking up never posts a burst.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from autopilot.inbox.config import Destination

UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class Slot:
    """One posting time of one destination."""

    dest: str
    key: str  # e.g. instagram-reels-2026-10-28-0730
    at_utc: datetime
    local: datetime


def _slot(dest: Destination, day: date, hhmm: str, tz: ZoneInfo) -> Slot:
    hh, mm = (int(x) for x in hhmm.split(":"))
    local = datetime.combine(day, time(hh, mm), tzinfo=tz)
    at = local.astimezone(UTC)  # a time inside a spring-forward gap resolves to a real instant
    return Slot(dest.name, f"{dest.name}-{day.isoformat()}-{hh:02d}{mm:02d}", at, local)


def slots_between(
    dest: Destination, tz: ZoneInfo, start_utc: datetime, end_utc: datetime
) -> Iterator[Slot]:
    """Slots with ``start_utc <= time < end_utc``, in time order."""
    day = start_utc.astimezone(tz).date() - timedelta(days=1)
    while True:
        if dest.runs_on(day.weekday()):
            for t in dest.times:
                s = _slot(dest, day, t, tz)
                if s.at_utc >= end_utc:
                    return
                if s.at_utc >= start_utc:
                    yield s
        elif datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC) >= end_utc:
            return
        day += timedelta(days=1)


def due(dest: Destination, tz: ZoneInfo, now_utc: datetime, catchup_minutes: float) -> list[Slot]:
    """Slots whose time has passed but are still inside the catch-up window (oldest first)."""
    window = timedelta(minutes=catchup_minutes)
    return list(slots_between(dest, tz, now_utc - window, now_utc + timedelta(microseconds=1)))


def upcoming(dest: Destination, tz: ZoneInfo, now_utc: datetime, count: int) -> list[Slot]:
    """The next ``count`` slots after ``now_utc``."""
    out: list[Slot] = []
    for s in slots_between(dest, tz, now_utc, now_utc + timedelta(days=400)):
        if s.at_utc > now_utc:
            out.append(s)
        if len(out) == count:
            break
    return out
