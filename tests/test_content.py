from __future__ import annotations

import random
from datetime import date
from typing import Any

import pytest

from autopilot.config import ROOT
from autopilot.content.bank import choose_audio_mode, choose_template, select_entry, topic_tags
from autopilot.content.captions import build_caption, load_caption_kit, pick_hashtags
from autopilot.content.sanitize import sanitise_text
from autopilot.content.similarity import compare, find_duplicates, normalise
from autopilot.content.validate import check_fact, check_quote, length_class, validate_bank
from autopilot.errors import ContentError
from autopilot.state import STATUS_PUBLISHED, PageState, PostRecord

DAY = date(2026, 10, 20)
ARCHIVE = ROOT / "pages" / "simplestorymode" / "archive"


def _posted(
    state: PageState, entry_id: str, day: date, slot: str = "morning", tags: list[str] | None = None
) -> None:
    state.upsert(
        PostRecord(
            f"bankpage-{day}-{slot}",
            day.isoformat(),
            slot,
            STATUS_PUBLISHED,
            content_id=entry_id,
            hashtags=tags or [],
        )
    )


# ----- similarity --------------------------------------------------------------------------
def test_normalise() -> None:
    assert normalise("  Hello,   WORLD! It’s fine. ") == "hello world it's fine"


def test_near_duplicate_detected() -> None:
    assert compare("Be kind to yourself today.", "Be kind to yourself, today!", 85)
    assert compare(
        "The calm sea teaches patience to all sailors.",
        "The calm sea teaches patience to all young sailors.",
        85,
    )


def test_short_phrase_not_contained_in_long_text() -> None:
    assert (
        compare(
            "Be kind.", "Be kind to everyone you meet, for every person is fighting something.", 85
        )
        is None
    )


def test_find_duplicates_reports_pairs() -> None:
    found = find_duplicates(
        [
            ("a", "Rest is part of the work."),
            ("b", "Rest is part of the work!"),
            ("c", "Something else entirely here."),
        ],
        85,
    )
    assert [(m.a, m.b) for m in found] == [("a", "b")]


# ----- validation ----------------------------------------------------------------------------
def test_quote_validation(entries: list[dict[str, Any]]) -> None:
    assert check_quote(entries[0], 180) == []
    bad = dict(entries[0], status="draft", text="x" * 200, tags=[])
    problems = check_quote(bad, 180)
    assert any("verified" in p for p in problems)
    assert any("longer" in p for p in problems)
    assert any("tags" in p for p in problems)


def test_attributed_quote_needs_source(entries: list[dict[str, Any]]) -> None:
    assert any("source" in p for p in check_quote(dict(entries[0], author="Seneca"), 180))


def test_quote_rejects_odd_characters(entries: list[dict[str, Any]]) -> None:
    assert check_quote(dict(entries[0], text="Click <here> now"), 180)


def test_fact_needs_two_independent_sources() -> None:
    fact = {
        "id": "s-00000001",
        "text": "Water boils at 100 °C at sea level.",
        "tags": ["physics"],
        "status": "verified",
        "checked_on": "2026-10-01",
        "verification_note": "standard pressure",
        "sources": [{"title": "A", "url": "https://www.nist.gov/a"}],
    }
    assert any("two" in p for p in check_fact(fact, 200))
    fact["sources"].append(
        {"title": "B", "url": "https://nist.gov/b"}
    )  # same host: not independent
    assert any("two" in p for p in check_fact(fact, 200))
    fact["sources"][1] = {"title": "B", "url": "https://www.esa.int/b"}
    assert check_fact(fact, 200) == []


def test_bank_report(entries: list[dict[str, Any]]) -> None:
    bank = {"type": "quotes", "entries": [*entries, dict(entries[0], id="q-00000009")]}
    report = validate_bank(bank, 180, 85)
    assert not report.ok and report.duplicates


