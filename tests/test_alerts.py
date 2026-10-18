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
    assert "README step 6" in alerts.token_renewal_failed("Wisdom").title
    assert (
        alerts.no_recent_post("Wisdom", 30).title
        == "[Wisdom] no post for 30+ hours — check the Status page"
    )
    assert "ask Claude Code to upgrade" in alerts.api_version_retiring("1 Feb").title


def test_keys_are_stable_for_dedupe() -> None:
    assert alerts.low_stock("Wisdom", 70, 35).key == alerts.low_stock("Wisdom", 10, 5).key


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
        alerts.slot_skipped("Wisdom", "evening", leak)
