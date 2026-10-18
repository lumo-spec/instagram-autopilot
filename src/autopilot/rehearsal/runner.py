"""Full practice run: fake clock, real selection/rendering/gates/voice/state, fake Instagram.

Simulates the 30-minute schedule over N days (spanning a clock change by default), injects one
failure of each kind, and writes a report plus every sample for the owner to review.
"""

from __future__ import annotations

import html
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from autopilot.config import ROOT, Config, Page
from autopilot.errors import PublishError
from autopilot.ops import alerts
from autopilot.pipeline import Builder, BuildResult, Request
from autopilot.plan import plan
from autopilot.publish.instagram import publish_manifest
from autopilot.rehearsal.fakes import (
    FakeInstagram,
    LocalHosting,
    SimulatedCrashError,
    no_instagram_network,
)
from autopilot.rehearsal.samples import media_tag
from autopilot.state import (
    STATUS_FAILED,
    STATUS_INTENT,
    STATUS_PUBLISHED,
    STATUS_SKIPPED,
    PageState,
    PostRecord,
    save_state,
)

# Day index (0-based) -> injected failures for that day's reel slot (or the whole day).
FAILURE_PLAN: dict[int, tuple[str, ...]] = {
    1: ("missed_runs",),
    2: ("voice_gate",),
    3: ("container_error",),
    4: ("voice_gate_all",),
    5: ("publish_transient",),
    6: ("crash_after_publish",),
    8: ("quota_high",),
}
MISSED_LOCAL_HOURS = (6, 10)  # cron runs "lost" between these local hours on the missed day


@dataclass
class SimPost:
    """One simulated post for the report."""

    page: str
    slot_key: str
    local_time: str
    format: str
    audio_mode: str
    fallback: str
    template: str
    content_id: str
    text: str
    caption: str
    sample: str
    gates: list[dict[str, Any]]
    events: list[str] = field(default_factory=list)


def next_clock_change(tz: ZoneInfo, start: date) -> date | None:
    """First date on or after ``start`` whose UTC offset differs from the previous day's."""
    noon = datetime.combine(start, datetime.min.time(), tzinfo=tz).replace(hour=12)
    prev = noon.utcoffset()
    for i in range(1, 400):
        day = noon + timedelta(days=i)
        if day.utcoffset() != prev:
            return start + timedelta(days=i)
    return None


