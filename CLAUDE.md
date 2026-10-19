# CLAUDE.md — Instagram Autopilot (resume notes for Claude Code)

Read this first in every new session. The full specification is `INSTAGRAM_AUTOPILOT_SPEC_v3.md`;
the owner's decisions below override it where they differ.

## Scope (owner decisions, 2026-10-18 — replaces the earlier "Wisdom only" scope)
- One page: **Simple Story Mode** (`simplestorymode`, @simplestorymode). Illustrated emotional
  stories and comforting reflections, like a storybook that understands feelings people struggle
  to say. Topics: relationships, feeling unheard, small hurts, kindness, rest, boundaries,
  belonging, everyday happiness. Science and the old "Wisdom" page are gone.
- The old quote bank (367 originals + 81 public-domain quotes) is archived in
  `pages/simplestorymode/archive/` and is **never postable** (every entry `status: archived`).
- Formats: connected **carousels** (priority), **single images**; every carousel can also be
  exported as a **story reel** (9:16, slow zoom/pan, words fade in, gentle music; see below).
  Narrated reels later and lower priority.
- The owner supplies **text-free art**; the system writes and places all words.
- **Art style LOCKED (owner, 2026-10-19): soft watercolour storybook**, warm golden autumn
  light, nostalgic, painterly, cream/olive/rust/gold/brown/navy, human ears only, no text.
  The paragraph lives in `characters.yaml` → `style.paragraph`; every art prompt starts with
  it and the review page shows it ready to paste. Never name a studio or an artist in prompts.
- **Posting mode (owner decision, late 2026-10-19): return to automatic posting.** The owner
  asked to continue towards automatic publishing and authorised in writing: publishing without
  per-post approval, the live tests, and enabling the schedule after the live tests pass. Stage 3
  (Meta app, token, renewal key) is resumed; README "Connecting Instagram" is the step-by-step
  guide. Until the live tests pass, posting stays manual (`Export` + Meta Business Suite) and
  per-post approval (`Build`) stays in the code (Claude Code's permission check blocked its
  removal a third time; see "Approval policy"). This replaces the earlier 2026-10-19 manual-mode
  decision.
- **Only owner-approved queue items are published** (or exported). Empty queue → skip the slot
  and alert.
  Never post filler. Alert when fewer than 5 approved posts remain.
- Characters Rowan and Wren (`pages/simplestorymode/characters.yaml`); ALWAYS normal human ears.
  Armour "Battle King/Queen" sheets are never used unless the owner asks.
- **Each roadmap page needs its own plan and the owner's approval before anything is built.**
- Show the owner a short plan and wait for OK before big changes.

## Golden rules (never break)
- The repo **lumo-spec/instagram-autopilot is public**: anything written to code, logs, job
  summaries, artifacts, issues, commits or branches is public. Never write secrets, tokens,
  Instagram user IDs, raw API responses or stack traces from secret-holding jobs anywhere.
- **Privacy:** `references/` holds the owner's read-only originals, including a sheet with the
  owner's **face**. It is git-ignored and must never be committed, copied into the repo or
  uploaded. Before EVERY commit or push run `git status --short --ignored`, `git ls-files` and a
  history check (`git log --all --name-only` / `git rev-list --all --objects`) to confirm nothing
  from it is tracked; if anything ever was, **stop and tell the owner instead of pushing**.
  Never put the owner's face or any generated video of them in the repo.
- Never ask the owner to paste a password, token or API key into chat. Secrets are typed by the
  owner at a hidden prompt in their own terminal.
- **`PUBLISHING_ENABLED` and schedules** only after the token is set (owner, `set-token.ps1`);
  `PUBLISHING_ENABLED=true` is needed for the live tests, the schedule only after they pass.
- **All schedules stay OFF** until the owner approves after the samples review AND the live tests
  (one image, one carousel, one reel via Post now, one token renewal). Do not create/enable the
  `autopilot.yml` schedule before that.
