"""Command line: ``python -m autopilot <command>``.

Local commands: validate, queue, preview, generate-art, art-key-check, build, export, hold,
release, rehearse, pronunciation-report, inbox (video pages posted from a folder on this PC).
The GitHub Actions jobs (plan, build, host, publish, record, renew, ...) live in
``python -m autopilot.jobs``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from autopilot.config import ROOT, Config, Page, load_config
from autopilot.content.queue import Post
from autopilot.errors import AutopilotError


def _pages(cfg: Config, name: str | None) -> tuple[Page, ...]:
    return cfg.enabled_pages if not name else (cfg.page(name),)


def cmd_validate(args: argparse.Namespace) -> int:
    """Check every queued post of each story page (structure, text gates, approvals)."""
    from autopilot.content.queue import load_queue
    from autopilot.pipeline import StoryBuilder
    from autopilot.rehearsal.fakes import LocalHosting

    cfg = load_config()
    ok = True
    for page in _pages(cfg, args.page):
        items, errors = load_queue(page.dir)
        builder = StoryBuilder(cfg, page, ROOT / ".cache" / "validate", LocalHosting(Path()))
        bad = 0
        for item in items:
            post = item.post
            report = builder.text_gates(post, builder.caption(post, "voice" in post.audio))
            if not report.passed:
                bad += 1
                print(f"  {post.id}: {report.summary()}")
            for problem in item.problems:
                label = "" if problem.startswith("on hold") else "approval not valid: "
                print(f"  {post.id}: {label}{problem}")
        for e in errors:
            print(f"  INVALID {e}")
        approved = sum(1 for i in items if i.publishable)
        print(
            f"{page.title}: {len(items) + len(errors)} queued posts, {approved} approved, "
            f"{bad + len(errors)} with problems"
        )
        ok &= not bad and not errors
    return 0 if ok else 1


def cmd_queue(args: argparse.Namespace) -> int:
    """List queued posts and what each still needs."""
    from autopilot.content.queue import load_queue, publishable

    cfg = load_config()
    for page in _pages(cfg, args.page):
        items, errors = load_queue(page.dir)
        order = {i.post.id: n for n, i in enumerate(publishable(items, set()), start=1)}
        print(f"{page.title} queue ({len(order)} approved):")
        for item in items:
            post = item.post
            if post.hold:
                status = f"ON HOLD ({post.hold}) — release with: autopilot release"
            elif item.publishable:
                status = f"APPROVED #{order[post.id]}"
            elif item.approval is not None:
                status = "CHANGED since approval — run Build again"
            elif post.missing_art():
                status = (
                    f"needs art: {', '.join(post.missing_art())} "
                    f"(draw it, or: generate-art {page.name} {post.id})"
                )
            else:
                status = "art supplied — ready for Build"
            print(f"  {post.id:40s} {post.format:8s} {status}")
        for e in errors:
            print(f"  INVALID {e}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Render queued posts (placeholder art where missing) into a review page."""
    from autopilot.rehearsal.preview import preview_queue

    cfg = load_config()
    page = cfg.page(args.page)
    out = Path(args.out) if args.out else ROOT / "rehearsal-output" / "queue-preview"
    index, failures = preview_queue(cfg, page, out, args.post or None)
    print(f"Review page: {index}")
    return 1 if failures else 0