def test_length_class() -> None:
    assert length_class("a" * 50) == "short"
    assert length_class("a" * 100) == "medium"
    assert length_class("a" * 150) == "long"


def test_archived_bank_is_never_postable() -> None:
    from autopilot.config import ROOT
    from autopilot.content.validate import load_bank, postable_entries

    bank = load_bank(ROOT / "pages" / "simplestorymode" / "archive" / "content.json")
    assert len(bank["entries"]) >= 400
    assert {e["status"] for e in bank["entries"]} == {"archived"}
    assert postable_entries(bank, 180) == []
    # Structurally still a clean bank (used as test data for the quote-bank engine).
    restored = dict(bank, entries=[dict(e, status="verified") for e in bank["entries"]])
    report = validate_bank(restored, 180, 85)
    assert report.ok
    attributed = sum(1 for e in bank["entries"] if e["author"])
    assert 0.1 <= attributed / report.total <= 0.3


# ----- selection -----------------------------------------------------------------------------
def test_no_repeat_until_bank_used(entries: list[dict[str, Any]]) -> None:
    state = PageState("bankpage")
    seen = set()
    for i in range(len(entries)):
        day = date(2026, 10, 1 + i)
        sel = select_entry(entries, state, day, f"seed{i}", 0, 85, 200)
        assert sel.entry["id"] not in seen and not sel.recycled
        seen.add(sel.entry["id"])
        _posted(state, sel.entry["id"], day)
    sel = select_entry(entries, state, date(2026, 11, 1), "x", 0, 85, 200)
    assert sel.recycled
    assert sel.entry["id"] == "q-00000001" or sel.entry["id"] in seen


def test_recycles_oldest_first(entries: list[dict[str, Any]]) -> None:
    state = PageState("bankpage")
    for i, e in enumerate(entries):
        _posted(state, e["id"], date(2026, 10, 1 + i))
    sel = select_entry(entries, state, date(2026, 12, 1), "x", 0, 85, 1)
    assert sel.entry["id"] == entries[0]["id"]


def test_facts_recycle_only_after_min_days(entries: list[dict[str, Any]]) -> None:
    state = PageState("factpage")
    for e in entries:
        _posted(state, e["id"], date(2026, 10, 1), slot=e["id"])
    with pytest.raises(ContentError):
        select_entry(entries, state, date(2026, 11, 1), "x", 120, 85, 1)
    assert select_entry(entries, state, date(2027, 2, 1), "x", 120, 85, 1).recycled


def test_same_primary_tag_not_twice_a_day(entries: list[dict[str, Any]]) -> None:
    twins = [
        dict(entries[0], id="q-0000000a", text="Rest well tonight and wake gently."),
        entries[1],
    ]
    state = PageState("bankpage")
    _posted(state, "q-00000001", DAY)
    pool = [entries[0], *twins]
    for i in range(10):
        sel = select_entry(pool, state, DAY, f"s{i}", 0, 85, 200)
        assert sel.entry["id"] == entries[1]["id"]


def test_near_duplicate_of_recent_post_skipped(entries: list[dict[str, Any]]) -> None:
    custom = PostRecord(
        "bankpage-2026-10-19-ondemand",
        "2026-10-19",
        "ondemand",
        STATUS_PUBLISHED,
        content_id="custom-00000000",
        text="Rest is part of the work!",
    )
    state = PageState("bankpage", [custom])
    for i in range(10):
        assert select_entry(entries, state, DAY, f"s{i}", 0, 85, 200).entry["id"] != "q-00000001"


def test_topic_matching(entries: list[dict[str, Any]]) -> None:
    synonyms = {"anxiety": ["peace", "courage"], "hard times": ["resilience"]}
    known = {t for e in entries for t in e["tags"]}
    assert topic_tags("anxiety", synonyms, known) == {"peace", "courage"}
    assert topic_tags("Hard times", synonyms, known) == {"resilience"}
    assert topic_tags("kindness", synonyms, known) == {"kindness"}
    assert topic_tags("bananas", synonyms, known) == set()
    sel = select_entry(entries, PageState("w"), DAY, "s", 0, 85, 200, {"resilience"})
    assert sel.entry["id"] == "q-00000003"