def default_start(tz: ZoneInfo, today: date, days: int) -> date:
    """Start so the run spans the next clock change (about mid-way)."""
    change = next_clock_change(tz, today)
    return change - timedelta(days=days // 2) if change else today


def _ticks(start: date, days: int, tz: ZoneInfo) -> list[datetime]:
    first = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    first = first.replace(minute=17, second=0)
    end = first + timedelta(days=days, hours=1)
    out, t = [], first
    while t < end:
        out.append(t)
        t += timedelta(minutes=30)
    return out


class Rehearsal:
    """Runs the practice and collects results."""

    def __init__(self, cfg: Config, out: Path, days: int, start: date, failures: bool) -> None:
        self.cfg, self.out, self.days, self.start, self.failures = cfg, out, days, start, failures
        self.fake = FakeInstagram()
        self.hosting = LocalHosting(out / "hosted")
        self.states: dict[str, PageState] = {p.name: PageState(p.name) for p in cfg.enabled_pages}
        self.builders = {
            p.name: Builder(cfg, p, out / "build", self.hosting) for p in cfg.enabled_pages
        }
        self.posts: list[SimPost] = []
        self.events: list[str] = []
        self.alerts: dict[str, alerts.Alert] = {}
        self.built: dict[str, BuildResult] = {}
        self.injected: set[str] = set()

    def _day_index(self, local: date) -> int:
        return (local - self.start).days

    def _injected(self, local: date) -> tuple[str, ...]:
        return FAILURE_PLAN.get(self._day_index(local), ()) if self.failures else ()

    def log(self, msg: str) -> None:
        """Record and print an event."""
        self.events.append(msg)
        print(msg, flush=True)

    def _record(self, page: str, rec: PostRecord) -> None:
        self.states[page].upsert(rec)
        save_state(self.out / "state" / page / "state.json", self.states[page])

    def _build(
        self, page: Page, slot_key: str, local: date, fmt: str, inject: tuple[str, ...]
    ) -> BuildResult:
        if slot_key not in self.built:
            req = Request(slot_key, local, fmt, inject=frozenset(inject))
            if fmt == "reel" and {"voice_gate", "voice_gate_all"} & set(inject):
                req.audio = "voice+music"
            self.log(f"[build] {slot_key} ({fmt})")
            self.built[slot_key] = self.builders[page.name].build(
                req, self.states[page.name], self.log
            )
        return self.built[slot_key]

    def _skip(self, page: Page, slot_key: str, day: date, reason: str) -> None:
        slot = slot_key.rsplit("-", 1)[1]
        self._record(
            page.name,
            PostRecord(slot_key, day.isoformat(), slot, STATUS_SKIPPED, fallback="skipped"),
        )
        alert = alerts.slot_skipped(page.title, slot, reason)
        self.alerts[alert.key] = alert
        self.log(f"[skip] {slot_key}: {reason}")

    def _publish(self, page: Page, slot_key: str, local_dt: datetime, fmt: str) -> None:
        state = self.states[page.name]
        inject = self._injected(local_dt.date())
        result = self._build(page, slot_key, local_dt.date(), fmt, inject)
        if result.manifest is None:
            self._skip(page, slot_key, local_dt.date(), result.skipped_reason)
            return
        manifest = result.manifest
        if slot_key not in self.injected:  # each simulated failure fires once per slot
            self.injected.add(slot_key)
            api_failures = ("container_error", "publish_transient", "crash_after_publish")
            if fmt == "reel":
                self.fake.fail_next.update(i for i in inject if i in api_failures)
            if "quota_high" in inject:
                self.fake.fail_next.add("quota_high")
        stop_at = int(self.cfg.section("limits", "quota_stop_at"))
        if self.fake.publishing_usage() >= stop_at:
            self.log(f"[quota] {slot_key}: usage at/above {stop_at}; stopping safely this run")
            self._record(
                page.name,
                PostRecord(
                    slot_key,
                    local_dt.date().isoformat(),
                    slot_key.rsplit("-", 1)[1],
                    STATUS_FAILED,
                    format=manifest.format,
                ),
            )
            return
        prev = state.get(slot_key)
        previous_container = (
            prev.container_id if prev and prev.status in (STATUS_INTENT, STATUS_FAILED) else ""
        )
        base = PostRecord(
            slot_key,
            local_dt.date().isoformat(),
            slot_key.rsplit("-", 1)[1],
            STATUS_INTENT,
            format=manifest.format,
            audio_mode=manifest.audio_mode,
            fallback=manifest.fallback,
            content_id=manifest.content_id,
            template=manifest.template,
            hashtags=list(manifest.hashtags),
            container_id=previous_container,
            recorded_at=local_dt.isoformat(timespec="minutes"),
            text=result.entry["text"]
            if result.entry and result.entry["id"].startswith("custom-")
            else "",
        )

        def on_intent(cid: str) -> None:
            base.container_id = cid
            self._record(page.name, base)

        if not previous_container:
            self._record(page.name, base)  # intent before any API call
        try:
            outcome = publish_manifest(
                self.fake,
                manifest,
                previous_container,
                on_intent,
                delays=[0.0] * 5,
                retries=3,
                sleep=lambda _s: None,
            )
        except SimulatedCrashError:
            self.log(f"[crash] {slot_key}: runner died right after publishing (intent kept)")
            return
        except PublishError as exc:
            base.status = STATUS_FAILED
            self._record(page.name, base)
            self.log(f"[fail] {slot_key}: {exc}; will retry next run")
            return
        base.status, base.media_id, base.permalink = (
            STATUS_PUBLISHED,
            outcome.media_id,
            outcome.permalink,
        )
        self._record(page.name, base)
        self._finish(page, local_dt, result, outcome.reconciled)

    def _finish(
        self, page: Page, local_dt: datetime, result: BuildResult, reconciled: bool
    ) -> None:
        """Keep the sample, remove the hosted copy, report the post and check stock."""
        manifest = result.manifest
        if manifest is None or result.media_path is None:
            return
        slot_key = manifest.slot_key
        ext = ".jpg" if manifest.format == "image" else ".mp4"
        sample = self.out / "samples" / f"{slot_key}{ext}"
        sample.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(result.media_path, sample)
        self.hosting.remove(page.name, slot_key, ext)
        note = " (reconciled, not re-posted)" if reconciled else ""
        self.log(
            f"[post] {slot_key} {manifest.format}/{manifest.audio_mode or '-'} "
            f"fallback={manifest.fallback or 'none'}{note}"
        )
        self.posts.append(
            SimPost(
                page.name,
                slot_key,
                local_dt.strftime("%a %d %b %H:%M %Z"),
                manifest.format,
                manifest.audio_mode,
                manifest.fallback,
                manifest.template,
                manifest.content_id,
                result.entry["text"] if result.entry else "",
                manifest.caption,
                f"samples/{sample.name}",
                [
                    {"attempt": a, "passed": r.passed, "summary": r.summary()}
                    for a, r in result.gates
                ],
                [e for e in self.events if slot_key in e],
            )
        )
        warn = int(self.cfg.section("alerts", "low_stock_warning"))
        if result.unused_left < warn:
            per_day = max(len(page.slots), 1)
            alert = alerts.low_stock(page.title, result.unused_left, result.unused_left / per_day)
            self.alerts[alert.key] = alert

    def run(self) -> dict[str, Any]:
        """Execute the simulation and write the report."""
        tz = self.cfg.timezone
        t0 = time.time()
        with no_instagram_network():
            for tick in _ticks(self.start, self.days, tz):
                local = tick.astimezone(tz)
                if self._day_index(local.date()) >= self.days:
                    break
                if (
                    "missed_runs" in self._injected(local.date())
                    and MISSED_LOCAL_HOURS[0] <= local.hour < MISSED_LOCAL_HOURS[1]
                ):
                    continue
                for due in plan(self.cfg, tick, self.states):
                    self._publish(self.cfg.page(due.page), due.slot_key, local, due.format)
        return self._report(time.time() - t0)

    def _report(self, seconds: float) -> dict[str, Any]:
        published = self.fake.published_slots()
        expected = sum(len(p.slots) for p in self.cfg.enabled_pages) * self.days
        fallbacks: dict[str, int] = {}
        for p in self.posts:
            fallbacks[p.fallback or "none"] = fallbacks.get(p.fallback or "none", 0) + 1
        data = {
            "start": self.start.isoformat(),
            "days": self.days,
            "seconds": round(seconds),
            "expected_slots": expected,
            "published": len(published),
            "double_posts": len(published) - len(set(published)),
            "fallbacks": fallbacks,
            "alerts": [a.title for a in self.alerts.values()],
            "stock_left": {
                n: len(b.entries) - len(self.states[n].last_used())
                for n, b in self.builders.items()
            },
            "instagram_calls": len(self.fake.calls),
            "posts": [p.__dict__ for p in self.posts],
            "events": self.events,
        }
        (self.out / "report.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        (self.out / "fake-instagram-calls.json").write_text(
            json.dumps(self.fake.calls, indent=1), encoding="utf-8"
        )
        (self.out / "index.html").write_text(_html(data), encoding="utf-8")
        shutil.rmtree(self.out / "build", ignore_errors=True)
        return data


def _html(data: dict[str, Any]) -> str:
    esc = html.escape
    cards = []
    for p in data["posts"]:
        media = media_tag(p["sample"], p["format"])
        gates = "<br>".join(esc(f"{g['attempt']}: {g['summary']}") for g in p["gates"])
        cards.append(
            f'<div class="card">{media}<h3>{esc(p["local_time"])} &middot; {esc(p["format"])}'
            f' {esc(p["audio_mode"])}</h3><p class="meta">template {esc(p["template"])}'
            f" &middot; fallback {esc(p['fallback'] or 'none')}</p>"
            f'<pre>{esc(p["caption"])}</pre><p class="gates">{gates}</p></div>'
        )
    summary = (
        f"{data['published']} of {data['expected_slots']} slots posted &middot; "
        f"double posts: {data['double_posts']} &middot; fallbacks: {esc(str(data['fallbacks']))}"
    )
    alerts_html = "".join(f"<li>{esc(a)}</li>" for a in data["alerts"]) or "<li>none</li>"
    return f"""<!doctype html><meta charset="utf-8"><title>Rehearsal samples</title>
<style>body{{font:15px system-ui;margin:24px;background:#f6f4ef;color:#222}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}}
.card{{background:#fff;border-radius:10px;padding:12px;box-shadow:0 1px 3px #0002}}
img,video{{width:100%;border-radius:6px;background:#000}}h3{{margin:8px 0 2px;font-size:15px}}
.meta,.gates{{color:#666;font-size:12px}}pre{{white-space:pre-wrap;font:13px system-ui}}</style>
<h1>Rehearsal: {esc(data["start"])}, {data["days"]} days</h1><p>{summary}</p>
<h2>Alerts that would have been raised</h2><ul>{alerts_html}</ul>
<div class="grid">{"".join(cards)}</div>"""


def run_rehearsal(
    cfg: Config, days: int, start: date | None, out: Path | None, failures: bool
) -> dict[str, Any]:
    """Entry point used by the CLI."""
    start = start or default_start(cfg.timezone, datetime.now(cfg.timezone).date(), days)
    out = out or ROOT / str(cfg.section("rehearsal", "output_dir")) / f"{start.isoformat()}-{days}d"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    return Rehearsal(cfg, out, days, start, failures).run()
