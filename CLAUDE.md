# CLAUDE.md — Instagram Autopilot (resume notes for Claude Code)

Read this first in every new session. The full specification is `INSTAGRAM_AUTOPILOT_SPEC_v3.md`.
Scope agreed with the owner: **build for Wisdom only. Science stays disabled** (no content bank, no
account connection) until the owner asks.

## Golden rules (never break)
- The repo **lumo-spec/instagram-autopilot is public**: anything written to code, logs, job
  summaries, artifacts, issues, commits or branches is public. Never write secrets, tokens,
  Instagram user IDs, raw API responses or stack traces from secret-holding jobs anywhere.
- Never ask the owner to paste a password or token into chat. Secrets are typed by the owner at a
  hidden prompt in their own terminal (see "Owner commands" below).
- **Scheduled publishing stays disabled** until the owner has reviewed the samples AND the live
  tests in spec §20 step 9 have passed. Do not create/enable the `autopilot.yml` schedule before that.
- Never claim something works unless it was run; record evidence in `docs/HANDOVER.md`.

## Progress

| Stage | What | Status |
|---|---|---|
| 1 | Local rehearsal: content bank, rendering, voice, music, gates, selection, planning, fake publishing, tests | **Done (2026-10-18)** |
| 2 | GitHub Actions workflows (ci, rehearsal, post-now, maintenance, autopilot — schedule OFF), media hosting on `media` branch, state/record job, alerts as issues, status page, token renewal (sealed hand-off), `audit_workflows.py`, `check_repo_settings.py`, hashed requirements, harden-runner (audit mode first), repo settings/rulesets/environments | Not started |
| 3 | Owner setup: Instagram Professional account + 2FA, Meta app + Instagram Login + tester, long-lived token, fine-grained PAT, `set-token` | Not started — needs the owner |
| 4 | Live tests (spec §20.9): one real image + one narrated reel via post-now, token renewal once, egress block mode, then enable schedule | Not started |

### Stage 1 evidence (2026-10-18, Windows 10, Python 3.12.15)
- `python -m autopilot validate` → Wisdom: 448 entries, 0 invalid, 0 near-duplicate pairs.
- `python scripts/verify_pd_quotes.py` → 81/81 public-domain quotes found verbatim in their
  Project Gutenberg sources (translator's text only, not editors' introductions).
- `python -m autopilot pronunciation-report wisdom` → 8 non-dictionary words, all covered.
- `pytest` (incl. slow end-to-end rehearsal) → all pass; coverage 88 % overall.
  `ruff check`, `ruff format --check`, `mypy --strict` → clean.
- `python -m autopilot rehearse --days 14` (18–31 Oct 2026, spans the 25 Oct clock change, every
  failure kind injected) → 28/28 slots posted, 0 double posts; fallbacks: second-voice ×1,
  music-only ×1; missed cron runs caught up (10:17 instead of ~08:30); container ERROR retried next
  run; transient publish error retried; crash-after-publish reconciled without re-posting; quota
  stop held then recovered. Output: `rehearsal-output/2026-10-18-14d/index.html` (git-ignored).
- `python -m autopilot sample wisdom` → one image + one reel per template:
  `rehearsal-output/samples-by-template/index.html` (git-ignored).

### Waiting on the owner
1. Review the samples (look, voices `bm_george` / `bm_fable` / `bm_lewis`, music) and say what to change.
2. Provide the Wisdom Instagram handle (needed for the watermark; currently `@CHANGE_ME`).

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
  uv sync                                   # install/refresh dependencies
  .venv\Scripts\python -m pytest -m "not slow"   # fast tests (~1 min)
  .venv\Scripts\python -m pytest                 # everything incl. end-to-end (~3 min)
  .venv\Scripts\python -m autopilot rehearse --days 14
  .venv\Scripts\python -m autopilot sample wisdom
  .venv\Scripts\ruff check src tests scripts ; .venv\Scripts\mypy
  ```
- Dependency freshness rule: `[tool.uv] exclude-newer` in `pyproject.toml` only admits releases at
  least ~2 weeks old at lock time. Move the date forward deliberately when upgrading.
- Git identity for this repo uses the GitHub noreply address (owner's email is never committed).

## Architecture (stage 1 code)
```
src/autopilot/
  config.py            config.yaml -> typed settings (pages, safe zones, gate thresholds)
  plan.py              due slots: Europe/London, deterministic ±10 min jitter, catch-up window
  state.py             per-page state.json (public facts only; dates for tokens)
  manifest.py          build -> publish hand-over; strictly re-validated by the receiver
  pipeline.py          Builder: select -> caption -> render -> gates -> fallbacks -> host -> manifest
  content/             bank selection, validation, near-duplicates, captions, sanitising
  render/              fonts (checksummed OFL), themes (8 templates), layout/auto-fit, image, reel
  audio/               Kokoro voice (pinned revision, SHA-256 on every load), procedural music, mix
  gates/               visual (size/contrast/placement), spelling, media/reel/voice gates
  publish/instagram.py stdlib-only Graph client + publish/reconcile flow (no render deps)
  ops/alerts.py        alert titles "[Page] problem — what to do" + public-safety check
  rehearsal/           fake Instagram, local hosting, network guard, runner, per-template samples
pages/wisdom/          content.json (built), sources/, captions.json, theme.yaml,
                       pronunciations.yaml, spellcheck-words.txt
