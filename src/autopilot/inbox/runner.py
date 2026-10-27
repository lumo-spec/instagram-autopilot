"""One inbox run: scan, post what is due, file the copies, raise or clear alerts.

Folder layout inside the page's folder (``WELLITSTARTED_DIR``)::

    postthem/<name>/        the owner's folders: only ever read
    working/<dest>/<name>/  our copy while it is being posted
    posted/<name>/          the copy after posting + "<Destination>.txt" (post id and link)
    failed/<name>/          the copy that could not be posted + "<Destination> - reason.txt"
    PAUSE.txt               present = post nothing at all
    .autopilot/             ledger.json, log.txt, lock

An empty drop folder at a slot is skipped silently. Deleting a folder from ``failed`` makes that
video postable again.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from autopilot.errors import AutopilotError, InboxError, PublishError, TransientPublishError
from autopilot.inbox import schedule
from autopilot.inbox.config import Destination, InboxConfig
from autopilot.inbox.destinations import Posted, Publisher
from autopilot.inbox.folders import Candidate, Probe, ScanResult, file_sha256, scan
from autopilot.inbox.ledger import Ledger
from autopilot.ops.alerts import Alert, AlertPlan, make_alert, page_slug

LOCK_STALE = timedelta(hours=2)


@dataclass
class RunReport:
    """What one run did (printed for the owner and the log)."""

    paused: bool = False
    lines: list[str] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    alerts: AlertPlan = field(default_factory=AlertPlan)

    def say(self, line: str) -> None:
        """Add a line."""
        self.lines.append(line)


class InboxRunner:
    """Runs the inbox of one page."""

    def __init__(
        self,
        cfg: InboxConfig,
        root: Path,
        publishers: dict[str, Publisher],
        probe: Probe,
        clock: Callable[[], datetime],
    ) -> None:
        self.cfg = cfg
        self.root = root
        self.publishers = publishers
        self.probe = probe
        self.clock = clock
        self.state_dir = root / ".autopilot"
        self.ledger = Ledger.load(self.state_dir / "ledger.json")
        self.slug = page_slug(cfg.title)

    # --- helpers ------------------------------------------------------------------------------
    @property
    def paused(self) -> bool:
        """True while PAUSE.txt exists."""
        return (self.root / self.cfg.pause_file).exists()

    def _log(self, report: RunReport) -> None:
        if not report.lines:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.clock().astimezone(self.cfg.tz).strftime("%Y-%m-%d %H:%M")
        with (self.state_dir / "log.txt").open("a", encoding="utf-8") as f:
            for line in report.lines:
                f.write(f"{stamp}  {line}\n")

    def _lock(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.state_dir / "lock"
        now = self.clock().timestamp()
        if lock.exists() and now - lock.stat().st_mtime < LOCK_STALE.total_seconds():
            raise InboxError("another inbox run is still going (try again in a few minutes)")
        lock.write_text(str(os.getpid()), encoding="utf-8")
        return lock

    def scan(self) -> ScanResult:
        """Ready, invalid and still-arriving folders (updates the hash cache)."""
        return scan(
            self.cfg,
            self.root / self.cfg.inbox,
            self.probe,
            self.clock().timestamp(),
            self.ledger.fingerprints,
            self.ledger.first_seen,
        )

    def queue(self, dest: Destination, ready: list[Candidate]) -> list[Candidate]:
        """Videos still to post to ``dest``, in arrival order (each video once)."""
        posted, failed = self.ledger.posted(dest.name), self.ledger.failed(dest.name)
        seen: set[str] = set()
        out = []
        for c in ready:
            if c.kind not in dest.kinds or c.fingerprint in seen:
                continue
            seen.add(c.fingerprint)
            if c.fingerprint not in posted and c.fingerprint not in failed:
                out.append(c)
        return out

    def _when(self) -> str:
        return self.clock().astimezone(self.cfg.tz).strftime("%a %d %b %Y %H:%M (UK time)")

    def _file_copy(self, work: Path, bucket: str, name: str, note_name: str, note: str) -> Path:
        """Move our working copy into posted/ or failed/ (merging with an earlier copy of the
        same folder) and write the note file next to it."""
        target = self.root / bucket / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(work, ignore_errors=True)
        else:
            shutil.move(str(work), str(target))
        (target / note_name).write_text(note + "\n", encoding="utf-8")
        return target

    def _working_copy(self, dest: str, video: Candidate) -> Candidate:
        work = self.root / "working" / dest / video.name
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        for src in (video.video, video.cover, video.copy):
            shutil.copy2(src, work / src.name)
        copy = video.with_folder(work)
        if file_sha256(copy.video) != video.fingerprint:
            raise TransientPublishError("the video changed while it was being copied")
        return copy

    # --- one slot -----------------------------------------------------------------------------
    def post(self, dest: Destination, video: Candidate, slot_key: str, report: RunReport) -> str:
        """Post ``video`` to ``dest`` now; returns the slot outcome."""
        pub = self.publishers[dest.name]
        sha = video.fingerprint
        problems = pub.problems(video)
        if problems:
            return self._fail(dest, video, None, "; ".join(problems), report)
        work: Candidate | None = None
        try:
            work = self._working_copy(dest.name, video)
            result = pub.publish(
                work,
                slot_key,
                self.ledger.intent(dest.name, sha),
                lambda intent: self.ledger.set_intent(dest.name, sha, intent),
            )
        except TransientPublishError as exc:
            n = self.ledger.add_attempt(dest.name, sha)
            if n >= self.cfg.max_attempts:
                return self._fail(dest, video, work, f"{exc} ({n} tries)", report)
            report.say(f"{dest.label}: {video.name} hit a temporary problem ({exc}); next slot")
            if work is not None:  # copied afresh at the next slot
                shutil.rmtree(work.folder, ignore_errors=True)
            return "retry"
        except (PublishError, AutopilotError) as exc:
            return self._fail(dest, video, work, str(exc), report)
        self._done(dest, video, work, result, report)
        return "posted"

    def _done(
        self,
        dest: Destination,
        video: Candidate,
        work: Candidate,
        result: Posted,
        report: RunReport,
    ) -> None:
        note = [
            f"Posted to {dest.label}"
            + (" (found already posted after an interruption)" if result.reconciled else ""),
            f"When: {self._when()}",
            f"Post ID: {result.media_id}",
            f"Link: {result.url}",
            f"From: {self.cfg.inbox}\\{video.name}",
        ]
        if result.note:
            note.append(f"Note: {result.note}")
        folder = self._file_copy(
            work.folder, "posted", video.name, f"{dest.label}.txt", "\n".join(note)
        )
        self.ledger.mark_posted(
            dest.name,
            video.fingerprint,
            {
                "name": video.name,
                "id": result.media_id,
                "url": result.url,
                "at": self.clock().isoformat(),
                "folder": str(folder),
            },
        )
        report.posted.append(f"{dest.name}:{video.name}")
        report.say(f"{dest.label}: posted {video.name} -> {result.url}")

    def _fail(
        self,
        dest: Destination,
        video: Candidate,
        work: Candidate | None,
        reason: str,
        report: RunReport,
    ) -> str:
        if work is None or not work.folder.exists():
            work = self._working_copy_safe(dest.name, video)
        note = (
            f"Not posted to {dest.label} ({self._when()}).\nReason: {reason}\n"
            "To try again: fix the folder in postthem, then delete this folder from failed."
        )
        folder = self._file_copy(
            work.folder, "failed", video.name, f"{dest.label} - reason.txt", note
        )
        self.ledger.mark_failed(
            dest.name,
            video.fingerprint,
            {
                "name": video.name,
                "reason": reason,
                "at": self.clock().isoformat(),
                "folder": str(folder),
            },
        )
        report.failed.append(f"{dest.name}:{video.name}")
        report.say(f"{dest.label}: {video.name} FAILED: {reason}")
        report.alerts.add(self._failed_alert(dest, video.name))
        return "failed"

    def _working_copy_safe(self, dest: str, video: Candidate) -> Candidate:
        work = self.root / "working" / dest / video.name
        work.mkdir(parents=True, exist_ok=True)
        for src in (video.video, video.cover, video.copy):
            if src.exists() and not (work / src.name).exists():
                shutil.copy2(src, work / src.name)
        return video.with_folder(work)

    # --- alerts -------------------------------------------------------------------------------
    def _alert(self, key: str, title: str, body: str) -> Alert:
        """An alert; a folder name that fails the public-safety check is left out."""
        try:
            return make_alert(key, title, body)
        except ValueError:
            return make_alert(
                f"inbox-{self.slug}-problem",
                title,
                "Open the failed folder in the page's folder: the reason file says what to fix.",
            )

    def _failed_alert(self, dest: Destination, name: str) -> Alert:
        return self._alert(
            f"inbox-{self.slug}-failed-{page_slug(dest.name)}-{page_slug(name)}"[:90],
            f"ACTION NEEDED [{self.cfg.title}]: a video could not go to {dest.label}",
            f"The video folder {name!r} was moved (as a copy) to the failed folder with a "
            "reason file. Fix the folder in postthem, then delete it from failed to try again.",
        )

    def low_stock(self, result: ScanResult, report: RunReport) -> None:
        """Alert when a destination's videos run out within ``low_stock_days``; clear the alert
        once there are enough again. Failure alerts are left for the owner to close."""
        now = self.clock()
        for dest in self.cfg.enabled:
            left = len(self.queue(dest, result.ready))
            slots = schedule.upcoming(dest, self.cfg.tz, now, left + 1)
            runs_out = slots[left].local if len(slots) > left else None
            key = f"inbox-{self.slug}-low-{page_slug(dest.name)}"
            if runs_out is not None and runs_out - now < timedelta(days=self.cfg.low_stock_days):
                report.alerts.add(
                    self._alert(
                        key,
                        f"ACTION NEEDED [{self.cfg.title}]: {dest.label} is running out of "
                        "videos — add more to postthem",
                        f"{dest.label} has {left} video{'' if left == 1 else 's'} left and runs "
                        f"out on {runs_out:%a %d %b at %H:%M} (UK time), "
                        f"less than {self.cfg.low_stock_days} days away. Drop new video folders "
                        "into postthem; empty slots are skipped without posting.",
                    )
                )
            else:
                report.alerts.resolved.append(key)

    def _invalid(self, result: ScanResult, report: RunReport) -> None:
        for bad in result.rejected:
            seen = self.ledger.invalid.get(bad.name)
            if seen and seen.get("signature") == bad.signature and Path(seen["folder"]).exists():
                continue
            target = self.root / "failed" / bad.name
            target.mkdir(parents=True, exist_ok=True)
            for f in bad.folder.iterdir():
                if f.is_file() and not (target / f.name).exists():
                    shutil.copy2(f, target / f.name)
            (target / "reason.txt").write_text(
                f"This folder cannot be posted ({self._when()}).\nReason: {bad.reason}\n"
                "A folder needs one .mp4, cover.png and publication-copy.txt. Fix it in "
                "postthem, then delete this folder from failed.\n",
                encoding="utf-8",
            )
            self.ledger.invalid[bad.name] = {
                "signature": bad.signature,
                "reason": bad.reason,
                "folder": str(target),
            }
            report.say(f"{bad.name}: cannot be posted: {bad.reason}")
            report.alerts.add(
                self._alert(
                    f"inbox-{self.slug}-invalid-{page_slug(bad.name)}"[:90],
                    f"ACTION NEEDED [{self.cfg.title}]: a video folder is incomplete",
                    f"The folder {bad.name!r} in postthem cannot be posted ({bad.reason}). "
                    "A copy with a reason file is in the failed folder.",
                )
            )

    # --- entry points -------------------------------------------------------------------------
    def run(self) -> RunReport:
        """The scheduled run (every few minutes): post whatever slot is due."""
        return self._guarded(self._run)

    def post_now(self, dest_name: str) -> RunReport:
        """Post the next video to one destination now, whatever the schedule (live tests)."""
        dest = self.cfg.destination(dest_name)
        return self._guarded(lambda r: self._post_now(dest, r))

    def _guarded(self, body: Callable[[RunReport], None]) -> RunReport:
        report = RunReport()
        if self.paused:
            report.paused = True
            self._mark_paused()
            self.ledger.save()
            return report
        lock = self._lock()
        try:
            for cleared in self.ledger.forget_cleared_failures():
                report.say(f"{cleared}: failed folder deleted, will be tried again")
            body(report)
        finally:
            self.ledger.save()
            lock.unlink(missing_ok=True)
            self._log(report)
        return report

    def _mark_paused(self) -> None:
        """Slots that pass while paused are not posted later when the pause ends."""
        for dest in self.cfg.enabled:
            for slot in schedule.due(dest, self.cfg.tz, self.clock(), self.cfg.catchup_minutes):
                if not self.ledger.slot_done(slot.key):
                    self.ledger.mark_slot(slot.key, "paused")

    def _run(self, report: RunReport) -> None:
        result = self.scan()
        self._invalid(result, report)
        for dest in self.cfg.enabled:
            due = [
                s
                for s in schedule.due(dest, self.cfg.tz, self.clock(), self.cfg.catchup_minutes)
                if not self.ledger.slot_done(s.key)
            ]
            if not due:
                continue
            for old in due[:-1]:
                self.ledger.mark_slot(old.key, "missed")
                report.say(f"{dest.label}: slot {old.local:%a %H:%M} missed (PC was off)")
            slot = due[-1]
            todo = self.queue(dest, result.ready)
            if not todo:
                self.ledger.mark_slot(slot.key, "empty")  # silent: no error, no post
                continue
            outcome = self.post(dest, todo[0], slot.key, report)
            self.ledger.mark_slot(slot.key, outcome, todo[0].fingerprint)
        if not result.waiting:  # folders still being copied in: judge stock next run
            self.low_stock(result, report)

    def _post_now(self, dest: Destination, report: RunReport) -> None:
        result = self.scan()
        todo = self.queue(dest, result.ready)
        if not todo:
            report.say(f"{dest.label}: nothing to post (postthem has no new video for it)")
            return
        key = f"{dest.name}-now-{self.clock().astimezone(self.cfg.tz):%Y-%m-%d-%H%M%S}"
        outcome = self.post(dest, todo[0], key, report)
        self.ledger.mark_slot(key, outcome, todo[0].fingerprint)

    # --- status -------------------------------------------------------------------------------
    def status(self) -> list[str]:
        """Plain-English summary for the owner."""
        result = self.scan()
        now = self.clock()
        out = [f"{self.cfg.title}: {'PAUSED (PAUSE.txt)' if self.paused else 'running'}"]
        if result.waiting:
            out.append(f"  still arriving (not touched yet): {', '.join(result.waiting)}")
        for bad in result.rejected:
            out.append(f"  cannot be posted: {bad.name}: {bad.reason}")
        for dest in self.cfg.destinations:
            todo = self.queue(dest, result.ready)
            nxt = schedule.upcoming(dest, self.cfg.tz, now, 1)
            state = "on" if dest.enabled else "off"
            line = f"  {dest.label} ({state}): {len(todo)} waiting"
            if nxt:
                line += f"; next slot {nxt[0].local:%a %d %b %H:%M}"
            if todo:
                line += f"; next video {todo[0].name}"
            out.append(line)
            out.append(
                f"    posted so far: {len(self.ledger.posted(dest.name))}, "
                f"failed: {len(self.ledger.failed(dest.name))}"
            )
        return out


def describe(report: RunReport) -> dict[str, Any]:
    """JSON-friendly summary of a run."""
    return {
        "paused": report.paused,
        "posted": report.posted,
        "failed": report.failed,
        "log": report.lines,
        "alerts": [a.title for a in report.alerts.raise_],
    }
