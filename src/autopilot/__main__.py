"""Command line: ``python -m autopilot <command>``.

Commands available now: validate, rehearse, sample, pronunciation-report, status-local.
plan | build | publish | record | renew run inside GitHub Actions (added in stage 2).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from autopilot.config import ROOT, load_config
from autopilot.errors import AutopilotError


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate every enabled page's content bank (schema + near-duplicates)."""
    from autopilot.content.validate import load_bank, validate_bank

    cfg = load_config()
    ok = True
    for page in cfg.enabled_pages if not args.page else (cfg.page(args.page),):
        bank = load_bank(page.dir / "content.json")
        report = validate_bank(
            bank,
            int(cfg.section("limits", "text_max_chars", page.type)),
            float(cfg.section("near_duplicate", "threshold")),
        )
        print(
            f"{page.title}: {report.total} entries, {len(report.errors)} invalid, "
            f"{len(report.duplicates)} near-duplicate pairs"
        )
        for eid, problems in report.errors.items():
            print(f"  {eid}: {', '.join(problems)}")
        for m in report.duplicates:
            print(f"  near-duplicate {m.a} ~ {m.b} ({m.reason} {m.score:.0f})")
        ok &= report.ok
    return 0 if ok else 1


def cmd_pronunciation_report(args: argparse.Namespace) -> int:
    """List bank words outside the dictionary that are neither overridden nor waived."""
    import yaml

    from autopilot.content.validate import load_bank
    from autopilot.gates.spelling import unknown_words

    cfg = load_config()
    page = cfg.page(args.page)
    bank = load_bank(page.dir / "content.json")
    pron = yaml.safe_load((page.dir / "pronunciations.yaml").read_text(encoding="utf-8")) or {}
    covered = {w.lower() for w in (pron.get("words") or {})} | {
        w.lower() for w in pron.get("waived") or []
    }
    unknown = unknown_words([e["text"] for e in bank["entries"]], frozenset())
    missing = sorted(unknown - covered)
    print(
        f"{page.title}: {len(unknown)} non-dictionary words; {len(unknown) - len(missing)} covered."
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
    print(
        json.dumps(
            {
                k: data[k]
                for k in (
                    "start",
                    "days",
                    "seconds",
                    "expected_slots",
                    "published",
                    "double_posts",
                    "fallbacks",
                    "alerts",
                    "stock_left",
                )
            },
            indent=1,
        )
    )
    return 0 if data["double_posts"] == 0 else 1


def cmd_sample(args: argparse.Namespace) -> int:
    """Render one sample of every template (images, and reels in each audio mode)."""
    from autopilot.rehearsal.samples import render_samples

    cfg = load_config()
    out = Path(args.out) if args.out else ROOT / "rehearsal-output" / "samples-by-template"
    return render_samples(cfg, cfg.page(args.page), out, reels=not args.images_only)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(prog="autopilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="validate content banks")
    p.add_argument("--page")
    p.set_defaults(func=cmd_validate)
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
    p = sub.add_parser("sample", help="render one sample per template")
    p.add_argument("page")
    p.add_argument("--out")
    p.add_argument("--images-only", action="store_true")
    p.set_defaults(func=cmd_sample)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AutopilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