def cmd_build(args: argparse.Namespace) -> int:
    """``Build <post-id>``: compose with the real art, run every gate, approve if all pass."""
    from autopilot.rehearsal.preview import build_and_approve

    cfg = load_config()
    page = cfg.page(args.page)
    preview_page = page.dir / "queue" / args.post_id / "preview" / "index.html"
    try:
        preview, index = build_and_approve(cfg, page, args.post_id)
    except AutopilotError as exc:
        print(f"NOT APPROVED: {exc}")
        if preview_page.exists():
            print(f"Preview: {preview_page}")
        return 1
    print(f"APPROVED {args.post_id}: {preview.report.summary()}")
    print(f"Preview: {index}")
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    """Put one post on hold, or release it. A held post is never published or exported."""
    from autopilot.content.queue import find_post, set_hold

    cfg = load_config()
    post = find_post(cfg.page(args.page).dir, args.post_id)
    set_hold(post.dir, None if args.release else args.reason)
    print(f"{'RELEASED' if args.release else 'ON HOLD'}: {post.id}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """``Export <post-id>``: finished files + caption + notes for posting by hand."""
    from autopilot.export import NOTES, export_post

    cfg = load_config()
    page = cfg.page(args.page)
    out = Path(args.out) if args.out else ROOT / "exports"
    try:
        result = export_post(
            cfg,
            page,
            args.post_id,
            out,
            reel=args.reel and not args.no_reel,
            music=args.music or "",
        )
    except AutopilotError as exc:
        print(f"NOT EXPORTED: {exc}")
        return 1
    print(f"EXPORTED {args.post_id} -> {result.folder}")
    print(f"  files in order: {', '.join(result.media)}")
    label = "YES (" + "; ".join(result.ai_label) + ")" if result.ai_label else "not needed"
    print(f"  AI label: {label}")
    if result.reel is not None:
        if result.reel.made:
            print(
                f"  story reel: story-reel.mp4 ({result.reel.seconds:.0f} s), {result.reel.music}"
            )
        else:
            print(f"  story reel NOT made: {result.reel.problems}")
    print(f"  read {result.folder / NOTES} before posting")
    return 0


def _slide_numbers(text: str | None) -> set[int]:
    try:
        return {int(n) for n in (text or "").replace(" ", "").split(",") if n}
    except ValueError:
        raise AutopilotError("--slides takes slide numbers like 2,5") from None


def _art_dry_run(cfg: Config, page: Page, posts: list[Post], redo: set[int]) -> int:
    from autopilot.art.factory import characters, settings
    from autopilot.art.generate import NAMED, generation_prompt

    art = settings(cfg)
    chars = characters(page)
    per_try = float(art["providers"].get(art["provider"], {}).get("usd_per_image", 0)) + float(
        art["checkers"].get(art["checker"], {}).get("usd_per_check", 0)
    )
    count = 0
    for post in posts:
        for s in post.slides:
            if s.index not in redo and (post.dir / s.art).is_file():
                continue
            count += 1
            names = [
                chars["characters"][n]["name"] for n in s.brief.get("characters", []) if n in NAMED
            ]
            print(f"--- {post.id} {s.art} (references: {', '.join(names) or 'none'}) ---")
            print(generation_prompt(post, s, chars, names, s.index > 1, []))
    rate = float(art["usd_to_gbp"])
    tries = 1 + int(art["max_regenerations"])
    print(
        f"\n{count} pictures to draw. Estimated cost: about £{count * per_try * rate:.2f} "
        f"if every picture passes first time, at most £{count * per_try * rate * tries:.2f} "
        f"(cap £{float(art['monthly_cap_gbp']):.2f} a month). Nothing was sent."
    )
    return 0


def _rehearsal_copy(cfg: Config, page: Page, out: Path) -> tuple[Config, Page]:
    """``generate-art --fake`` works on a copy of the page, never on the real queue."""
    import dataclasses
    import shutil

    copy = out / "page"
    shutil.copytree(page.dir, copy, ignore=shutil.ignore_patterns("archive", "preview"))
    page = dataclasses.replace(page, base_dir=copy)
    return dataclasses.replace(cfg, pages=(page,)), page


def _art_posts(page: Page, ids: list[str], all_missing: bool) -> list[Post]:
    from autopilot.content.queue import find_post, load_queue

    if ids:
        return [find_post(page.dir, pid) for pid in ids]
    if not all_missing:
        raise AutopilotError("name one or more post ids, or use --all-missing")
    items, _ = load_queue(page.dir)
    ready = [i for i in items if i.approval is None and not i.post.hold and i.post.missing_art()]
    return [i.post for i in sorted(ready, key=lambda i: (i.post.priority, i.post.id))]


def cmd_generate_art(args: argparse.Namespace) -> int:
    """``generate-art``: draw, check and save missing art (generate only; nothing is posted)."""
    from datetime import datetime

    from autopilot.art.factory import make_generator, send_alerts
    from autopilot.ops.alerts import AlertPlan
    from autopilot.rehearsal.preview import preview_queue

    cfg = load_config()
    page = cfg.page(args.page)
    redo = _slide_numbers(args.slides)
    ledger = None
    if args.fake:
        out = ROOT / "rehearsal-output" / "art-rehearsal" / datetime.now().strftime("%Y%m%d-%H%M%S")
        cfg, page = _rehearsal_copy(cfg, page, Path(args.out) if args.out else out)
        ledger = page.dir.parent / "art-spend.json"
        print(f"Rehearsal (fake painter and checker, no cost) in {page.dir.parent}")
    posts = _art_posts(page, args.post, args.all_missing)
    if args.dry_run:
        return _art_dry_run(cfg, page, posts, redo)
    gen = make_generator(cfg, page, fake=args.fake, ledger_path=ledger)
    plan = AlertPlan()
    finished: list[str] = []
    ok = True
    for post in posts:
        print(f"{post.id}: £{gen.ledger.left_gbp():.2f} of this month's art budget left")
        result = gen.generate(post, redo)
        plan.merge(result.alerts)
        print(f"{post.id}: {result.status.upper()} {result.message}".rstrip())
        if result.status == "done":
            finished.append(post.id)
        ok &= result.status in ("done", "nothing-to-do", "skipped")
        if result.status == "budget":
            break
    if finished:
        out = (
            ROOT / "rehearsal-output" / "art-preview"
            if not args.fake
            else page.dir.parent / "preview"
        )
        index, _ = preview_queue(cfg, page, out, finished)
        print(f"Look at the new art: {index}")
        print(f"If you like it: python -m autopilot build {args.page} <post-id>")
    for alert in plan.raise_:
        print(f"ALERT: {alert.title}")
    if args.fake or args.no_alert:
        return 0 if ok else 1
    note = send_alerts(cfg, plan)
    if note:
        print(note)
    return 0 if ok else 1


def cmd_art_key_check(args: argparse.Namespace) -> int:
    """Prove the image key works and the configured models exist (free calls only)."""
    from autopilot.art.factory import check_models

    for name in check_models(load_config()):
        print(f"OK: key accepted, model {name} available")
    return 0


def cmd_pronunciation_report(args: argparse.Namespace) -> int:
    """List non-dictionary words in reel narrations that are neither overridden nor waived."""
    import yaml

    from autopilot.content.queue import load_queue
    from autopilot.gates.spelling import unknown_words

    cfg = load_config()
    page = cfg.page(args.page)
    items, _ = load_queue(page.dir)
    texts = [t for i in items if i.post.format == "reel" for t in i.post.plain_texts]
    pron = yaml.safe_load((page.dir / "pronunciations.yaml").read_text(encoding="utf-8")) or {}
    covered = {w.lower() for w in (pron.get("words") or {})} | {
        w.lower() for w in pron.get("waived") or []
    }
    unknown = unknown_words(texts, frozenset())
    missing = sorted(unknown - covered)
    print(
        f"{page.title}: {len(unknown)} non-dictionary words in narrations; "
        f"{len(unknown) - len(missing)} covered."
    )
    for w in missing:
        print(f"  NOT COVERED: {w}")
    return 1 if missing else 0


def cmd_rehearse(args: argparse.Namespace) -> int:
    """Run the full practice (no Instagram, no secrets)."""
    from autopilot.rehearsal.runner import run_rehearsal

    cfg = load_config()
    start = date.fromisoformat(args.start) if args.start else None
    data = run_rehearsal(
        cfg, args.days, start, Path(args.out) if args.out else None, not args.no_failures
    )
    keys = (
        "start",
        "days",
        "seconds",
        "expected_slots",
        "published",
        "skipped",
        "double_posts",
        "posts_reused",
        "fallbacks",
        "alerts",
        "queue_left",
    )
    print(json.dumps({k: data[k] for k in keys}, indent=1))
    return 0 if data["double_posts"] == 0 and data["posts_reused"] == 0 else 1


def cmd_inbox(args: argparse.Namespace) -> int:
    """Video inbox of a page (e.g. wellitstarted): status, run, post-now, rehearse, set-up."""
    import getpass
    from datetime import UTC, datetime

    from autopilot.inbox import setup
    from autopilot.inbox.config import load_inbox_config, resolve_root
    from autopilot.inbox.folders import ffmpeg_probe
    from autopilot.inbox.runner import InboxRunner

    cfg = load_inbox_config(args.page)
    if args.action == "rehearse":
        from autopilot.inbox.rehearsal import run_guarded

        out = Path(args.out) if args.out else ROOT / "rehearsal-output" / f"inbox-{cfg.page}"
        result = run_guarded(
            cfg, out, days=args.days, sample=Path(args.sample) if args.sample else None
        )
        print(json.dumps(result.data, indent=1))
        return 0 if result.passed else 1
    if args.action == "instagram-token":
        token = getpass.getpass("Paste the Instagram token (hidden), then press Enter: ").strip()
        if not token:
            print("nothing pasted; nothing changed")
            return 1
        user = setup.instagram_user_id(token)
        p = setup.prefix(cfg)
        setup.set_values(
            {
                f"{p}_IG_TOKEN": token,
                f"{p}_IG_USER_ID": user,
                f"{p}_IG_TOKEN_SET": date.today().isoformat(),
            }
        )
        print(f"Instagram token saved for {cfg.title} (in .env, which is never committed).")
        return 0
    if args.action == "youtube-login":
        from autopilot.inbox.youtube import YouTubeApi, login

        cid = input("OAuth client ID (from Google Cloud): ").strip()
        secret = getpass.getpass("OAuth client secret (hidden): ").strip()
        refresh = login(cid, secret)
        p = setup.prefix(cfg)
        setup.set_values(
            {
                f"{p}_YT_CLIENT_ID": cid,
                f"{p}_YT_CLIENT_SECRET": secret,
                f"{p}_YT_REFRESH_TOKEN": refresh,
            }
        )
        print(
            f"YouTube connected to the channel: {YouTubeApi(cid, secret, refresh).channel_title()}"
        )
        return 0
    root = resolve_root(cfg)
    clock = lambda: datetime.now(UTC)  # noqa: E731
    if args.action == "status":
        runner = InboxRunner(cfg, root, {}, ffmpeg_probe, clock)
        print("\n".join(runner.status()))
        return 0
    instagram_on = any(d.name.startswith("instagram") for d in cfg.enabled)
    if instagram_on and setup.renew_instagram_token(cfg, date.today()):
        print("Instagram token renewed (valid for another 60 days).")
    runner = InboxRunner(cfg, root, setup.build_publishers(cfg), ffmpeg_probe, clock)
    report = runner.post_now(args.destination) if args.action == "post-now" else runner.run()
    if report.paused:
        print(f"{cfg.title} is paused ({cfg.pause_file} exists): nothing posted.")
        return 0
    for line in report.lines:
        print(line)
    if not args.no_alert:
        from autopilot.art.factory import send_alerts

        note = send_alerts(load_config(), report.alerts)
        if note:
            print(note)
    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915 (one parser per command)
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(prog="autopilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="check queued posts")
    p.add_argument("--page")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("queue", help="list queued posts and what they need")
    p.add_argument("--page")
    p.set_defaults(func=cmd_queue)
    p = sub.add_parser("preview", help="render queued posts into a review page")
    p.add_argument("page")
    p.add_argument("post", nargs="*", help="post ids (default: all)")
    p.add_argument("--out")
    p.set_defaults(func=cmd_preview)
    p = sub.add_parser("build", help="compose a post with its art and approve it")
    p.add_argument("page")
    p.add_argument("post_id")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("export", help="write an approved post's files for posting by hand")
    p.add_argument("page")
    p.add_argument("post_id")
    p.add_argument("--out", help="parent folder (default: exports/)")
    # Story reels are postponed until after the first live post (owner, 2026-10-19): opt-in.
    p.add_argument("--reel", action="store_true", help="carousels: also make the story reel")
    p.add_argument("--no-reel", action="store_true", help="skip the story reel (the default)")
    p.add_argument("--music", help="story reel music: procedural, or a CC0 file in assets/music")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("hold", help="put one post on hold (never published or exported)")
    p.add_argument("page")
    p.add_argument("post_id")
    p.add_argument("reason", nargs="?", default="put on hold by the owner")
    p.set_defaults(func=cmd_hold, release=False)
    p = sub.add_parser("release", help="take a post off hold")
    p.add_argument("page")
    p.add_argument("post_id")
    p.set_defaults(func=cmd_hold, release=True, reason=None)
    p = sub.add_parser("generate-art", help="draw + check missing art (generate only)")
    p.add_argument("page")
    p.add_argument("post", nargs="*", help="post ids (e.g. c01-you-can-miss)")
    p.add_argument("--all-missing", action="store_true", help="every post that still needs art")
    p.add_argument("--slides", help="redraw these slides even if they have art, e.g. 2,5")
    p.add_argument("--dry-run", action="store_true", help="show prompts and cost, send nothing")
    p.add_argument("--fake", action="store_true", help="rehearsal on a copy: fake painter/checker")
    p.add_argument("--no-alert", action="store_true", help="print alerts instead of e-mailing")
    p.add_argument("--out", help="--fake only: rehearsal folder")
    p.set_defaults(func=cmd_generate_art)
    p = sub.add_parser("art-key-check", help="check the image key and model names (free)")
    p.set_defaults(func=cmd_art_key_check)
    p = sub.add_parser("pronunciation-report", help="words the voice may mispronounce")
    p.add_argument("page")
    p.set_defaults(func=cmd_pronunciation_report)
    p = sub.add_parser("rehearse", help="full practice run, never posts")
    p.add_argument("--days", type=int, default=14)
    p.add_argument(
        "--start", help="first simulated local date YYYY-MM-DD (default: spans a clock change)"
    )
    p.add_argument("--out")
    p.add_argument("--no-failures", action="store_true", help="do not inject simulated failures")
    p.set_defaults(func=cmd_rehearse)
    p = sub.add_parser("inbox", help="video page posted from a folder on this PC")
    p.add_argument(
        "action",
        choices=["status", "run", "post-now", "rehearse", "instagram-token", "youtube-login"],
    )
    p.add_argument("page", help="e.g. wellitstarted")
    p.add_argument(
        "destination",
        nargs="?",
        default="instagram-reels",
        help="post-now only: instagram-reels, youtube-shorts or youtube-long",
    )
    p.add_argument("--days", type=int, default=14, help="rehearse: simulated days")
    p.add_argument("--sample", help="rehearse: folder of finished video folders to copy in")
    p.add_argument("--out", help="rehearse: scratch folder")
    p.add_argument("--no-alert", action="store_true", help="print alerts instead of e-mailing")
    p.set_defaults(func=cmd_inbox)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AutopilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
