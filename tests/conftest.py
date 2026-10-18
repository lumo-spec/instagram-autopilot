"""Shared fixtures."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from autopilot.config import ROOT, Config, load_config, parse_config


@pytest.fixture(scope="session")
def cfg() -> Config:
    """The real repository configuration."""
    return load_config()


@pytest.fixture
def raw_cfg() -> dict[str, Any]:
    """A mutable copy of the raw config for negative tests."""
    data: dict[str, Any] = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


@pytest.fixture
def make_cfg(raw_cfg: dict[str, Any]):  # type: ignore[no-untyped-def]
    """Build a Config from an edited raw mapping."""
    return lambda: parse_config(raw_cfg)


@pytest.fixture
def entries() -> list[dict[str, Any]]:
    """A small, varied quote bank."""
    texts = [
        ("q-00000001", "Rest is part of the work.", ["rest", "discipline"]),
        ("q-00000002", "Be gentle with yourself today.", ["kindness"]),
        ("q-00000003", "The river finds its way around the stone.", ["nature", "resilience"]),
        ("q-00000004", "Small steps still cover great distances.", ["patience"]),
        ("q-00000005", "Silence can answer what noise cannot.", ["peace"]),
        ("q-00000006", "Courage grows each time you use it.", ["courage"]),
    ]
    return [
        {
            "id": i,
            "text": t,
            "author": None,
            "tags": tags,
            "length_class": "short",
            "status": "verified",
        }
        for i, t, tags in texts
    ]


@pytest.fixture
def tmp_out(tmp_path: Path) -> Path:
    """Output directory for rendered files."""
    out = tmp_path / "out"
    out.mkdir()
    return out


from story_helpers import story  # noqa: E402,F401  (fixture: temporary story page)
