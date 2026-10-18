from __future__ import annotations

from typing import Any

import pytest

from autopilot.config import Config
from autopilot.errors import ConfigError


def test_real_config_loads(cfg: Config) -> None:
    assert [p.name for p in cfg.enabled_pages] == ["wisdom"]
    assert not cfg.page("science").enabled
    assert cfg.page("wisdom").slots[0].name == "morning"
    assert cfg.section("limits", "max_hashtags") <= 5


def test_unknown_page(cfg: Config) -> None:
    with pytest.raises(ConfigError):
        cfg.page("nope")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("timezone",), "Mars/Base"),
        (("pages", "wisdom", "type"), "memes"),
        (("pages", "wisdom", "handle"), "no-at-sign"),
        (("pages", "wisdom", "slots", "morning", "time"), "25:00"),
        (("pages", "wisdom", "slots", "morning", "format"), "story"),
        (("audio", "weights", "music"), 0.9),
        (("safe_zones", "reel", "bottom"), 0.7),
    ],
)
def test_invalid_config_rejected(
    raw_cfg: dict[str, Any], make_cfg: Any, path: tuple[str, ...], value: Any
) -> None:
    node = raw_cfg
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ConfigError):
        make_cfg()


def test_missing_section(cfg: Config) -> None:
    with pytest.raises(ConfigError):
        cfg.section("does", "not", "exist")