def test_template_differs_from_previous() -> None:
    state = PageState("bankpage")
    state.upsert(
        PostRecord("k", "2026-10-19", "m", STATUS_PUBLISHED, content_id="a", template="sage")
    )
    for i in range(20):
        assert choose_template(["sage", "plum", "sand"], state, "b", f"s{i}") != "sage"
        assert choose_template(["sage", "plum", "sand"], state, "a", f"s{i}") != "sage"


def test_templates_rotate_evenly() -> None:
    names = ["a", "b", "c", "d", "e", "f", "g", "h"]
    state = PageState("bankpage")
    for i in range(80):
        t = choose_template(names, state, f"id{i}", f"s{i}")
        state.upsert(
            PostRecord(
                f"k{i}", "2026-10-01", "m", STATUS_PUBLISHED, content_id=f"id{i}", template=t
            )
        )
    counts = [sum(1 for p in state.posts if p.template == n) for n in names]
    assert min(counts) >= 8 and max(counts) <= 12


def test_audio_mode_weights() -> None:
    weights = {"voice+music": 0.5, "music": 0.3, "voice": 0.2}
    picks = [choose_audio_mode(weights, f"k{i}") for i in range(2000)]
    assert 0.42 < picks.count("voice+music") / 2000 < 0.58
    assert choose_audio_mode(weights, "same") == choose_audio_mode(weights, "same")


# ----- captions ------------------------------------------------------------------------------
def test_caption_contents() -> None:
    kit = load_caption_kit(ARCHIVE / "captions-quotes.json")
    entry = {
        "id": "w-1",
        "text": "A quiet mind -- clear.",
        "author": "Epictetus",
        "tags": ["peace"],
    }
    cap = build_caption(kit, entry, "seed", 5, True, "Voiceover: AI-generated", frozenset())
    assert "“A quiet mind—clear.” — Epictetus" in cap.text
    assert "Voiceover: AI-generated" in cap.text
    assert 1 <= len(cap.hashtags) <= 5 and len(set(cap.hashtags)) == len(cap.hashtags)
    plain = build_caption(kit, entry, "seed", 5, False, "Voiceover: AI-generated", frozenset())
    assert "Voiceover" not in plain.text


def test_hashtags_never_repeat_previous_set() -> None:
    kit = load_caption_kit(ARCHIVE / "captions-quotes.json")
    entry = {"tags": ["peace"], "author": None}
    first = pick_hashtags(kit, entry, random.Random(1), 5, frozenset())
    for i in range(50):
        assert frozenset(
            pick_hashtags(kit, entry, random.Random(i), 5, frozenset(first))
        ) != frozenset(first)


def test_banned_hashtag_rejected(tmp_path: Any) -> None:
    path = tmp_path / "captions.json"
    path.write_text(
        '{"openers":["a"],"calls_to_action":["b"],"hashtag_pools":{"core":["#like4like"]},'
        '"banned_hashtags":["#like4like"]}',
        encoding="utf-8",
    )
    with pytest.raises(ContentError):
        load_caption_kit(path)


# ----- sanitising ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  hello\u200b   world  ", "hello world"),
        ("<script>alert(1)</script>", "scriptalert(1)script"),
        ("ｆｕｌｌwidth", "fullwidth"),
        ("line\nbreak\ttab", "line break tab"),
        ("visit https://evil.example #tag @user", "visit https:evil.example tag user"),
        ("\u202eRTL override", "RTL override"),
    ],
)
def test_sanitise(raw: str, expected: str) -> None:
    assert sanitise_text(raw, 300) == expected


def test_sanitise_truncates_at_word() -> None:
    assert sanitise_text("one two three four", 9) == "one two"
