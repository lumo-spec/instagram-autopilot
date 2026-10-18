from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from autopilot.config import Config
from autopilot.plan import due_slots, jitter_minutes, next_slots, plan, slot_key, target_time
from autopilot.state import STATUS_PUBLISHED, PageState, PostRecord


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_slot_key() -> None:
    assert slot_key("wisdom", date(2026, 9, 18), "morning") == "wisdom-2026-09-18-morning"


def test_jitter_is_deterministic_and_bounded() -> None:
    values = {
        jitter_minutes("wisdom", date(2026, 1, 1) + timedelta(days=i), "morning", 10)
        for i in range(200)
    }
    assert min(values) >= -10 and max(values) <= 10
    assert len(values) > 10  # actually varies day to day
    assert jitter_minutes("wisdom", date(2026, 5, 5), "evening", 10) == jitter_minutes(
        "wisdom", date(2026, 5, 5), "evening", 10
    )


def test_bst_and_gmt_conversion(cfg: Config) -> None:
    page = cfg.page("wisdom")
    morning = page.slots[0]
    summer = target_time("wisdom", morning, date(2026, 7, 1), cfg.timezone, 0)
    winter = target_time("wisdom", morning, date(2026, 12, 1), cfg.timezone, 0)
    assert (summer.hour, summer.minute) == (7, 30)  # 08:30 BST
    assert (winter.hour, winter.minute) == (8, 30)  # 08:30 GMT


def test_clock_change_days(cfg: Config) -> None:
    page = cfg.page("wisdom")
    for day in (date(2026, 3, 29), date(2026, 10, 25)):
        t = target_time("wisdom", page.slots[0], day, cfg.timezone, 0)
        assert t.astimezone(cfg.timezone).strftime("%H:%M") == "08:30"


def _target(cfg: Config, day: date, slot: int = 0) -> datetime:
    page = cfg.page("wisdom")
    return target_time(
        "wisdom", page.slots[slot], day, cfg.timezone, int(cfg.section("jitter_minutes"))
    )


def test_due_after_target_not_before(cfg: Config) -> None:
    page, day = cfg.page("wisdom"), date(2026, 11, 3)
    t = _target(cfg, day)
    assert due_slots(cfg, page, t - timedelta(minutes=1), PageState("wisdom")) == []
    due = due_slots(cfg, page, t + timedelta(minutes=1), PageState("wisdom"))
    assert [d.slot for d in due] == ["morning"]


def test_catch_up_within_window_only(cfg: Config) -> None:
    page, day = cfg.page("wisdom"), date(2026, 11, 3)
    t = _target(cfg, day)
    assert due_slots(cfg, page, t + timedelta(hours=4, minutes=50), PageState("wisdom"))
    assert not due_slots(cfg, page, t + timedelta(hours=5, minutes=1), PageState("wisdom"))


def test_not_too_close_to_next_slot(cfg: Config) -> None:
    page, day = cfg.page("wisdom"), date(2026, 11, 3)
    evening = _target(cfg, day, 1)
    due = due_slots(cfg, page, evening + timedelta(minutes=5), PageState("wisdom"))
    assert [d.slot for d in due] == ["evening"]  # morning is far outside its window anyway


def test_done_slot_not_due(cfg: Config) -> None:
    page, day = cfg.page("wisdom"), date(2026, 11, 3)
    state = PageState(
        "wisdom",
        [
            PostRecord(
                slot_key("wisdom", day, "morning"), day.isoformat(), "morning", STATUS_PUBLISHED
            )
        ],
    )
    assert due_slots(cfg, page, _target(cfg, day) + timedelta(minutes=5), state) == []


def test_earlier_days_never_backfilled(cfg: Config) -> None:
    page = cfg.page("wisdom")
    # Just after midnight: yesterday's evening slot is not due, today's slots are not yet due.
    assert due_slots(cfg, page, utc(2026, 11, 4, 0, 47), PageState("wisdom")) == []


def test_plan_only_enabled_pages(cfg: Config) -> None:
    t = _target(cfg, date(2026, 11, 3)) + timedelta(minutes=3)
    assert {d.page for d in plan(cfg, t, {})} == {"wisdom"}


def test_next_slots(cfg: Config) -> None:
    upcoming = next_slots(cfg, cfg.page("wisdom"), utc(2026, 11, 3, 12, 0), 3)
    assert len(upcoming) == 3 and upcoming == sorted(upcoming)
