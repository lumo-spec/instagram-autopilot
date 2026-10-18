from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from autopilot.config import Config, parse_config
from autopilot.plan import due_slots, jitter_minutes, next_slots, plan, slot_key, target_time
from autopilot.state import STATUS_PUBLISHED, PageState, PostRecord


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def cfg(raw_cfg: dict[str, Any]) -> Config:
    """A daily two-slot page ("dailypage": 08:30 image, 19:30 reel) for timing tests."""
    raw_cfg["pages"] = {
        "dailypage": {
            "enabled": True,
            "type": "quotes",
            "handle": "@CHANGE_ME",
            "slots": {
                "morning": {"time": "08:30", "format": "image"},
                "evening": {"time": "19:30", "format": "reel"},
            },
        },
        "other": {
            "enabled": False,
            "type": "quotes",
            "handle": "@CHANGE_ME",
            "slots": {"noon": {"time": "12:00", "format": "image"}},
        },
    }
    return parse_config(raw_cfg)


def test_slot_key() -> None:
    assert slot_key("dailypage", date(2026, 9, 18), "morning") == "dailypage-2026-09-18-morning"


def test_jitter_is_deterministic_and_bounded() -> None:
    values = {
        jitter_minutes("dailypage", date(2026, 1, 1) + timedelta(days=i), "morning", 10)
        for i in range(200)
    }
    assert min(values) >= -10 and max(values) <= 10
    assert len(values) > 10  # actually varies day to day
    assert jitter_minutes("dailypage", date(2026, 5, 5), "evening", 10) == jitter_minutes(
        "dailypage", date(2026, 5, 5), "evening", 10
    )


def test_bst_and_gmt_conversion(cfg: Config) -> None:
    page = cfg.page("dailypage")
    morning = page.slots[0]
    summer = target_time("dailypage", morning, date(2026, 7, 1), cfg.timezone, 0)
    winter = target_time("dailypage", morning, date(2026, 12, 1), cfg.timezone, 0)
    assert (summer.hour, summer.minute) == (7, 30)  # 08:30 BST
    assert (winter.hour, winter.minute) == (8, 30)  # 08:30 GMT


def test_clock_change_days(cfg: Config) -> None:
    page = cfg.page("dailypage")
    for day in (date(2026, 3, 29), date(2026, 10, 25)):
        t = target_time("dailypage", page.slots[0], day, cfg.timezone, 0)
        assert t.astimezone(cfg.timezone).strftime("%H:%M") == "08:30"


def _target(cfg: Config, day: date, slot: int = 0) -> datetime:
    page = cfg.page("dailypage")
    return target_time(
        "dailypage", page.slots[slot], day, cfg.timezone, int(cfg.section("jitter_minutes"))
    )


def test_due_after_target_not_before(cfg: Config) -> None:
    page, day = cfg.page("dailypage"), date(2026, 11, 3)
    t = _target(cfg, day)
    assert due_slots(cfg, page, t - timedelta(minutes=1), PageState("dailypage")) == []
    due = due_slots(cfg, page, t + timedelta(minutes=1), PageState("dailypage"))
    assert [d.slot for d in due] == ["morning"]


def test_catch_up_within_window_only(cfg: Config) -> None:
    page, day = cfg.page("dailypage"), date(2026, 11, 3)
    t = _target(cfg, day)
    assert due_slots(cfg, page, t + timedelta(hours=4, minutes=50), PageState("dailypage"))
    assert not due_slots(cfg, page, t + timedelta(hours=5, minutes=1), PageState("dailypage"))


def test_not_too_close_to_next_slot(cfg: Config) -> None:
    page, day = cfg.page("dailypage"), date(2026, 11, 3)
    evening = _target(cfg, day, 1)
    due = due_slots(cfg, page, evening + timedelta(minutes=5), PageState("dailypage"))
    assert [d.slot for d in due] == ["evening"]  # morning is far outside its window anyway


def test_done_slot_not_due(cfg: Config) -> None:
    page, day = cfg.page("dailypage"), date(2026, 11, 3)
    state = PageState(
        "dailypage",
        [
            PostRecord(
                slot_key("dailypage", day, "morning"), day.isoformat(), "morning", STATUS_PUBLISHED
            )
        ],
    )
    assert due_slots(cfg, page, _target(cfg, day) + timedelta(minutes=5), state) == []


def test_earlier_days_never_backfilled(cfg: Config) -> None:
    page = cfg.page("dailypage")
    # Just after midnight: yesterday's evening slot is not due, today's slots are not yet due.
    assert due_slots(cfg, page, utc(2026, 11, 4, 0, 47), PageState("dailypage")) == []


def test_plan_only_enabled_pages(cfg: Config) -> None:
    t = _target(cfg, date(2026, 11, 3)) + timedelta(minutes=3)
    assert {d.page for d in plan(cfg, t, {})} == {"dailypage"}


def test_next_slots(cfg: Config) -> None:
    upcoming = next_slots(cfg, cfg.page("dailypage"), utc(2026, 11, 3, 12, 0), 3)
    assert len(upcoming) == 3 and upcoming == sorted(upcoming)


def test_weekday_slots_only_run_on_their_days(raw_cfg: dict[str, Any]) -> None:
    real = parse_config(raw_cfg)
    page = real.page("simplestorymode")
    carousel = next(s for s in page.slots if s.format == "carousel")
    single = next(s for s in page.slots if s.format == "image")
    assert carousel.days == (0, 2, 4) and single.days == (1, 5)
    state = PageState(page.name)
    monday, thursday = date(2026, 11, 2), date(2026, 11, 5)
    t = target_time(page.name, carousel, monday, real.timezone, 10) + timedelta(minutes=11)
    assert [d.slot for d in due_slots(real, page, t, state)] == ["carousel"]
    for slot in page.slots:  # nothing at all on a Thursday
        t = target_time(page.name, slot, thursday, real.timezone, 0) + timedelta(minutes=30)
        assert due_slots(real, page, t, state) == []
    upcoming = next_slots(real, page, utc(2026, 11, 5, 9, 0), 5)
    days = [u.astimezone(real.timezone).strftime("%a") for u in upcoming]
    assert days == ["Fri", "Sat", "Mon", "Tue", "Wed"]


def test_bad_weekday_rejected(raw_cfg: dict[str, Any]) -> None:
    from autopilot.errors import ConfigError

    raw_cfg["pages"]["simplestorymode"]["slots"]["carousel"]["days"] = ["funday"]
    with pytest.raises(ConfigError):
        parse_config(raw_cfg)
