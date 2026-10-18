"""Full practice run: fake clock, real queue/rendering/gates/state, fake Instagram.

Simulates the 30-minute schedule over N days (spanning a clock change by default), injects one
failure of each kind, and writes a report plus every post for the owner to review. Queue drafts
count as approved and missing art is replaced by placeholders — in rehearsals only.
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
from autopilot.manifest import media_names
from autopilot.ops import alerts
from autopilot.pipeline import BuildResult, Request, StoryBuilder
from autopilot.plan import plan
from autopilot.publish.instagram import publish_manifest
from autopilot.rehearsal.fakes import (
    FakeInstagram,
    LocalHosting,
    SimulatedCrashError,
    no_instagram_network,
)
from autopilot.rehearsal.preview import media_tag
from autopilot.state import (
    STATUS_FAILED,
    STATUS_INTENT,
    STATUS_PUBLISHED,
    STATUS_SKIPPED,
    PageState,
    PostRecord,
    save_state,
)

# N-th posting day of the rehearsal (0-based; days without any slot are not counted) -> failures.
FAILURE_PLAN: dict[int, tuple[str, ...]] = {
    1: ("missed_runs",),
    2: ("container_error",),
    3: ("publish_transient",),
    4: ("crash_after_publish",),
    5: ("queue_empty",),
    6: ("quota_high",),
}
MISSED_WINDOW_H = 2.5  # cron runs "lost" for this long after the slot's time on the missed day
API_FAILURES = ("container_error", "publish_transient", "crash_after_publish")


@dataclass
class SimPost:
    """One simulated post for the report."""

    page: str
    slot_key: str
    local_time: str
    format: str
    audio_mode: str
    fallback: str
    post_id: str
    texts: list[str]
    caption: str
    samples: list[str]
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
            p.name: StoryBuilder(cfg, p, out / "build", self.hosting, rehearsal=True)
            for p in cfg.enabled_pages
        }
        self.posts: list[SimPost] = []
        self.events: list[str] = []
        self.alerts: dict[str, alerts.Alert] = {}
        self.built: dict[str, BuildResult] = {}
        self.injected: set[str] = set()
        self.queue_left: dict[str, int] = {}
        posting_days = [
            d
            for d in (start + timedelta(days=i) for i in range(days))
            if any(s.runs_on(d.weekday()) for p in cfg.enabled_pages for s in p.slots)
        ]
        self.failure_days = {
            posting_days[i]: f for i, f in FAILURE_PLAN.items() if i < len(posting_days)
        }

    def _in_missed_window(self, local: datetime) -> bool:
        """True during the hours just after a slot's time on the 'missed runs' day."""
        hours = local.hour + local.minute / 60
        for page in self.cfg.enabled_pages:
            for slot in page.slots:
                begin = slot.minutes / 60
                if slot.runs_on(local.weekday()) and begin <= hours < begin + MISSED_WINDOW_H:
                    return True
        return False

    def _day_index(self, local: date) -> int:
        return (local - self.start).days

    def _injected(self, local: date) -> tuple[str, ...]:
        return self.failure_days.get(local, ()) if self.failures else ()

    def log(self, msg: str) -> None:
        """Record and print an event."""
        self.events.append(msg)
        print(msg, flush=True)

    def _record(self, page: str, rec: PostRecord) -> None:
        self.states[page].upsert(rec)
        save_state(self.out / "state" / page / "state.json", self.states[page])

    def _alert(self, alert: alerts.Alert) -> None:
        self.alerts[alert.key] = alert

    def _build(
        self, page: Page, slot_key: str, local: date, fmt: str, inject: tuple[str, ...]
    ) -> BuildResult:
        if slot_key not in self.built:
            req = Request(slot_key, local, fmt, inject=frozenset(inject))
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
        if reason.startswith("queue empty"):
            self._alert(alerts.queue_empty(page.title, slot))
        else:
            self._alert(alerts.slot_skipped(page.title, slot, reason))
        self.log(f"[skip] {slot_key}: {reason}")

    def _publish(self, page: Page, slot_key: str, local_dt: datetime, fmt: str) -> None:
        state = self.states[page.name]
        inject = self._injected(local_dt.date())
        result = self._build(page, slot_key, local_dt.date(), fmt, inject)
        for post_id in result.changed:
            self._alert(alerts.approval_changed(page.title, post_id))
        if result.manifest is None:
            self._skip(page, slot_key, local_dt.date(), result.skipped_reason)
            return
        manifest = result.manifest
        slot = slot_key.rsplit("-", 1)[1]
        if slot_key not in self.injected:  # each simulated failure fires once per slot
            self.injected.add(slot_key)
            self.fake.fail_next.update(i for i in inject if i in API_FAILURES)
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
                    slot,
                    STATUS_FAILED,
                    format=manifest.format,
                    content_id=manifest.content_id,
                ),
            )
            return
        prev = state.get(slot_key)
        previous_container: str | None = (
            prev.container_id if prev and prev.status in (STATUS_INTENT, STATUS_FAILED) else None
        )
        base = PostRecord(
            slot_key,
            local_dt.date().isoformat(),
            slot,
            STATUS_INTENT,
            format=manifest.format,
            audio_mode=manifest.audio_mode,
            fallback=manifest.fallback,
            content_id=manifest.content_id,
            template=manifest.template,
            hashtags=list(manifest.hashtags),
            container_id=previous_container or "",
            recorded_at=local_dt.isoformat(timespec="minutes"),
            slides=len(manifest.media_urls) if manifest.format == "carousel" else 0,
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
        """Keep the samples, remove the hosted copies, report the post and check the queue."""
        manifest = result.manifest
        if manifest is None or not result.media_paths:
            return
        slot_key = manifest.slot_key
        samples = []
        names = media_names(slot_key, manifest.format, len(result.media_paths))
        for path, name in zip(result.media_paths, names, strict=True):
            sample = self.out / "samples" / name
            sample.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, sample)
            self.hosting.remove(page.name, name)
            samples.append(f"samples/{name}")
        note = " (reconciled, not re-posted)" if reconciled else ""
        self.log(
            f"[post] {slot_key} {manifest.format} {manifest.content_id} "
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
                manifest.content_id,
                result.post.plain_texts if result.post else [],
                manifest.caption,
                samples,
                [
                    {"attempt": a, "passed": r.passed, "summary": r.summary()}
                    for a, r in result.gates
                ],
                [e for e in self.events if slot_key in e],
            )
        )
        self.queue_left[page.name] = result.queue_left
        if result.queue_left < page.queue_low_warning:
            self._alert(alerts.queue_low(page.title, result.queue_left))

    def run(self) -> dict[str, Any]:
        """Execute the simulation and write the report."""
        tz = self.cfg.timezone
        t0 = time.time()
        with no_instagram_network():
            for tick in _ticks(self.start, self.days, tz):
                local = tick.astimezone(tz)
                if self._day_index(local.date()) >= self.days:
                    break
                if "missed_runs" in self._injected(local.date()) and self._in_missed_window(local):
                    continue
                for due in plan(self.cfg, tick, self.states):
                    self._publish(self.cfg.page(due.page), due.slot_key, local, due.format)
        return self._report(time.time() - t0)

    def _report(self, seconds: float) -> dict[str, Any]:
        published = self.fake.published_slots()
        expected = sum(
            1
            for p in self.cfg.enabled_pages
            for s in p.slots
            for i in range(self.days)
            if s.runs_on((self.start + timedelta(days=i)).weekday())
        )
        skipped = sum(
            1 for st in self.states.values() for r in st.posts if r.status == STATUS_SKIPPED
        )
        fallbacks: dict[str, int] = {}
        for p in self.posts:
            fallbacks[p.fallback or "none"] = fallbacks.get(p.fallback or "none", 0) + 1
        data = {
            "start": self.start.isoformat(),
            "days": self.days,
            "seconds": round(seconds),
            "expected_slots": expected,
            "published": len(published),
            "skipped": skipped,
            "double_posts": len(published) - len(set(published)),
            "posts_reused": len(self.posts) - len({(p.page, p.post_id) for p in self.posts}),
            "fallbacks": fallbacks,
            "alerts": [a.title for a in self.alerts.values()],
            "queue_left": self.queue_left,
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
        media = "".join(media_tag(s, p["format"]) for s in p["samples"])
        gates = "<br>".join(esc(f"{g['attempt']}: {g['summary']}") for g in p["gates"])
        cards.append(
            f'<div class="card"><div class="strip">{media}</div><h3>{esc(p["local_time"])}'
            f" &middot; {esc(p['format'])} &middot; {esc(p['post_id'])}</h3>"
            f'<p class="meta">fallback {esc(p["fallback"] or "none")}</p>'
            f'<pre>{esc(p["caption"])}</pre><p class="gates">{gates}</p></div>'
        )
    summary = (
        f"{data['published']} of {data['expected_slots']} slots posted &middot; "
        f"skipped: {data['skipped']} &middot; double posts: {data['double_posts']} &middot; "
        f"posts reused: {data['posts_reused']}"
    )
    alerts_html = "".join(f"<li>{esc(a)}</li>" for a in data["alerts"]) or "<li>none</li>"
    return f"""<!doctype html><meta charset="utf-8"><title>Rehearsal</title>
<style>body{{font:15px system-ui;margin:24px;background:#f6f1e7;color:#2b1d14}}
.card{{background:#fff;border-radius:10px;padding:12px;margin-bottom:18px;
box-shadow:0 1px 3px #0002}}
.strip{{display:flex;gap:8px;overflow-x:auto}}
.strip img,.strip video{{height:360px;border-radius:6px}}
h3{{margin:8px 0 2px;font-size:15px}}.meta,.gates{{color:#666;font-size:12px}}
pre{{white-space:pre-wrap;font:13px system-ui}}</style>
<h1>Rehearsal: {esc(data["start"])}, {data["days"]} days</h1><p>{summary}</p>
<p>Placeholder art stands in for art not supplied yet;
drafts count as approved in rehearsals only.</p>
<h2>Alerts that would have been raised</h2><ul>{alerts_html}</ul>{"".join(cards)}"""


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
