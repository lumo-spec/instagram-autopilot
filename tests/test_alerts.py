from __future__ import annotations

import pytest

from autopilot.ops import alerts


def test_titles_say_problem_and_fix() -> None:
    a = alerts.low_stock("Science", 70, 35)
    assert (
        a.title == "ACTION NEEDED [Science]: content running low — "
        "open Claude Code and type: Top up Science"
    )
    assert alerts.renewal_key_expiring("12 Nov").title.startswith(
        "ACTION NEEDED: renewal key expires 12 Nov"
    )
    assert "README step 6" in alerts.token_renewal_failed("Simple Story Mode").title
    assert (
        alerts.no_recent_post("Simple Story Mode", 30).title
        == "[Simple Story Mode] no post for 30+ hours — check the Status page"
    )
    assert "ask Claude Code to upgrade" in alerts.api_version_retiring("1 Feb").title


def test_keys_are_stable_for_dedupe() -> None:
    assert (
        alerts.low_stock("Simple Story Mode", 70, 35).key
        == alerts.low_stock("Simple Story Mode", 10, 5).key
    )
    assert alerts.queue_low("Simple Story Mode", 4).key == "queue-low-simple-story-mode"
    assert (
        alerts.queue_low("Simple Story Mode", 4).key == alerts.queue_low("Simple Story Mode", 1).key
    )


def test_queue_alerts_say_what_to_do() -> None:
    assert alerts.queue_low("Simple Story Mode", 4).title == (
        "ACTION NEEDED [Simple Story Mode]: only 4 approved posts left — "
        "open Claude Code and type: New post: <topic>"
    )
    empty = alerts.queue_empty("Simple Story Mode", "carousel")
    assert "no approved post for the carousel slot" in empty.title and "skipped" in empty.title
    changed = alerts.approval_changed("Simple Story Mode", "c01-you-can-miss")
    assert "Build c01-you-can-miss" in changed.title


@pytest.mark.parametrize(
    "leak",
    [
        "IGQWRN" + "a" * 30,
        "ghp" + "_" + "b" * 36,
        "github" + "_pat_" + "c" * 40,
        "user 17841400000000000",
        "Traceback (most recent call last)",
        '{"error": {"message": "x"}}',
        "access_token=abc",
        "Bearer abc",
    ],
)
def test_public_safety_check_blocks_leaks(leak: str) -> None:
    with pytest.raises(ValueError):
        alerts.assert_public_safe(f"Something failed: {leak}")
    with pytest.raises(ValueError):
        alerts.slot_skipped("Simple Story Mode", "evening", leak)
