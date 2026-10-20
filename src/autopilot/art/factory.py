"""Build the art generator from ``config.yaml`` and send its alerts from the owner's PC."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from autopilot.art.budget import Ledger
from autopilot.art.clients import (
    Checker,
    GeminiChecker,
    GeminiImageClient,
    ImageClient,
    OpenAIChecker,
    OpenAIImageClient,
    model_exists,
)
from autopilot.art.fakes import FakeChecker, FakeImageClient
from autopilot.art.generate import (
    ArtGenerator,
    PlaceholderReferences,
    ReferenceLibrary,
    references_dir,
)
from autopilot.art.http import Transport, urllib_transport
from autopilot.art.keys import CHECK_KEY, IMAGE_KEY, load_key
from autopilot.config import ROOT, Config, Page
from autopilot.errors import ConfigError
from autopilot.ops.alerts import AlertPlan
from autopilot.ops.github import GitHub, GitHubError
from autopilot.ops.issues import sync

PROVIDERS = ("gemini", "openai", "fake")


def settings(cfg: Config) -> dict[str, Any]:
    """The ``art_generation`` section, checked."""
    art = dict(cfg.section("art_generation"))
    for key in ("provider", "checker"):
        if art.get(key) not in PROVIDERS:
            raise ConfigError(f"art_generation.{key} must be one of {PROVIDERS}")
    if float(art.get("monthly_cap_gbp", 0)) <= 0:
        raise ConfigError("art_generation.monthly_cap_gbp must be above 0")
    if int(art.get("max_regenerations", -1)) not in range(6):
        raise ConfigError("art_generation.max_regenerations must be 0-5")
    return art


def make_painter(art: dict[str, Any], key: str, transport: Transport) -> ImageClient:
    """The configured image client."""
    name = art["provider"]
    if name == "fake":
        return FakeImageClient()
    p = art["providers"][name]
    if name == "gemini":
        return GeminiImageClient(
            key,
            str(p["model"]),
            str(p["aspect_ratio"]),
            str(p["image_size"]),
            float(p["usd_per_image"]),
            transport,
        )
    return OpenAIImageClient(
        key,
        str(p["model"]),
        str(p["size"]),
        str(p["quality"]),
        float(p["usd_per_image"]),
        transport,
    )


def make_checker(art: dict[str, Any], key: str, transport: Transport) -> Checker:
    """The configured vision checker."""
    name = art["checker"]
    if name == "fake":
        return FakeChecker()
    c = art["checkers"][name]
    cls = GeminiChecker if name == "gemini" else OpenAIChecker
    return cls(key, str(c["model"]), float(c["usd_per_check"]), transport)


def keys(art: dict[str, Any]) -> tuple[str, str]:
    """(image key, checker key); fake providers need none, one provider shares one key."""
    image = load_key(IMAGE_KEY) if art["provider"] != "fake" else ""
    if art["checker"] == "fake":
        return image, ""
    if art["checker"] == art["provider"]:
        return image, image
    return image, load_key(CHECK_KEY)


def characters(page: Page) -> dict[str, Any]:
    """The page's ``characters.yaml``."""
    data = yaml.safe_load((page.dir / "characters.yaml").read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def make_generator(
    cfg: Config,
    page: Page,
    fake: bool = False,
    ledger_path: Path | None = None,
    transport: Transport = urllib_transport,
) -> ArtGenerator:
    """An :class:`ArtGenerator` wired from config (``fake``: no network, no key, no cost)."""
    art = settings(cfg)
    if fake:
        art = {**art, "provider": "fake", "checker": "fake"}
    image_key, check_key = keys(art)
    chars = characters(page)
    size = art["output_size"]
    return ArtGenerator(
        page.title,
        chars,
        make_painter(art, image_key, transport),
        make_checker(art, check_key, transport),
        Ledger(
            ledger_path or ROOT / str(art["ledger"]),
            float(art["monthly_cap_gbp"]),
            float(art["usd_to_gbp"]),
            cfg.timezone,
        ),
        (PlaceholderReferences if fake else ReferenceLibrary)(
            chars, references_dir(chars), int(art["reference_max_px"])
        ),
        (int(size[0]), int(size[1])),
        int(art["max_regenerations"]),
    )


def check_models(cfg: Config, transport: Transport = urllib_transport) -> list[str]:
    """Free calls proving the key works and both model names exist; returns what was checked."""
    art = settings(cfg)
    image_key, check_key = keys(art)
    done = []
    for provider, model, key in (
        (art["provider"], (art["providers"].get(art["provider"]) or {}).get("model"), image_key),
        (art["checker"], (art["checkers"].get(art["checker"]) or {}).get("model"), check_key),
    ):
        if provider != "fake":
            model_exists(provider, str(model), key, transport)
            done.append(f"{provider}/{model}")
    return done


def send_alerts(cfg: Config, plan: AlertPlan) -> str:
    """Open (or close) alert issues with the owner's GitHub CLI login; GitHub e-mails the owner.

    Returns a short note on what happened (never raises: the alert is also printed).
    """
    if not plan.raise_ and not plan.resolved:
        return ""
    try:
        token = subprocess.run(
            ["gh", "auth", "token"], check=True, capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "could not send the alert e-mail (GitHub CLI not logged in)"
    try:
        gh = GitHub(token, str(cfg.section("github", "repo")))
        from datetime import date

        result = sync(
            gh, plan, str(cfg.section("github", "owner_mention")), "", date.today().isoformat()
        )
    except GitHubError as exc:
        return f"could not send the alert e-mail ({exc})"
    parts = []
    if result.opened or result.commented:
        parts.append(f"alert e-mail sent ({', '.join(result.opened + result.commented)})")
    if result.closed:
        parts.append(f"closed alerts: {', '.join(result.closed)}")
    return "; ".join(parts)
