"""Command line: ``python -m autopilot <command>``.

Local commands: validate, queue, preview, build, rehearse, pronunciation-report.
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
                print(f"  {post.id}: approval not valid: {problem}")
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
            if item.publishable:
                status = f"APPROVED #{order[post.id]}"
            elif item.approval is not None:
                status = "CHANGED since approval — run Build again"
            elif post.missing_art():
                status = f"needs art: {', '.join(post.missing_art())}"
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


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AutopilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