scripts/               build_bank.py, verify_pd_quotes.py
```
Fallback order on a failed gate: second voice → music-only reel → image → skip + alert.
Narrated reels always use word-by-word reveal timed to Kokoro's word timestamps.

## Spec vs current official docs (checked 2026-10-18) and other deliberate differences
| Topic | Spec says | Current docs / decision |
|---|---|---|
| Graph API version | "PIN_CURRENT" | Pinned `v26.0` (released 29 Jul 2026). Retirement date not announced yet → `graph_api_retires_on: null`. |
| Publishing limit | 50 posts / 24 h | Meta's content-publishing doc now says 100 / 24 h (one section still says 50). We keep `quota_stop_at: 40`. |
| Hashtags | 5–8, follow lower limit | Instagram caps posts at **5 hashtags** since Dec 2025 → `max_hashtags: 5`, min 3. |
| Caption | default max 2,000 | API limit 2,200; we keep 2,000. |
| Container polling | ~10 min | Meta: poll about once a minute for no more than 5 minutes → 15/30/60… s, 300 s timeout. |
| AI disclosure | API label if available, else caption | API supports `is_ai_generated=true` → set on narrated reels; caption line also kept (configurable). **Not yet verified live.** |
| Reel safe zone bottom | 22 % | Meta guidance keeps the bottom 35 % clear (top 14 %, sides 6 %) → we use top 14 %, bottom 35 %, sides 8 %. |
| Grid crop | images: central 80 % height | Profile grid is now 3:4; our 4:5 image margins (90 %×80 %) are stricter, kept. |
| Kokoro voices | bm_george, bm_lewis | Both exist; added `bm_fable` (graded C like george; lewis is D+). Owner to choose. |
| Kokoro pronunciations | check syntax | Inline `[word](/phonemes/)` (misaki phonemes) — used. |
| Fine-grained PAT permission | "Secrets: read and write" | GitHub lists environment-secret endpoints under the **"Environments"** repository permission (read for public key, write for set). Stage 2/3 must use "Environments: Read and write". This also lets the PAT edit environment protection rules → `check_repo_settings.py` must verify branch policies weekly. |
| Token refresh | verify rules | `GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token`; token must be ≥24 h old; new token valid 60 days. Docs do not say whether the old token stays valid → verify in the live renewal test. |
| harden-runner | block mode | Block mode is free for public repos on GitHub-hosted **Linux** runners (Windows/macOS audit only) → all jobs run on ubuntu. |
| Scheduled workflows | 60-day idle switch-off | Confirmed in GitHub docs ("no repository activity in 60 days"); docs do not define "activity" → keepalive re-enables via API + heartbeat; must be tested in stage 2. |
| Word-sync gate | ±150 ms of spoken time | Checked against Kokoro's own word timestamps (no speech recogniser in the pipeline). It catches alignment/schedule bugs, not TTS timestamp errors. |
| Watermark | min font 44 px for text | Watermark is decorative: own thresholds (≥28 px, contrast ≥3:1), still placement-gated. Quote and attribution text use 44 px / 4.5:1. |
| Seneca, Rumi | examples of PD authors | Not used: Gutenberg's Seneca (L'Estrange 1678) is a loose paraphrase, and most popular "Rumi quotes" are modern copyrighted versions. Used Marcus Aurelius (Long 1862), Epictetus (Higginson 1865), Lao Tzu (Legge 1891), Thoreau, Emerson. |
| Original reflections | original | ~70 drafted lines were removed because they resembled sayings attributed to real people (e.g. Beattie, Ashe, Brown). |

## "Top up" procedure (spec §5.5) — when the owner types "Top up Wisdom"
1. Write `topup_batch` (150) new original reflections in `pages/wisdom/sources/originals.txt`
   (format `primary-tag, other-tags | text`, ≤180 chars, calm tone, no resemblance to known quotes
   by real people), and/or new public-domain quotes in `public_domain.yaml` — each must be found
   verbatim by `python scripts/verify_pd_quotes.py`.
2. For a facts page (not built yet): two independent reputable sources per fact found by web search.
3. Run `python scripts/build_bank.py wisdom` (removes and reports near-duplicates) and
   `python -m autopilot validate` and `python -m autopilot pronunciation-report wisdom`; add British
   spellings to `spellcheck-words.txt` and words to `pronunciations.yaml` as needed.
4. Open a pull request, let CI pass, merge it (stage 2 adds the PR rule).
5. Tell the owner in plain English: added, rejected (and why), stock left (unused entries ÷ 2 per day).

## Upgrade procedure (API version, dependencies, model)
- **Graph API**: read Meta's changelog for every version since the pinned one; check the endpoints
  and fields in `publish/instagram.py`; bump `graph_api_version`; run tests; do one `post-now` live
  test before relying on the schedule.
- **Python deps**: move `exclude-newer` forward, `uv lock --upgrade`, run the full test suite and a
  `rehearse --days 2`; regenerate hashed requirement files (stage 2).
- **Kokoro**: change `tts.revision`, recompute every `tts.sha256` value from the new files, render
  samples and have the owner listen before switching.

## Remaining work (next session starts here)
1. Owner: review samples, give the handle → set `pages.wisdom.handle`, re-render a few samples.
2. Stage 2 (see table). Keep `autopilot.yml` without an enabled schedule until stage 4.
3. Stage 3 owner guidance: step-by-step, Windows PowerShell commands with hidden prompts
   (`Read-Host -AsSecureString` piped to `gh secret set --env`), never via chat.
4. Stage 4 live tests and `docs/HANDOVER.md` checklist (spec §18).
