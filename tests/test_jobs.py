"""The GitHub Actions job chain end to end, against a throwaway git origin and fake services:
plan -> build -> host -> publish (prepare, finish) -> record, plus renewal, health and cleanup."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from autopilot.config import Config, Page
from autopilot.errors import ValidationError
from autopilot.jobs import maintenance
from autopilot.jobs.build import check_request, on_demand_key, run_build
from autopilot.jobs.common import RunContext
from autopilot.jobs.host import run_host
from autopilot.jobs.plan import run_plan
from autopilot.jobs.publish import run_finish, run_prepare
from autopilot.jobs.record import run_record
from autopilot.rehearsal.fakes import FakeInstagram, SimulatedCrashError
from autopilot.rehearsal.preview import build_and_approve
from gh_helpers import FakeGitHubAPI, branch_file, make_origin, run_git
from story_helpers import post_data, write_post

REPO = "lumo-spec/instagram-autopilot"
NOW = datetime(2026, 10, 19, 18, 30, tzinfo=UTC)  # Monday 19:30 UK time: carousel slot due
KEY = "simplestorymode-2026-10-19-carousel"


def ctx(tmp: Path) -> RunContext:
    return RunContext(REPO, f"https://github.com/{REPO}/actions/runs/123456789012", None, None)


def approved_queue(cfg: Config, page: Page) -> None:
    write_post(page, post_data("c01-first", n=3), art="quiet")
    write_post(page, post_data("c02-second", n=2), art="quiet")
    write_post(page, post_data("i01-single", fmt="image", n=1), art="quiet")
    write_post(page, post_data("c03-draft", n=2))  # no art, never approved
    for pid in ("c01-first", "c02-second", "i01-single"):
        build_and_approve(cfg, page, pid)


def no_verify(_url: str, _path: Path, _timeout: float) -> None:
    return None


def probe_ok(url: str) -> tuple[int, str]:
    return 200, "video/mp4" if url.endswith(".mp4") else "image/jpeg"


def run_chain(
    cfg: Config,
    repo_dir: Path,
    work: Path,
    fake: FakeInstagram,
    api: FakeGitHubAPI,
    key: str = KEY,
    fmt: str = "carousel",
    post_id: str = "",
    crash: bool = False,
) -> dict:
    run_build(cfg, repo_dir, work, "simplestorymode", key, fmt, post_id, NOW)
    run_host(cfg, repo_dir, work, REPO, verify=no_verify)
    run_prepare(cfg, work, REPO, client=fake, probe=probe_ok)
    if crash:
        fake.fail_next.add("crash_after_publish")
        with pytest.raises(SimulatedCrashError):
            run_finish(cfg, work, REPO, client=fake)
    else:
        run_finish(cfg, work, REPO, client=fake)
    run_record(cfg, repo_dir, work, ctx(work), api.client(), NOW)
    state = branch_file(
        origin_url(repo_dir), work.parent, "state", "pages/simplestorymode/state.json"
    )
    return json.loads(state or "{}")


def origin_url(repo_dir: Path) -> str:
    return run_git(repo_dir, "remote", "get-url", "origin").strip()


@pytest.fixture
def setup(story: tuple[Config, Page], tmp_path: Path) -> tuple[Config, Page, str, Path]:
    cfg, page = story
    approved_queue(cfg, page)
    url, clone = make_origin(tmp_path)
    return cfg, page, url, clone


def test_scheduled_carousel_end_to_end(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, _page, url, clone = setup
    fake, api = FakeInstagram(), FakeGitHubAPI()
    state = run_chain(cfg, clone, tmp_path / "w1", fake, api)
    rec = state["posts"][0]
    assert rec["status"] == "published" and rec["content_id"] == "c01-first" and rec["slides"] == 3
    assert fake.published_slots() == [KEY]
    creates = [c for c in fake.calls if c["op"] == "create-item"]
    assert [c["url"].rsplit("/", 1)[1] for c in creates] == [f"{KEY}-0{i}.jpg" for i in (1, 2, 3)]
    assert all("raw.githubusercontent.com" in c["url"] for c in creates)
    # hosted files were removed again; the status page exists; nothing needed an alert
    assert branch_file(url, tmp_path, "media", f"simplestorymode/{KEY}-01.jpg") is None
    status = branch_file(url, tmp_path, "state", "STATUS.md") or ""
    assert "Last post:** 2026-10-19 · carousel · c01-first" in status
    assert "Automatic posting:** OFF" in status
    titles = [i["title"] for i in api.issues]
    assert titles == [
        "ACTION NEEDED [Simple Story Mode]: only 2 approved posts left — "
        "open Claude Code and type: New post: <topic>"
    ]
    # the next carousel slot takes the next approved post, never the same one again
    state = run_chain(cfg, clone, tmp_path / "w2", fake, api, key=KEY.replace("19", "21"))
    assert [p["content_id"] for p in state["posts"]] == ["c01-first", "c02-second"]


def test_crash_after_publish_is_reconciled_not_reposted(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, _page, _url, clone = setup
    fake, api = FakeInstagram(), FakeGitHubAPI()
    state = run_chain(cfg, clone, tmp_path / "w1", fake, api, crash=True)
    assert state["posts"][0]["status"] == "intent" and state["posts"][0]["container_id"]
    assert any("could not be published" in i["title"] for i in api.issues)
    state = run_chain(cfg, clone, tmp_path / "w2", fake, api)  # the next run, same slot
    assert state["posts"][0]["status"] == "published"
    assert fake.published_slots() == [KEY]  # exactly once
    assert all(i["state"] == "closed" for i in api.issues if "could not be published" in i["title"])


def test_lost_record_step_does_not_double_post(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    """Published, but the record job never ran: the retry finds the post by its caption."""
    cfg, _page, _url, clone = setup
    fake = FakeInstagram()
    work = tmp_path / "w1"
    run_build(cfg, clone, work, "simplestorymode", KEY, "carousel", "", NOW)
    run_host(cfg, clone, work, REPO, verify=no_verify)
    run_prepare(cfg, work, REPO, client=fake, probe=probe_ok)
    run_finish(cfg, work, REPO, client=fake)  # ... and then the record step is lost
    state = run_chain(cfg, clone, tmp_path / "w2", fake, FakeGitHubAPI())
    assert state["posts"][0]["status"] == "published" and fake.published_slots() == [KEY]


def test_quota_and_unreachable_media_publish_nothing(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, _page, _url, clone = setup
    fake = FakeInstagram(usage=45)
    state = run_chain(cfg, clone, tmp_path / "w1", fake, FakeGitHubAPI())
    assert state["posts"][0]["status"] == "failed" and not fake.published_slots()
    work = tmp_path / "w2"
    run_build(cfg, clone, work, "simplestorymode", KEY, "carousel", "", NOW)
    run_host(cfg, clone, work, REPO, verify=no_verify)
    out = run_prepare(cfg, work, REPO, client=FakeInstagram(), probe=lambda _u: (404, ""))
    assert out["status"] == "failed" and out["error"] == "hosted media not reachable"


def test_post_now_publishes_a_chosen_approved_post(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, page, _url, clone = setup
    fake, api = FakeInstagram(), FakeGitHubAPI()
    key = on_demand_key(cfg, page, NOW)
    assert key == "simplestorymode-2026-10-19-now193000"
    state = run_chain(
        cfg, clone, tmp_path / "w", fake, api, key=key, fmt="auto", post_id="i01-single"
    )
    assert (
        state["posts"][0]["content_id"] == "i01-single" and state["posts"][0]["format"] == "image"
    )
    image = next(c for c in fake.calls if c["op"] == "create")
    assert image["format"] == "image" and image["ai_generated"]


def test_unapproved_post_and_empty_queue_are_skipped_with_alert(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, page, _url, clone = setup
    fake, api = FakeInstagram(), FakeGitHubAPI()
    key = on_demand_key(cfg, page, NOW)
    state = run_chain(
        cfg, clone, tmp_path / "w", fake, api, key=key, fmt="auto", post_id="c03-draft"
    )
    assert state["posts"][0]["status"] == "skipped" and not fake.published_slots()
    assert any("no approved post" in i["title"] for i in api.issues)


def test_changed_post_is_held_back_and_alerted(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, page, _url, clone = setup
    post = page.dir / "queue" / "c01-first" / "post.yaml"
    post.write_text(
        post.read_text(encoding="utf-8").replace("let go", "let it go"), encoding="utf-8"
    )
    fake, api = FakeInstagram(), FakeGitHubAPI()
    state = run_chain(cfg, clone, tmp_path / "w", fake, api)
    assert state["posts"][0]["content_id"] == "c02-second"  # c01 changed after approval
    assert any("c01-first changed after approval" in i["title"] for i in api.issues)


def test_build_rejects_bad_inputs(story: tuple[Config, Page]) -> None:
    cfg, _ = story
    with pytest.raises(ValidationError):
        check_request(cfg, "simplestorymode", KEY, "story", "")
    with pytest.raises(ValidationError):
        check_request(cfg, "simplestorymode", KEY, "auto", "c01; rm -rf /")
    with pytest.raises(ValidationError):
        check_request(cfg, "simplestorymode", "otherpage-2026-10-19-x", "auto", "")
    page, kind, day = check_request(cfg, "simplestorymode", KEY, "auto", "")
    assert kind == "any" and day == date(2026, 10, 19) and page.name == "simplestorymode"


def test_publish_requires_hosted_manifest_and_secrets(
    setup: tuple[Config, Page, str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _page, _url, clone = setup
    work = tmp_path / "w"
    work.mkdir()
    assert run_prepare(cfg, work, REPO, client=FakeInstagram())["status"] == "not_run"
    run_build(cfg, clone, work, "simplestorymode", KEY, "carousel", "", NOW)
    run_host(cfg, clone, work, REPO, verify=no_verify)
    hosted = json.loads((work / "hosted.json").read_text(encoding="utf-8"))
    hosted["manifest"]["media_urls"][0] = "https://evil.example/x.jpg"
    (work / "hosted.json").write_text(json.dumps(hosted), encoding="utf-8")
    with pytest.raises(ValidationError):
        run_prepare(cfg, work, REPO, client=FakeInstagram(), probe=probe_ok)
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    from autopilot.jobs.publish import client_from_env

    with pytest.raises(ValidationError, match="secrets are not set"):
        client_from_env(cfg)


def test_plan_job_outputs_matrix(setup: tuple[Config, Page, str, Path], tmp_path: Path) -> None:
    cfg, _page, _url, clone = setup
    out = tmp_path / "out.txt"
    due = run_plan(cfg, clone, RunContext(REPO, "", None, out), NOW)
    assert [d["slot_key"] for d in due] == [KEY]
    text = out.read_text(encoding="utf-8")
    assert "any=true" in text and KEY in text


# ----- maintenance ---------------------------------------------------------------------------
def keys_for(api: FakeGitHubAPI):  # type: ignore[no-untyped-def]
    from nacl import public

    priv = public.PrivateKey.generate()
    api.keys["page-simplestorymode"] = (
        "568250167242549743",
        base64.b64encode(bytes(priv.public_key)).decode(),
    )
    return priv


def test_token_renewal_sealed_handoff(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    from nacl import public

    cfg, _page, url, clone = setup
    api = FakeGitHubAPI()
    priv = keys_for(api)
    work = tmp_path / "w"
    keys = maintenance.run_renew_keys(cfg, api.client(), work)
    assert keys["pat_expires_on"] == "2027-10-01" and "IG" not in json.dumps(keys)
    masked: list[str] = []
    new = "IGQVJ" + "z" * 40
    sealed = maintenance.run_renew_refresh(
        cfg,
        "simplestorymode",
        clone,
        work,
        "IGQVJold",
        date(2026, 10, 18),
        fetch=lambda _u: (200, {"access_token": new, "expires_in": 5184000}),
        mask=masked.append,
    )
    assert masked == [new] and new not in json.dumps(sealed)  # only ciphertext leaves the job
    result = maintenance.run_renew_store(cfg, api.client(), work)
    assert result["pages"]["simplestorymode"]["status"] == "stored"
    stored = api.secrets[("page-simplestorymode", "IG_ACCESS_TOKEN")]
    plain = public.SealedBox(priv).decrypt(base64.b64decode(stored["encrypted_value"]))
    assert plain.decode() == new
    maintenance.run_record_maintenance(cfg, clone, work, ctx(work), api.client(), NOW)
    state = json.loads(
        branch_file(url, tmp_path, "state", "pages/simplestorymode/state.json") or "{}"
    )
    assert state["token"] == {"refreshed_on": "2026-10-18", "expires_on": "2026-12-17"}
    renewal = json.loads(branch_file(url, tmp_path, "state", "status/renewal.json") or "{}")
    assert renewal["pat_expires_on"] == "2027-10-01"
    # a second refresh on the same day is skipped (Meta: token must be 24 h old)
    again = maintenance.run_renew_refresh(
        cfg,
        "simplestorymode",
        clone,
        tmp_path / "w2",
        "x",
        date(2026, 10, 18),
        fetch=lambda _u: pytest.fail("must not refresh twice a day"),  # type: ignore[arg-type,return-value]
    )
    assert again["status"] == "skipped"


def test_renewal_failure_keeps_old_token_and_alerts(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, _page, _url, clone = setup
    api = FakeGitHubAPI()
    keys_for(api)
    work = tmp_path / "w"
    maintenance.run_renew_keys(cfg, api.client(), work)
    maintenance.run_renew_refresh(
        cfg,
        "simplestorymode",
        clone,
        work,
        "IGQVJold",
        date(2026, 10, 18),
        fetch=lambda _u: (400, {"error": {"code": 190}}),
        mask=lambda _v: None,
    )
    maintenance.run_renew_store(cfg, api.client(), work)
    assert not api.secrets  # nothing overwritten
    plan = maintenance.run_record_maintenance(cfg, clone, work, ctx(work), api.client(), NOW)
    assert any("token renewal failed" in a.title for a in plan.raise_)


def test_token_set_health_and_expiry_alerts(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, _page, url, clone = setup
    api = FakeGitHubAPI()
    work = tmp_path / "w"
    work.mkdir()
    fake = FakeInstagram()
    fake.fail_next.add("bad_token")
    health = maintenance.run_health(cfg, "simplestorymode", work, fake)
    assert health["token_ok"] is False and health["quota_ok"] is True
    plan = maintenance.run_record_maintenance(
        cfg, clone, work, ctx(work), api.client(), NOW, token_set="simplestorymode"
    )
    state = json.loads(
        branch_file(url, tmp_path, "state", "pages/simplestorymode/state.json") or "{}"
    )
    assert state["token"]["expires_on"] == "2026-12-18"
    assert any("token not working" in a.title for a in plan.raise_)
    later = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
    (work / "health-simplestorymode.json").unlink()
    plan = maintenance.run_record_maintenance(cfg, clone, work, ctx(work), api.client(), later)
    assert any("token expires in 8 days" in a.title for a in plan.raise_)


def test_keepalive_and_test_alert(story: tuple[Config, Page], tmp_path: Path) -> None:
    cfg, _ = story
    api = FakeGitHubAPI()
    maintenance.run_keepalive(api.client(), ["autopilot.yml", "maintenance.yml"])
    assert ("PUT", "/actions/workflows/autopilot.yml/enable", None) in api.calls
    with pytest.raises(ValidationError):
        maintenance.run_keepalive(api.client(), ["../x"])
    maintenance.run_test_alert(cfg, api.client(), ctx(tmp_path), False, "2026-10-18")
    assert api.issues[0]["title"].startswith("Test alert") and api.issues[0]["locked"]
    maintenance.run_test_alert(cfg, api.client(), ctx(tmp_path), True, "2026-10-18")
    assert api.issues[0]["state"] == "closed"


def test_media_cleanup_removes_old_files_and_squashes(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    from autopilot.ops.gitstore import commit_and_push, open_worktree

    cfg, _page, url, clone = setup
    tree = open_worktree(clone, "media", tmp_path / "m")
    for day in ("2026-10-01", "2026-10-18"):
        name = f"simplestorymode/simplestorymode-{day}-carousel-01.jpg"

        def add(root: Path, name: str = name) -> None:
            (root / name).parent.mkdir(exist_ok=True)
            (root / name).write_text("x")

        commit_and_push(tree, "media", f"host {day}", add)
    removed = maintenance.run_cleanup(cfg, clone, tmp_path / "w", NOW)
    assert removed == 1
    assert (
        branch_file(
            url, tmp_path, "media", "simplestorymode/simplestorymode-2026-10-18-carousel-01.jpg"
        )
        == "x"
    )
    assert (
        branch_file(
            url, tmp_path, "media", "simplestorymode/simplestorymode-2026-10-01-carousel-01.jpg"
        )
        is None
    )


def test_missed_slots_only_when_schedule_on(story: tuple[Config, Page]) -> None:
    from autopilot.plan import missed_slots
    from autopilot.state import PageState

    cfg, page = story
    missed = missed_slots(cfg, page, NOW, PageState(page.name), date(2026, 10, 1))
    assert "simplestorymode-2026-10-17-single" in missed  # Saturday 12:30 had no run
    assert all(k < "simplestorymode-2026-10-19-carousel" or "19" not in k for k in missed)


def test_publishing_switch_off_records_skip_without_alert(
    setup: tuple[Config, Page, str, Path], tmp_path: Path
) -> None:
    cfg, _page, url, clone = setup
    work = tmp_path / "w"
    api = FakeGitHubAPI()
    run_build(cfg, clone, work, "simplestorymode", KEY, "carousel", "", NOW)
    run_host(cfg, clone, work, REPO, verify=no_verify)
    out = run_record(cfg, clone, work, ctx(work), api.client(), NOW, publish_job="skipped")
    assert out is not None and out.record.status == "skipped"
    assert "switched off" in out.summary
    assert not any("could not be published" in i["title"] for i in api.issues)
    assert branch_file(url, tmp_path, "media", f"simplestorymode/{KEY}-01.jpg") is None


def test_jobs_cli_plan(
    setup: tuple[Config, Page, str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autopilot.jobs.__main__ import main

    _cfg, _page, _url, clone = setup
    out = tmp_path / "gh-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert main(["--work", str(tmp_path / "w"), "--repo-dir", str(clone), "plan"]) == 0
    assert "any=" in out.read_text(encoding="utf-8")
    monkeypatch.delenv("GH_SECRETS_PAT", raising=False)
    assert main(["--work", str(tmp_path / "w"), "renew-keys"]) == 1  # no key in this job
