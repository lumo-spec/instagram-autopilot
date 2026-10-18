"""End-to-end rehearsal with real TTS, rendering and gates (slow: needs the Kokoro model)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from autopilot.config import Config
from autopilot.rehearsal import runner

pytestmark = pytest.mark.slow


def test_rehearsal_with_injected_failures(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Compress the failure plan into three days so the test stays reasonably quick.
    monkeypatch.setattr(
        runner,
        "FAILURE_PLAN",
        {
            0: ("voice_gate", "crash_after_publish"),
            1: ("container_error", "missed_runs"),
            2: ("quota_high",),
        },
    )
    data = runner.run_rehearsal(cfg, 3, date(2026, 10, 24), tmp_path / "r", failures=True)
    assert data["published"] == data["expected_slots"] == 6
    assert data["double_posts"] == 0
    assert data["fallbacks"].get("second-voice") == 1
    events = "\n".join(data["events"])
    assert "[crash]" in events and "reconciled, not re-posted" in events
    assert "[fail]" in events and "[quota]" in events
    for post in data["posts"]:
        assert (tmp_path / "r" / post["sample"]).exists()
        assert post["gates"][-1]["passed"]
    calls = json.loads((tmp_path / "r" / "fake-instagram-calls.json").read_text(encoding="utf-8"))
    assert all("token" not in json.dumps(c).lower() for c in calls)
    state = json.loads((tmp_path / "r/state/wisdom/state.json").read_text(encoding="utf-8"))
    assert all(p["status"] == "published" for p in state["posts"])
