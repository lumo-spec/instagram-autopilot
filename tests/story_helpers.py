"""Helpers for story-page tests: temporary page folders, posts and generated art."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from PIL import Image

from autopilot.config import Config, Page
from autopilot.content.queue import load_queue
from autopilot.pipeline import StoryBuilder
from autopilot.rehearsal.fakes import LocalHosting

PAGE_FILES = (
    "theme.yaml",
    "spellcheck-words.txt",
    "captions.json",
    "characters.yaml",
    "pronunciations.yaml",
)


def unillustrated_carousel(page: Page) -> tuple[str, int]:
    """(id, slide count) of a real queued carousel with no art and no approval yet.

    CLI tests use the real queue; picking the post at run time keeps them working as the owner
    adds art to posts.
    """
    for item in load_queue(page.dir)[0]:
        post = item.post
        if (
            post.format == "carousel"
            and item.approval is None
            and len(post.missing_art()) == len(post.slides)
        ):
            return post.id, len(post.slides)
    pytest.skip("every queued carousel already has art")


def brief(chars: list[str] | None = None) -> dict[str, Any]:
    return {
        "scene": "A stone bridge in autumn.",
        "characters": chars or ["wren"],
        "emotion": "quiet",
        "composition": "Plain sky at the top.",
    }


def post_data(pid: str, fmt: str = "carousel", n: int = 3, **extra: Any) -> dict[str, Any]:
    texts = [
        "You can miss someone\nand still **let go.**",
        "You can miss\nhow they laughed.",
        "And still choose\nyourself.",
        "Rest is not\na reward.",
        "It is allowed.",
    ]
    data: dict[str, Any] = {
        "id": pid,
        "format": fmt,
        "kind": "repeated-opening" if fmt == "carousel" else "single",
        "topic": "letting go",
        "slides": [
            {"text": texts[i % len(texts)], "position": "top", "brief": brief()} for i in range(n)
        ],
        "caption": f"A gentle {fmt} note, number {int(''.join(filter(str.isdigit, pid)) or 0)}.",
        "hashtags": ["#simplestorymode", "#healing", "#feelings"],
    }
    data.update(extra)
    return data


def write_post(page: Page, data: dict[str, Any], art: str | None = None) -> Path:
    folder = page.dir / "queue" / data["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "post.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if art:
        for i in range(1, len(data["slides"]) + 1):
            make_art(folder / f"art-{i:02d}.png", art, seed=i)
    return folder


def make_art(path: Path, kind: str, seed: int = 0, size: tuple[int, int] = (1080, 1350)) -> None:
    rng = np.random.default_rng(seed)
    w, h = size
    if kind == "busy":  # high-contrast noise everywhere: text needs a panel
        arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    else:  # "quiet": a pale, almost plain cream picture
        arr = np.full((h, w, 3), (246, 238, 222), dtype=np.uint8)
        arr[h // 2 :] = (120, 110, 70)
    Image.fromarray(arr, "RGB").save(path)


@pytest.fixture
def story(cfg: Config, tmp_path: Path) -> tuple[Config, Page]:
    """The real config, with the story page pointed at an empty temporary folder."""
    src = cfg.page("simplestorymode")
    folder = tmp_path / "page"
    folder.mkdir()
    for name in PAGE_FILES:
        shutil.copyfile(src.dir / name, folder / name)
    page = dataclasses.replace(src, base_dir=folder)
    return dataclasses.replace(cfg, pages=(page,)), page


def builder(story: tuple[Config, Page], tmp_path: Path, rehearsal: bool = False) -> StoryBuilder:
    cfg, page = story
    return StoryBuilder(cfg, page, tmp_path / "build", LocalHosting(tmp_path / "hosted"), rehearsal)
