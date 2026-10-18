"""Runtime-branch git writes, media hosting, alert issues, status page and token renewal pieces."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from autopilot.config import Config
from autopilot.errors import ValidationError
from autopilot.manifest import media_url_ok
from autopilot.ops import alerts
from autopilot.ops.alerts import AlertPlan, assert_public_safe
from autopilot.ops.gitstore import (
    GitError,
    commit_and_push,
    open_worktree,
    read_branch_file,
    squash_branch,
)
from autopilot.ops.issues import sync
from autopilot.ops.status import days_of_stock, render_status
from autopilot.ops.token import (
    PublicKey,
    Refreshed,
    RenewalError,
    pat_expiry,
    refresh_token,
    seal,
    store_secret,
)
from autopilot.publish.hosting import (
    DeferredHosting,
    HostingError,
    Probe,
    media_url,
    push_media,
    verify_url,
)
from autopilot.state import PageState, PostRecord
from gh_helpers import (
    FakeGitHubAPI,
    branch_file,
    clone_of,
    commit_count,
    make_origin,
    run_git,
)

REPO = "lumo-spec/instagram-autopilot"


# ----- git runtime branches -------------------------------------------------------------------
def test_new_branch_is_created_as_orphan_and_read_back(tmp_path: Path) -> None:
    _url, clone = make_origin(tmp_path)
    assert read_branch_file(clone, "state", "x.txt") is None
    tree = open_worktree(clone, "state", tmp_path / "wt")
    sha = commit_and_push(tree, "state", "add", lambda r: (r / "x.txt").write_text("1\n"))
    assert len(sha) == 40
    assert read_branch_file(clone, "state", "x.txt") == "1\n"
    assert read_branch_file(clone, "state", "README.md") is None  # orphan: no main files
    assert commit_and_push(tree, "state", "same", lambda r: (r / "x.txt").write_text("1\n")) == ""


def test_concurrent_writers_both_land_without_force(tmp_path: Path) -> None:
    """Two runs write state at once: the loser re-applies its change on the new tip."""
    url, clone = make_origin(tmp_path)
    other = clone_of(url, tmp_path / "other")
    a = open_worktree(clone, "state", tmp_path / "wa")
    commit_and_push(a, "state", "seed", lambda r: (r / "seed.txt").write_text("s"))
    b = open_worktree(other, "state", tmp_path / "wb")  # b starts from the seeded tip

    def add(name: str):  # type: ignore[no-untyped-def]
        def apply(root: Path) -> None:
            path = root / "log.txt"
            lines = path.read_text().splitlines() if path.exists() else []
            path.write_text("\n".join(sorted({*lines, name})) + "\n")

        return apply

    commit_and_push(a, "state", "a", add("page-a"))
    sleeps: list[float] = []
    commit_and_push(b, "state", "b", add("page-b"), sleep=sleeps.append)
    assert sleeps, "the second writer should have hit a conflict and retried"
    assert branch_file(url, tmp_path, "state", "log.txt") == "page-a\npage-b\n"


def test_push_gives_up_after_retries(tmp_path: Path) -> None:
    _url, clone = make_origin(tmp_path)
    tree = open_worktree(clone, "state", tmp_path / "wt")
    run_git(clone, "remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(GitError):
        commit_and_push(tree, "state", "x", lambda r: (r / "f").write_text("1"), 2, lambda _s: None)


def test_squash_keeps_live_files(tmp_path: Path) -> None:
    url, clone = make_origin(tmp_path)
    tree = open_worktree(clone, "media", tmp_path / "wt")
    for i in range(3):
        commit_and_push(tree, "media", f"c{i}", lambda r, i=i: (r / f"f{i}.jpg").write_text("x"))
    assert commit_count(url, tmp_path, "media") == 3
    squash_branch(tree, "media", "squashed")
    assert commit_count(url, tmp_path, "media") == 1
    assert branch_file(url, tmp_path, "media", "f2.jpg") == "x"


# ----- hosting --------------------------------------------------------------------------------
def test_media_urls_and_validation() -> None:
    sha = "a" * 40
    jpg = media_url(REPO, sha, "simplestorymode", "k-01.jpg")
    mp4 = media_url(REPO, sha, "simplestorymode", "k.mp4")
    assert jpg == f"https://raw.githubusercontent.com/{REPO}/{sha}/simplestorymode/k-01.jpg"
    assert mp4 == "https://lumo-spec.github.io/instagram-autopilot/simplestorymode/k.mp4"
    assert media_url_ok(jpg, REPO, "simplestorymode", "k-01.jpg")
    assert media_url_ok(mp4, REPO, "simplestorymode", "k.mp4")
    assert not media_url_ok(jpg, REPO, "otherpage", "k-01.jpg")
    assert not media_url_ok(jpg.replace(sha, "b" * 39), REPO, "simplestorymode", "k-01.jpg")
    assert not media_url_ok(jpg + "x", REPO, "simplestorymode", "k-01.jpg")


def test_push_media_puts_files_in_page_folder(tmp_path: Path) -> None:
    url, clone = make_origin(tmp_path)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg-bytes")
    tree = open_worktree(clone, "media", tmp_path / "wt")
    sha = push_media(tree, "simplestorymode", [("k-01.jpg", img)], "host k")
    assert sha == push_media(tree, "simplestorymode", [("k-01.jpg", img)], "again")  # retry
    assert branch_file(url, tmp_path, "media", "simplestorymode/k-01.jpg") == "jpeg-bytes"
    assert branch_file(url, tmp_path, "media", ".nojekyll") == ""


def test_verify_url_waits_then_passes_or_fails(tmp_path: Path) -> None:
    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"12345")
    answers = [Probe(404, "text/plain", 9, 0), Probe(200, "image/jpeg", 5, 206)]
    clock = iter(range(0, 1000, 10))
    verify_url("u", jpg, 100, lambda _u: answers.pop(0), lambda _s: None, lambda: next(clock))
    with pytest.raises(HostingError, match="application/octet-stream"):
        verify_url(
            "u",
            jpg,
            20,
            lambda _u: Probe(200, "application/octet-stream", 5, 206),
            lambda _s: None,
            lambda: next(clock),
        )
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"123")
    with pytest.raises(HostingError, match="range"):
        verify_url(
            "u", mp4, 0, lambda _u: Probe(200, "video/mp4", 3, 200), lambda _s: None, lambda: 0
        )


def test_deferred_hosting_collects_files(tmp_path: Path) -> None:
    src = tmp_path / "x.jpg"
    src.write_bytes(b"x")
    h = DeferredHosting(tmp_path / "media")
    assert h.host("simplestorymode", "k.jpg", src) == "pending://simplestorymode/k.jpg"
    assert (tmp_path / "media" / "k.jpg").exists()
    h.remove("simplestorymode", "k.jpg")
    assert not (tmp_path / "media" / "k.jpg").exists()


# ----- alerts as issues -------------------------------------------------------------------------
RUN = "https://github.com/lumo-spec/instagram-autopilot/actions/runs/18512345678"


def test_issue_opened_locked_deduplicated_and_closed() -> None:
    api = FakeGitHubAPI()
    gh = api.client()
    plan = AlertPlan([alerts.queue_low("Simple Story Mode", 2)])
    r = sync(gh, plan, "@lumo-spec", RUN, "2026-10-18")
    assert r.opened == ["queue-low-simple-story-mode"]
    issue = api.issues[0]
    assert issue["locked"] and "@lumo-spec" in issue["body"] and RUN in issue["body"]
    assert "autopilot-alert" in issue["labels"]
    # same day again: no duplicate, no comment
    r = sync(gh, plan, "@lumo-spec", RUN, "2026-10-18")
    assert not r.opened and not r.commented and len(api.issues) == 1
    # next day: one comment
    r = sync(gh, plan, "@lumo-spec", RUN, "2026-10-19")
    assert r.commented == ["queue-low-simple-story-mode"]
    # condition cleared
    r = sync(gh, AlertPlan(resolved=["queue-low-simple-story-mode"]), "@lumo-spec", RUN, "x")
    assert r.closed == ["queue-low-simple-story-mode"] and issue["state"] == "closed"


def test_scoped_alerts_close_when_not_raised_again() -> None:
    api = FakeGitHubAPI()
    gh = api.client()
    sync(gh, AlertPlan([alerts.approval_changed("Simple Story Mode", "c01-a")]), "@x", "", "d")
    scope = AlertPlan(scopes=["approval-changed-simple-story-mode-"])
    assert sync(gh, scope, "@x", "", "d").closed == ["approval-changed-simple-story-mode-c01-a"]


def test_alert_text_with_secrets_is_never_sent() -> None:
    with pytest.raises(ValueError, match="public-safety"):
        AlertPlan.from_dict({"raise": [{"key": "k", "title": "x", "body": "IG" + "A" * 30}]})
    assert_public_safe(f"see {RUN}")  # run links are allowed
    with pytest.raises(ValueError, match="public-safety"):
        assert_public_safe("user 17841400000000000")


def test_alert_plan_round_trip() -> None:
    plan = AlertPlan([alerts.missed_slots("Simple Story Mode", 2)], ["a"], ["b-"])
    back = AlertPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert back.raise_ == plan.raise_ and back.resolved == ["a"] and back.scopes == ["b-"]


# ----- status page -------------------------------------------------------------------------------
def test_status_page(cfg: Config) -> None:
    page = cfg.page("simplestorymode")
    state = PageState(page.name, token={"refreshed_on": "2026-10-10", "expires_on": "2026-12-09"})
    state.posts.append(
        PostRecord(
            "simplestorymode-2026-10-16-carousel",
            "2026-10-16",
            "carousel",
            "published",
            format="carousel",
            content_id="c01-you-can-miss",
            media_id="1789",
            permalink="https://www.instagram.com/p/abc/",
        )
    )
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    text = render_status(cfg, {page.name: state}, now, schedule_on=False)
    assert "Automatic posting:** OFF" in text and "c01-you-can-miss" in text
    assert "52 days left" in text and "1789" not in text
    on = render_status(cfg, {page.name: state}, now, schedule_on=True, renewal_key_expires="x")
    assert "Next slots" in on
    assert days_of_stock(page, {"carousel": 3, "image": 4, "reel": 0}) == 7.0


# ----- token renewal pieces -------------------------------------------------------------------
def keypair() -> tuple[object, PublicKey]:
    from nacl import public

    priv = public.PrivateKey.generate()
    return priv, PublicKey("3380204578043523366", base64.b64encode(bytes(priv.public_key)).decode())


def test_seal_only_github_can_decrypt() -> None:
    from nacl import public

    priv, pub = keypair()
    ct = seal(pub, "IGQVJnewtoken")
    assert "IGQVJ" not in ct
    assert public.SealedBox(priv).decrypt(base64.b64decode(ct)) == b"IGQVJnewtoken"  # type: ignore[arg-type]


def test_refresh_token_success_and_failure() -> None:
    seen: list[str] = []

    def ok(url: str) -> tuple[int, dict[str, object]]:
        seen.append(url)
        return 200, {"access_token": "IGQVJ" + "n" * 30, "expires_in": 5184000}

    r = refresh_token("graph.instagram.com", "IGQVJold", date(2026, 10, 18), ok)
    assert r.expires_on == "2026-12-17" and "redacted" in repr(r) and "IGQVJ" not in repr(r)
    assert "grant_type=ig_refresh_token" in seen[0]
    with pytest.raises(RenewalError, match="HTTP 400 \\(error code 190\\)"):
        refresh_token("h", "t", date(2026, 10, 18), lambda _u: (400, {"error": {"code": 190}}))
    with pytest.raises(RenewalError):
        refresh_token("h", "t", date(2026, 10, 18), lambda _u: (200, {"access_token": "x y"}))
    assert isinstance(r, Refreshed)


def test_store_secret_and_pat_expiry() -> None:
    api = FakeGitHubAPI()
    gh = api.client()
    store_secret(gh, "page-simplestorymode", "IG_ACCESS_TOKEN", "123", "A" * 80)
    assert api.secrets[("page-simplestorymode", "IG_ACCESS_TOKEN")] == {
        "encrypted_value": "A" * 80,
        "key_id": "123",
    }
    with pytest.raises(RenewalError):
        store_secret(gh, "page-simplestorymode", "IG_ACCESS_TOKEN", "123", "not base64!")
    assert pat_expiry(gh) == "2027-10-01"
    with pytest.raises(RenewalError):
        PublicKey("1", "short")
    with pytest.raises(ValidationError):
        from autopilot.jobs.common import parse_state

        parse_state('{"page": "other"}', "simplestorymode")