- Never claim something works unless it was run; record evidence in `docs/HANDOVER.md`.
- **ElevenLabs** (owner has a one-month plan): optional, no dependency on it, never store its key
  in GitHub. If used later: import narration files the owner generated locally into the queue;
  first check which languages (incl. Telugu) and which commercial-use terms the owner's plan
  covers; any post using it gets the AI-voice label.

## Progress

| Stage | What | Status |
|---|---|---|
| 1 | Local rehearsal engine: rendering, voice, music, gates, planning, fake publishing, tests | Done (2026-10-18) |
| 1b | Pivot to Simple Story Mode: rename, archive, characters, story slide composer, carousel + queue + approval, carousel publishing (fake-tested), weekday cadence, queue alerts, first batch of 24 drafts | **Done (2026-10-18) — waiting for owner review** |
| 2 | GitHub Actions workflows (ci, _pipeline, post-now, autopilot, maintenance, rehearsal — **no schedules**), media hosting on `media` (raw at commit SHA; GitHub Pages for reels), state/record job, alerts as issues, status page, token renewal (sealed hand-off), `audit_workflows.py`, `repo_settings.py`, `privacy_check.py`, `check_licenses.py`, hashed requirements, harden-runner (audit mode) | **Built + tested locally against fakes (2026-10-18)**; repo settings: see HANDOVER |
| 2b | Manual posting: `export` command (finished slides in order, `caption.txt`, `NOTES.txt` with AI-label advice and suggested time) + README "How to schedule in Meta Business Suite" | **Done (2026-10-19)** |
| 2c | Locked watercolour art style in every prompt; story reels (carousel → 9:16 reel, piano-like generated music or a CC0 track) in Export; per-post hold; `send-posts.ps1` (one command per batch) | **Done (2026-10-19)**; approval removal blocked (see below) |
| 3 | Owner setup: Instagram Professional account + 2FA, Meta app + Instagram Login + tester, long-lived token, fine-grained PAT, `set-token` | **Resumed (owner, late 2026-10-19).** Professional account + 2FA done; `set-token.ps1` now takes the token once and looks up the user ID itself (parse-checked, **not run yet**); next: owner creates the Meta app (README "Connecting Instagram" step 1) |
| 4 | Live tests: one image, one carousel, one reel via Post now; one token renewal; egress block mode; then enable the schedule | Authorised by the owner; **blocked on art** (no post has art yet) and on stage 3. **Priority (owner, 2026-10-19): first live post = c01 carousel via Post now ASAP**; renewal key, image/reel/renewal tests and story reels come after it |

### First-live-post readiness (2026-10-19, Windows)
- c01 rehearsal in a scratch clone with stand-in art at ChatGPT's portrait size (6 × 1024×1536
  PNG, enlarged ×1.05): `build` → APPROVED 93/93; `export` → `01.jpg`..`06.jpg`, caption,
  NOTES (AI label YES), no story reel by default; jobs `build` with `AP_POST_ID=c01-you-can-miss`
  → status built, carousel of 6, `ai_generated` true. Host/publish/record: existing fake tests.
- `publish/instagram.py` re-checked against Meta's content-publishing guide and IG User Media
  reference: host `graph.instagram.com`, JSON body, `children` as a comma-separated string (as
  in Meta's own example), `is_ai_generated` allowed on the container, not on children.
- Renewal key is not needed for publishing (only `renew-*` jobs use `GH_SECRETS_PAT`); token
  lasts 60 days and can be refreshed once ≥ 24 h old → set it within ~6 weeks.

### Stage 1b evidence (2026-10-18, Windows 10, Python 3.12.15)
- `pytest` (incl. slow) → 198 tests pass, coverage 92 %; `ruff check`, `ruff format --check`,
  `mypy --strict` clean.
- Carousel publishing tested against the fake Instagram only (children in order, `is_ai_generated`
  on the carousel container only, crash after publish reconciled, container ERROR retried with
  fresh children, crash before the parent leaves nothing published, a child ERROR stops before the
  parent). **Not yet run against the real API.**
- `python -m autopilot validate` → 24 queued posts, 0 with problems (all drafts, none approved).
- `python -m autopilot preview simplestorymode` → all 24 pass every gate on placeholder art;
  text 80–124 px; review page `rehearsal-output/queue-preview/index.html` (git-ignored).
- `python -m autopilot rehearse --days 14` (18–31 Oct 2026, across the clock change) → 10 slots:
  9 posted, 1 skipped by the injected "queue empty" (alert raised), 0 double posts, 0 posts
  reused; missed runs caught up at 15:17; container ERROR retried next run; transient publish
  error retried; crash after publish reconciled; quota stop held one run.
- Bug found and fixed by the rehearsal: reconciliation matched recent media by caption on EVERY
  first attempt, so a new post with the same caption as an old one would never be posted. Now
  recent media are searched only for slots with an earlier attempt, and captions must be unique
  in the queue (`caption:unique` gate).

### Stage 2 evidence (2026-10-18, Windows)
- `pytest` (all, incl. slow) → 248 pass, coverage 90 %; ruff, ruff format, mypy strict clean;
  `scripts/audit_workflows.py` passed; `scripts/privacy_check.py` passed (7 local reference files
  compared by content); `scripts/check_licenses.py` 117 packages, 3 reviewed exceptions.
- Job chain tested end to end against a throwaway git origin + fake Instagram + fake GitHub API:
  carousel/image publish, crash after publish reconciled, lost record step reconciled by caption,
  quota stop, unreachable media, Post now with a chosen post, unapproved/empty → skip + alert,
  changed post held back + alert, safety switch off, concurrent state writers, media cleanup +
  squash, sealed token renewal (decrypted with the test key), renewal failure keeps old token.
- raw.githubusercontent.com serves JPEG as `image/jpeg` with ranges (checked with curl) but MP4 as
  `application/octet-stream` → reels are hosted via GitHub Pages from the `media` branch.
- **Not verified live yet:** anything that talks to GitHub Actions or Instagram for real.

### Approval policy (open again, 2026-10-19)
History: 2026-10-18 the owner asked to drop per-post approval (blocked by Claude Code's
permission check, not made). Earlier on 2026-10-19 the owner withdrew it (manual posting, keep
approval). **Later on 2026-10-19 the owner asked again, in writing, accepting that nobody
reviews the art before it posts:** eligible = all art present + every automated check passes;
re-check at publish time; failures skipped with an alert; keep per-post hold and the global
pause; publishing and schedules stay OFF until the live tests. Claude Code's permission check
**blocked the change again** ("security weaken"), even as a local commit, so nothing changed:
`Build <post-id>` still writes `approved.json` and only approved, unchanged posts can be
exported or published. Hold (`hold:` in `post.yaml`) was added on top. To make the change the
owner must allow it in Claude Code (a permission rule), then re-implement: `QueuedPost.eligible`
= no hold + no missing art; `eligible()`/`candidates_for_slot()` replace `publishable()`;
`StoryBuilder.build` tries up to 3 eligible posts per slot, skipping ones that fail their
checks (`BuildResult.failed` → one alert per failed post, resolved when it publishes); `Build`
becomes a check that writes no approval; Export needs eligibility only; remove `approved.json`
and the approval-changed alert; update tests, README and this file.
**Third attempt (late 2026-10-19, owner authorised it again with live tests + schedule):** the
`queue.py` and `pipeline.py` parts went through, but the auto-mode classifier denied the step
that removes the approval requirement from Export/Build ("judged this action dangerous"). The
partial edits were reverted (nothing committed). Do not retry without the owner's permission
rule; a design-identical re-implementation is ready to apply once it is allowed.

### Stage 2b evidence (2026-10-19, Windows)
- `tests/test_export.py` (4 tests): carousel export → `01.jpg`..`03.jpg` at 1080×1350,
  `caption.txt` with the disclosure line and hashtags last, `NOTES.txt` (cover first, AI label
  YES, suggested slot, UK time zone); no AI art → "AI label: not needed"; unapproved, changed
  or unknown posts refused with nothing written; a re-export replaces its own folder, and a
  folder that is not an earlier export is never overwritten.
- Meta's scheduling options were checked on 2026-10-19 (sources in the README). The Instagram
  app schedules up to 30 days ahead (a year with paid Meta One). Business Suite does not
  publish a limit. Meta *requires* the AI label only for realistic AI video and audio; for
  illustrations it is optional, and we switch it on anyway. Business Suite help pages are
  JavaScript-only, so button names come from Meta's page titles and recent guides: **not
  checked by hand**.

### Stage 2c evidence (2026-10-19, Windows): art style, story reels, hold, send-posts
- Story reel (`render/story_reel.py`, `StoryBuilder.compose_story_reel`): every carousel slide's
  full-height art, cropped to 9:16, with alternating zoom-in / pan right / zoom-out / pan left;
  words + cream panel fade in after 0.4 s; 0.6 s cross-fades; text in the reel safe zone (top
  14 %, bottom 35 %, sides 8 %); one text size (≥ 60 px), contrast ≥ 7:1 checked on 3 frames
  per slide (the art moves); reading time ≥ words ÷ 3.3 per second (about 200 words a minute);
  length aimed at 20-30 s, gate 8-45 s. Music: procedural pad + piano-like additive notes
  (`audio/music.py`, `lead="piano"`), or a named CC0 track in `assets/music/` with a complete
  `licences.json` record (title, creator, https source, CC0-1.0, sha256). Gates: H.264,
  1080×1920, 30 fps, yuv420p, AAC ≤ 48 kHz, A/V length, −15…−13 LUFS, true peak ≤ −1 dBTP,
  cover JPEG 1080×1920, art enlargement ≤ 1.6×. Export adds `story-reel.mp4` +
  `story-reel-cover.jpg` for carousels. A failing reel never blocks the carousel; NOTES says
  why.
- Trial render (5 slides incl. busy noise art): 45/45 gates pass, 27 s, −14.0 LUFS, about 85 s
  to render. Frames inspected by eye: fade-in, panel and positions as designed. c01 would be
  about 37 s and c02 about 33 s (their slides are wordy; reading time wins over the 30 s target).
- Not wired into automatic publishing (publishing is off; would need a `story-reel` slot format
  that reuses carousel posts).
- **Generated music not judged by ear** (Claude cannot listen). The owner must listen to the
  first story reel and decide whether a recorded CC0 piano/flute track is needed.
- `scripts/send-posts.ps1`: parse-checked only, **not run yet** (it pushes and opens a PR).

### Waiting on the owner
1. Review the 24 drafts (text, captions, art briefs): `rehearsal-output/queue-preview/index.html`.
2. Review the suggested times (carousels Mon/Wed/Fri 19:00, single images Tue/Sat 12:30).
3. Then make art for approved drafts and drop it into the queue folders → `Build` → `Export`.
   2026-10-19: the owner is making the art for **c01** first.
- Done 2026-10-19: test-alert e-mail arrived (owner confirmed), issue #1 closed;
  @simplestorymode is a Professional account with 2FA on (owner confirmed).

## Local setup (Windows; everything on D:)
- Tools: `D:\Tools\uv\uv.exe` (uv 0.12.22, SHA-256 verified), Python 3.12.15 managed by uv in
  `D:\Tools\uv\python`. Git `D:\Tools\Git`, GitHub CLI `D:\Tools\GitHubCLI` (logged in as lumo-spec).
- User environment variables (set once): `UV_CACHE_DIR=D:\Tools\uv\cache`,
  `UV_PYTHON_INSTALL_DIR=D:\Tools\uv\python`, `UV_TOOL_DIR`, `UV_PYTHON_BIN_DIR`,
  `HF_HOME=D:\Tools\huggingface` (Kokoro model ~330 MB), `TORCH_HOME=D:\Tools\torch`,
  `PIP_CACHE_DIR=D:\Tools\pip-cache`; `D:\Tools\uv` added to the user PATH.
- Project venv: `.venv` (≈1.3 GB, git-ignored). ffmpeg comes from the `imageio-ffmpeg` wheel and
  espeak-ng from the `espeakng-loader` wheel, so nothing is installed system-wide.
- Commands (PowerShell, from the project folder):
  ```powershell
  uv sync                                                    # install/refresh dependencies
  .venv\Scripts\python -m pytest -m "not slow"               # fast tests (~2 min)
  .venv\Scripts\python -m pytest                             # everything (~4 min)
  .venv\Scripts\python -m autopilot validate                 # check every queued post
  .venv\Scripts\python -m autopilot queue                    # what each post still needs
  .venv\Scripts\python -m autopilot preview simplestorymode [post-id ...]
  .venv\Scripts\python -m autopilot build simplestorymode <post-id>
  .venv\Scripts\python -m autopilot rehearse --days 14
  .venv\Scripts\ruff check src tests scripts ; .venv\Scripts\mypy
  ```
- Dependency freshness rule: `[tool.uv] exclude-newer` in `pyproject.toml` only admits releases at
  least ~2 weeks old at lock time. Move the date forward deliberately when upgrading.
- Git identity for this repo uses the GitHub noreply address (owner's email is never committed).

## Architecture
```
src/autopilot/
  config.py            config.yaml -> typed settings (pages, weekday slots, safe zones, gates)
  plan.py              due slots: Europe/London, weekdays, ±10 min jitter, catch-up window
  state.py             per-page state.json (public facts only; dates for tokens)
  manifest.py          build -> publish hand-over (media_urls: 1, or 2-10 carousel slides)
  pipeline.py          StoryBuilder: next approved queue post -> compose -> gates -> host -> manifest
  content/queue.py     post.yaml schema, approval (SHA-256 of inputs), queue order, art prompts
  content/             (also the archived quote-bank engine: bank, validate, similarity, captions)
  render/story.py      art crop, story text layout (**accent**, line breaks), adaptive cream panel
  render/              fonts (checksummed OFL), themes, layout/auto-fit, image, reel (art or procedural)
  audio/               Kokoro voice (pinned revision, SHA-256 on every load), procedural music, mix
  gates/story.py       per-slide gates + carousel gates (count, order, same size, strong cover)
  gates/               visual (size/contrast/placement), spelling, media/reel/voice gates
  publish/instagram.py stdlib-only Graph client: image, reel, carousel; publish/reconcile flow
  export.py            Export <post-id>: finished files + caption.txt + NOTES.txt (manual posting)
  ops/alerts.py        alert titles "[Page] problem — what to do" + public-safety check
  rehearsal/           fake Instagram, local hosting, network guard, runner, preview + approval
pages/simplestorymode/
  characters.yaml      Rowan & Wren traits; points to the originals in references/ (local only)
  queue/<post-id>/     post.yaml, art-NN.png (owner), approved.json (Build), preview/ (ignored)
  theme.yaml           storybook palette templates (placeholders, art-free reels)
  captions.json        banned + suggested hashtags; spellcheck-words.txt; pronunciations.yaml
  archive/             old quote bank — NOT postable
```
Story posts have no format fallback: what was approved is posted, or nothing. Reels keep the
narration fallbacks: second voice → music-only → skip + alert.

## Procedure: "New post: <topic>"
1. Pick the format (carousel by default; single image if the owner asks or the idea is one line)
   and the kind: `story` (a small scene that unfolds), `one-feeling` (one feeling seen from several
   sides), `repeated-opening` ("Please date...", "You can miss...", "You don't have to...",
   "Sometimes, love sounds like..."), `single`.
2. Write the words. Voice: gentle, personal, natural; clear emotional punch lines in short or
   medium sentences so the reader thinks "this is what I've been trying to say". **No lectures,
   long explanations or generic motivational quotes.** Never copy or closely echo a known quote or
   a real person's words; no real people. Each slide must add something new; end on a turn,
   comfort or small hope.
   Layout rules (so text stays ~80 px or larger): break lines yourself, **≤ 22 characters per
   line**, ≤ 6 lines per slide, first slide ≤ 16 words; blank line = new paragraph; `**words**`
   = accent colour (use once or twice per slide at most). 6 slides is a good carousel length (max 10).
3. For every slide write a brief: `scene`, `characters` (rowan / wren / others / none),
   `emotion` (expressions match the feeling — not always smiling), `composition` that names where
   the plain quiet space is, matching the slide's `position` (top / middle / bottom) and `focus`.
   Settings: autumn first (golden leaves, stone cottages, lanes, bridges, lakes, Highlands), also
   rainy windows, cosy homes, castles, lavender fields, spring flowers; palette cream, olive, rust,
   gold, brown, navy. Vary views and gestures. No signs, screens with text, letters or logos.
4. Caption (unique in the queue, no hashtags inside), 3–5 hashtags starting with
   `#simplestorymode`, none from `banned_hashtags`.
5. Save as `pages/simplestorymode/queue/<id>/post.yaml` (`c<NN>-slug` carousel, `i<NN>-slug`
   image, `r<NN>-slug` reel; set `priority`), run `python -m autopilot validate` and
   `python -m autopilot preview simplestorymode <id>`, and show the owner the preview page. The
   preview contains a copy-paste art prompt per slide (characters' must-have traits, "Normal
   rounded human ears — not pointed, not elf ears", "No text, no letters...").

## Procedure: "Build <post-id>"
1. Check the art files exist (`art-01.png` ... one per slide; 4:5 for posts, 9:16 for reels).
2. **Look at every art file yourself** (Read the image) and report to the owner: Rowan/Wren
   match their traits, **human ears (never pointed)**, no text/letters/signs/watermarks, emotion
   fits the slide, quiet space where the text goes. If anything is wrong, say so and do not build.
3. Run `python -m autopilot build simplestorymode <post-id>`. It composes the slides with the real
   art, runs every gate and, only if all pass, writes `approved.json` (SHA-256 of `post.yaml` and
   every art file). Show the owner `queue/<post-id>/preview/index.html`.
4. Before committing, run the privacy checks above; then commit `post.yaml`, the art and
   `approved.json` (stage 2: via pull request with CI). Any later edit to the text or art holds
   the post back until it is built again.

## Procedure: "Export <post-id>"
1. The post must be approved and unchanged since Build (`python -m autopilot queue` shows
   `APPROVED`); otherwise tell the owner to run Build first.
2. Run `python -m autopilot export simplestorymode <post-id>`. It composes the slides again
   from the approved art, re-runs every gate, and writes `exports/<post-id>/` (git-ignored):
   `01.jpg`, `02.jpg`, ... (or `reel.mp4`), `caption.txt` and `NOTES.txt`. Story reels are
   **postponed until after the first live post** (owner, 2026-10-19): only with `--reel` are
   `story-reel.mp4` + `story-reel-cover.jpg` made (about a minute; `--music <file>` uses a
   licensed CC0 track from `assets/music/`; `--no-reel` is still accepted). Nothing is uploaded.
3. Tell the owner the folder path, the file order, whether the AI label is needed (from
   `NOTES.txt`), the suggested time, and whether the story reel was made (and if not, why),
   and point them to the README guide "How to schedule in Meta Business Suite". Never copy
   exported files into tracked folders.

## Procedure: "Hold <post-id>" / "Release <post-id>"
`python -m autopilot hold simplestorymode <post-id> "<reason>"` adds `hold: "<reason>"` to
`post.yaml`; `release` removes it. A held post is never published or exported (approved or
not); hold/release does not invalidate an approval (the hold line is excluded from the hash).
The global pause stays the repository variable `PUBLISHING_ENABLED` (and disabling the
Autopilot workflow).

## Procedure: getting new art onto GitHub
Owner, in their own PowerShell window, once per batch (after Build):
`powershell -ExecutionPolicy Bypass -File .\scripts\send-posts.ps1` (or `-DryRun` first). It
pulls main, runs the privacy check and `validate`, sends only `post.yaml` / `approved.json` /
`art-NN.*` under `pages/*/queue/*/` on a `posts-<time>` branch as a PR with auto-merge (squash)
after the required checks, waits, then returns to an up-to-date main. Claude cannot merge PRs
itself (Claude Code's permission check refuses "merge without review").

## Spec vs current official docs (checked 2026-10-18) and other deliberate differences
| Topic | Spec says | Current docs / decision |
|---|---|---|
| Pages | `wisdom` enabled, `science` disabled, quote/fact banks of 400 | Owner decision: one story page, owner-approved queue only, empty queue → skip + alert; queue-low alert at < 5 replaces the 80-entry stock alert. |
| On-demand post | type a topic or text | Story posts need the owner's art, so Post now will publish a chosen approved queue item. |
| Carousels | — | API: max **10** items (the app allows 20); JPEG only; every slide is cropped to the first slide's aspect ratio (default 1:1) → all slides 1080×1350 (4:5), **to verify live**; `is_ai_generated` only on the carousel container (error on children); a carousel counts as one post. |
| Graph API version | "PIN_CURRENT" | Pinned `v26.0` (released 29 Jul 2026). Retirement date not announced yet → `graph_api_retires_on: null`. |
| Publishing limit | 50 posts / 24 h | Meta's content-publishing doc says 100 / 24 h in one section and 50 in another. We keep `quota_stop_at: 40`. |
| Hashtags | 5–8, follow lower limit | Instagram caps posts at **5 hashtags** since Dec 2025 → `max_hashtags: 5`, min 3. |
| Caption | default max 2,000 | API limit 2,200; we keep 2,000. Captions must be unique in the queue (reconciliation matches by caption). |
| Text size/contrast | 44 px, 4.5:1 | Owner wants huge, strong text: story slides ≥ 60 px and ≥ 7:1 (WCAG AAA level); watermark ≥ 28 px, 3:1. |
| Container polling | ~10 min | Meta: poll about once a minute for no more than 5 minutes → 15/30/60… s, 300 s timeout. |
| AI disclosure | API label if available, else caption | API supports `is_ai_generated=true` → set on narrated reels and on posts with the owner's AI-generated art (ChatGPT image generation; C2PA credentials embedded). Caption lines "Illustrations: AI-generated" / "Voiceover: AI-generated" also kept (configurable). **Not yet verified live.** |
| Reel safe zone bottom | 22 % | Meta guidance keeps the bottom 35 % clear (top 14 %, sides 6 %) → we use top 14 %, bottom 35 %, sides 8 %. |
| Grid crop | images: central 80 % height | Profile grid is now 3:4; our 4:5 image margins (90 %×80 %) are stricter, kept. |
| Kokoro voices | bm_george, bm_lewis | Both exist; added `bm_fable`. Owner to choose when reels start. |
| Fine-grained PAT permission | "Secrets: read and write" | GitHub lists environment-secret endpoints under the **"Environments"** repository permission. Stage 2/3 must use "Environments: Read and write". This also lets the PAT edit environment protection rules → `check_repo_settings.py` must verify branch policies weekly. |
| Token refresh | verify rules | `GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token`; token must be ≥24 h old; new token valid 60 days. Docs do not say whether the old token stays valid → verify in the live renewal test. |
| harden-runner | block mode | Block mode is free for public repos on GitHub-hosted **Linux** runners → all jobs run on ubuntu. |
| Scheduled workflows | 60-day idle switch-off | Confirmed in GitHub docs; "activity" not defined → keepalive re-enables via API + heartbeat; must be tested in stage 2. |
| Word-sync gate | ±150 ms of spoken time | Checked against Kokoro's own word timestamps (no speech recogniser). Catches alignment bugs, not TTS timestamp errors. |
| Public repo | everything public | Queued post text and art are visible in the repo before they are published (needed for free hosting/Actions). |
| Art checks | — | No reliable automatic check for ears, outfits, expressions or hidden text in art: Claude inspects each art file at Build time and the owner decides. |

## Upgrade procedure (API version, dependencies, model)
- **Graph API**: read Meta's changelog for every version since the pinned one; check the endpoints
  and fields in `publish/instagram.py`; bump `graph_api_version`; run tests; do one Post now live
  test before relying on the schedule.
- **Python deps**: move `exclude-newer` forward, `uv lock --upgrade`, run the full test suite and a
  `rehearse --days 2`; regenerate hashed requirement files (stage 2).
- **Kokoro**: change `tts.revision`, recompute every `tts.sha256` value from the new files, render
  samples and have the owner listen before switching.

## Roadmap — future pages (write-up only; do NOT build without a plan and the owner's approval)
Order agreed with the owner:
1. **@cloudtechclub** — platform engineering, SRE, architecture; DevOps, MLOps, AIOps, LLMOps.
   Text and diagram carousels drawn by our renderer, plus voice reels. Every technical claim needs
   two independent sources.
2. **Telugu civic page (@lyrics_reminder)** — civic sense, common sense, courtesy, respect,
   health, financial discipline, children's struggles, how everyone is doing. Fictional characters
   only; no songs, lyrics or real people; deity imagery always respectful. Text in Telugu with an
   open-licence font (check which — Noto Sans/Serif Telugu are OFL candidates). Check voice
   support (Kokoro has no Telugu voice). The owner reviews all Telugu text before it is queued.
   Children's topics: awareness and kindness only, nothing showing harm to children. Health and
   finance: general, sourced information, not personal advice. Note: the handle mentions lyrics
   although the page must not use any.
3. **@wellitstarted** — sourced company and founder stories (two sources each, no logos, no
   unverified quotes). First version: narrated reels with animated text. Presenter videos use the
   owner's own face; if AI-generated they need the AI label — check Meta's rules and which paid
   tools would be needed; assume none. The owner's face images stay in `references/` only.
4. **Comedy page ("@holduptightwith me" — exact handle to confirm; handles cannot contain spaces)**
   — deadpan comedy videos the owner records. Build a video queue (check specs, captions,
   schedule, publish). Safety topics must be accurate and not graphic.
5. A sixth page is not created yet; help the owner choose a niche later.

## Stage 2 layout (GitHub Actions)
- `.github/workflows/_pipeline.yml` (called): `build` (no secrets, read token, render stack) →
  `host` (write token, hashed minimal deps; media branch; URL checks) → `publish` (environment
  `page-<name>`; two phases with the intent uploaded in between; only if repo variable
  `PUBLISHING_ENABLED == 'true'`) → `record` (state branch, media removal, alerts, STATUS.md).
- `post-now.yml` (dispatch: post id or next approved), `autopilot.yml` (dispatch only; schedule
  commented out), `maintenance.yml` (dispatch only: renew 3 parts, health, keepalive, cleanup,
  status, token-set, test alert), `rehearsal.yml`, `ci.yml` (required check `ci-ok`).
- Job code: `src/autopilot/jobs/` (`python -m autopilot.jobs <job>`); inputs only via `AP_*`
  environment variables. Ops: `ops/github.py`, `ops/gitstore.py`, `ops/issues.py`,
  `ops/status.py`, `ops/token.py`, `publish/hosting.py`.
- Going live = set `autopilot.schedule_enabled: true` + `schedule_enabled_on`, uncomment the cron
  lines in `autopilot.yml` and `maintenance.yml`, switch harden-runner to block (docs/EGRESS.md).
- Before every commit/push: `python scripts/privacy_check.py`.

## Remaining work (next session starts here)
Mode: **moving to automatic posting** (see Scope); manual until the live tests pass.
1. **First live post first** (owner priority 2026-10-19; README "Connecting Instagram" A-G):
   c01 art → `Build c01-you-can-miss` → `send-posts.ps1`; Meta app (Other / Business) →
   Instagram product → tester → Generate token → `set-token.ps1`. When the owner says "token
   set": check the Maintenance `token-set` run, set repo variable `PUBLISHING_ENABLED=true`,
   then the owner runs Post now with `c01-you-can-miss`; record the result in HANDOVER.
   Renewal PAT (step H) after that, within ~6 weeks.
2. Art (owner): no post has art yet. Live tests need at least one single image (`i01`...), one
   carousel (c01 is in progress) and art for a reel test. Then `Build` each (look at every
   picture first) → live tests via Post now → token renewal test → schedule (owner authorised).
   Note: no `r..` reel post exists in the queue; the reel live test needs one (or a `story-reel`
   slot format, not built yet).
3. Approval removal: waiting for the owner to allow it in Claude Code (see "Approval policy").
