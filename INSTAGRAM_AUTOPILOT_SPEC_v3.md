# Instagram Autopilot — Complete Build Specification (v3)

**Revision note:** v3 rebuilds v2 after a security and reliability review. It adds multi-page support, trust-zone isolation of secrets, a hardened feed/AI path, fixes for four bugs that would have broken v2, automated pre-post quality gates, a rehearsal mode and a status/alert system.

**Instructions for Claude Code:** Build this entire system end to end. The owner wants a fully hands-off result: after setup, their routine tasks are glancing at their Instagram profile and, rarely, answering an alert email. Make all technical decisions yourself using this spec. Only stop for the owner when a step needs them personally (logins, account settings, typing a secret into their own terminal). When a detail here may be out of date (API fields, library versions, Instagram media specs and safe zones, hashtag limits, Meta disclosure rules, GitHub Actions behaviour), check the current official documentation and follow it; where this spec and the official docs disagree, follow the docs and note the difference in `CLAUDE.md`. After building, create a short `CLAUDE.md` summarising the architecture, the "Top up" procedure (section 5.5) and the upgrade procedure for future sessions.

**Golden rule: this repository is public, so everything the system writes anywhere (logs, job summaries, artifacts, issues, commits, branches) is public.** Nothing sensitive may ever be written to any of them.

---

## 1. What the system does

The system runs one or more themed Instagram **pages** (accounts). Each page has its own account, token, content bank, schedule and look. Ship two page definitions: `wisdom` (enabled) and `science` (defined, disabled until the owner connects its account). Adding a page later must need only a config block, a content bank, an environment and two secrets (section 12).

**Autopilot (default, no owner input)**
- Each page publishes its configured slots every day (default 2 posts a day per page).
- Default mix per page: one quote/fact image and one animated reel. Ratio and times are set in `config.yaml`.

**On demand (optional)**
- The owner opens GitHub (mobile app or phone browser), taps **Run workflow** on "Post now", picks a page and types a topic or text. A post is published within minutes. On-demand posts are extra and never cancel scheduled ones.

**Rehearsal (optional, no posting)**
- The entire system can run as a full practice with no Instagram calls and no secrets (section 17).

## 2. Hard requirements

- Use only the official **Instagram API with Instagram Login** (host `graph.instagram.com`). Request only `instagram_business_basic` and `instagram_business_content_publish`. Never use the owner's password, unofficial libraries, or browser automation.
- Run free on **GitHub Actions** in a **public** repository (public gives free hosting and unlimited Actions minutes).
- No paid services. No AI API is required for normal operation (it is optional, section 15).
- No copyrighted music, fonts, quotes or article text, and no cloning of any real person's voice.
- Handle errors and recovery automatically (section 11). When a quality check fails, fall back to a safer format; never post something flawed.
- Be explicit about what is public: the owner must be told in the README that nothing secret lives in the repo or its logs.

## 3. Trust zones (the security architecture)

Split every run into jobs with different powers. A job that holds a secret runs only small, audited code; jobs that run big dependency trees or handle untrusted text hold no secrets.

| Job | Purpose | Secrets | Token permissions | Network allowed |
|---|---|---|---|---|
| `plan` | decide which page/slots are due | none | `contents: read` | GitHub only |
| `fetch-feeds` (optional page type) | read news feeds safely | none | none | feed hosts in config only |
| `summarise` (optional) | AI writes a summary | `AI_API_KEY` only (environment `ai-summary`) | none | the AI provider host only |
| `build` | pick content, render, run quality gates, host media | none | `contents: write` (media branch only) | PyPI, model host (cache miss only), GitHub |
| `publish` | create and publish the Instagram container | per-page `IG_ACCESS_TOKEN`, `IG_USER_ID` (environment `page-<name>`) | none | `graph.instagram.com`, `raw.githubusercontent.com`, `api.github.com` |
| `record` | write state and status, raise alerts | none | `contents: write` (state branch), `issues: write` | GitHub only |
| `renew` | refresh and rotate tokens | see section 12 | see section 12 | `graph.instagram.com`, `api.github.com` |

Rules:
- **Secrets live in GitHub Environments, never as plain repository secrets.** One environment per page (`page-wisdom`, `page-science`), one for token renewal (`renewal`), and optionally `ai-summary`.
- **Every environment is restricted to the `main` branch** (deployment branch policy = `main` only). A copy of the code on any other branch, a fork, a pull request, or Dependabot cannot read any secret.
- `publish` installs only a minimal hashed requirements file (`requirements-publish.txt`: HTTP client + YAML parser). It must not install Pillow, TTS, audio or any other render dependency.
- Jobs pass data to each other only as small, validated strings or files; every receiving job re-validates what it receives.

## 4. Repository layout

```
.github/workflows/
  ci.yml               # lint, type-check, tests, workflow audit, secret scan (no secrets)
  autopilot.yml        # scheduled posting (plan -> build -> publish -> record)
  post-now.yml         # on-demand posting (workflow_dispatch)
  rehearsal.yml        # full practice run, never posts (manual)
  maintenance.yml      # token renewal, weekly health check, schedule keepalive, status page
src/autopilot/
  __main__.py          # CLI: plan | build | publish | record | rehearse | status | renew
  plan.py              # which page/slot is due now (timezone, jitter, catch-up)
  content/bank.py      # selection, tag matching, duplicate and near-duplicate prevention
  content/validate.py  # schema checks for quote and fact banks
  content/feeds.py     # protected feed reader (optional page type)
  content/summary.py   # untrusted-text handling, AI call, output validation (optional)
  render/image.py      # images (Pillow)
  render/reel.py       # animated reels
  audio/voice.py       # local TTS, pronunciation list, voice styling
  audio/music.py       # procedural ambient music, optional CC0 library
  audio/mix.py         # ducking, loudness normalisation
  gates/               # pre-post quality gates (section 9)
  publish/instagram.py # quota, container, status polling, publish, reconcile (minimal deps)
  publish/hosting.py   # temporary public media hosting + cleanup
  ops/token.py         # token refresh and sealed hand-off
  ops/alerts.py        # alert issues
  ops/status.py        # status page
  rehearsal/           # fake clock, fake Instagram client, local hosting
pages/<page>/
  content.json         # quote or fact bank for this page
  captions.json        # caption templates, hashtag pools
  pronunciations.yaml  # words the voice must say a particular way
  spellcheck-words.txt # page-specific allowed words
assets/
  fonts/               # open-licence fonts + LICENSE files + checksums
  music/               # optional CC0 tracks + licences.json
scripts/
  audit_workflows.py   # fails on unsafe workflow patterns (section 16)
  set-token.sh         # owner helper: hidden prompt, sets the secrets for a page
  check_repo_settings.py # verifies repo/environment settings via the GitHub API
config.yaml            # all tunable settings
tests/                 # unit, gate, injection and render smoke tests
docs/HANDOVER.md       # checklist results (section 18)
docs/EGRESS.md         # the approved outbound-connection list per job
requirements-*.txt     # hashed, separate files per job type
pyproject.toml         # tool settings, pinned Python version
CLAUDE.md  README.md  SECURITY.md  THIRD_PARTY_LICENSES.md  .gitignore
```

**Branches**
- `main`: code, content banks, config. Protected (section 16). Nothing writes to it at runtime.
- `state`: runtime records only, one folder per page (`pages/<page>/state.json`), plus `STATUS.md`. Protected only against deletion and force-push, so the system can commit to it.
- `media`: temporary hosted media, one folder per page (`<page>/<slot-key>.<ext>`). Never force-pushed to wipe other pages' files (section 10).

## 5. Content

### 5.1 Page types
- `quotes`: calm, reflective "daily wisdom" posts.
- `facts`: science facts.
- `news_digest` (optional, disabled by default): feed-based summaries (section 15).

### 5.2 Quote banks
- Start with at least **400** reviewed entries per quotes page (the owner tops up later, section 5.5).
- About 80% original short reflections with no attribution; about 20% verified public-domain quotes (e.g., Marcus Aurelius, Seneca, Epictetus, Lao Tzu, Rumi in a public-domain translation) with accurate attribution and a source note. Skip any quote whose attribution is doubtful.
- Fields: `id`, `text` (max ~180 characters), `author` (nullable), `tags`, `length_class`, `status` (`verified`).
- Include a synonym map ("anxiety" matches "fear" and "peace"; "hard times" matches "resilience").

### 5.3 Science fact banks
Carefully checking 1,000 facts at once is not realistic, so **start with 400 checked facts** per facts page.
- Fields: `id`, `text` (max ~200 characters), `topic` tags, `sources` (at least two independent reputable references: textbooks, agencies such as NASA/ESA/NIST, or peer-reviewed reviews, each with title and URL), `checked_on`, `verification_note`, `status` (`verified`).
- Only established facts. Exclude anything contested, fast-changing, dated ("the largest known…"), or medical advice. State figures with their units and the right precision.
- Run `validate.py` on every entry; entries without two sources or with `status` other than `verified` can never be posted.
- During setup, show the owner 20 random facts in plain English with their sources, so they can spot-check quality (recommended, not blocking).

### 5.4 Selection rules (all page types)
- Never repeat an entry until the whole bank has been used. After that, recycle those used longest ago (facts: only after 120 days), with a different visual template.
- Don't post two entries with the same primary tag on the same day on the same page.
- **Near-duplicate removal:** when building or validating a bank, and again at selection time against the last 200 posts, reject entries that are near-copies of another (normalise case, punctuation and whitespace; flag anything above a similarity threshold such as 0.85 token-set similarity or a high shingle overlap). Report what was removed.
- When the unused count falls below `low_stock_warning` (default 80), raise the "low stock" alert (section 13).

### 5.5 "Top up" procedure
When the owner opens Claude Code in this repo and types **"Top up Science"** (or any page name), Claude Code must:
1. Write `topup_batch` (default 150) new entries for that page, following 5.2 or 5.3.
2. For facts, check each against at least two independent sources using web search, and record them.
3. Run `validate.py` and the duplicate and near-duplicate check against the whole bank and recent posts.
4. Open a pull request, let CI pass, and merge it (owner approves if the repo rules require).
5. Tell the owner in plain English how many were added, how many were rejected and why, and how much stock is left.
Document this in `CLAUDE.md` and the README.

### 5.6 Captions (`pages/<page>/captions.json`)
- Short reflective line, the quote or fact itself (for accessibility), a rotated gentle call to action, and hashtags from rotating pools (default 5–8; follow Instagram's current hashtag limit if lower).
- Never repeat the same hashtag set on consecutive days. Avoid banned or restricted hashtags.
- Include the AI disclosure for narrated reels (section 8).

## 6. Visual design

- **Images:** 1080×1350 JPEG (4:5), sRGB, within Instagram's size limit.
- **Reels:** 1080×1920, 9:16, H.264 + AAC in MP4, 30 fps, 10–30 seconds. Verify against Instagram's current reel specs.
- **Templates:** at least 8 per page, rotated so consecutive posts differ (dark gradients, paper texture, minimal serif on solid colour, subtle grain, slow abstract shapes; each page gets its own palette).
- **Safe zones:** keep all text inside Instagram's current safe zones so the profile-grid crop and reel buttons/captions never cover it. Defaults (verify against current guidance): reels leave 14% at the top, 22% at the bottom and 8% at each side; images keep text within the central 90% of the width and 80% of the height (the grid shows a centre crop).
- **Fonts:** only SIL Open Font License fonts (e.g., Cormorant Garamond, Lora, Playfair Display, Inter) in `assets/fonts/` with licence files, checked against pinned checksums.
- **Reel animation styles** (rotated): word-by-word reveal, line fade-in, slow zoom on a generated background, drifting particles. Text animation is timed to the narration when a voice is present.
- Small handle watermark from the page's `handle`.
- Auto-fit text: wrap and scale so it never overflows, with a minimum readable size.

## 7. Audio for reels

Audio modes rotate by weights in the config (defaults: voice+music 50%, music only 30%, voice only 20%).

**Narration**
- **Kokoro TTS** (Apache-2.0) run locally, no external API. Calm, deep British male voice such as `bm_george` or `bm_lewis` (verify current names). The config may list several voices.
- Fall back to **Piper** only with a voice whose licence permits commercial use.
- Pin the model revision, verify its SHA-256 **every time after restoring from cache**, and cache with `actions/cache`.
- Wise-narrator style: rate about 0.85–0.9×, pitch down 1–2 semitones, light reverb, gentle compression, natural pauses at punctuation and line breaks, 0.5–1 s of silence before the voice.
- **Pronunciation list:** each page has `pronunciations.yaml` mapping unusual words (scientific terms, names, foreign words) to the spelling or phoneme markup the TTS engine needs (check what Kokoro currently supports). Apply it before synthesis. Add a test that every word in the content banks not found in a standard dictionary is either in the pronunciation list or deliberately waived; surface these as a report.

**Music**
- Default: procedurally generated ambient tracks, unique per reel (no copyright risk).
- Optional CC0 tracks in `assets/music/`, each recorded in `licences.json` with source URL and licence. Reject any track without an entry.
- Duck the music about 12 dB under the voice; fade in and out.
- Normalise the final mix to about −14 LUFS integrated, true peak ≤ −1 dBTP.

## 8. AI disclosure

- Check Meta's current rules for AI-generated realistic audio, and whether the API can apply the "AI info" label.
- If the API can apply it, use it for narrated reels.
- If not, add a caption line such as "Voiceover: AI-generated" to narrated reels. Configurable, on by default.

## 9. Pre-post quality gates

Run every gate in `build` (and the cheap ones again in `publish`) before anything is published. **A failed gate blocks that format; it never posts a flawed one.**

**Text**
- **Readability:** minimum font size (default 44 px at 1080 wide) and contrast of at least 4.5:1 between text and the background actually under it (sample the rendered pixels behind each text box).
- **Placement:** compute the bounding box of every text element (including watermark and any animation end state) and require it to sit inside the safe zones in section 6.
- **Spelling:** check every word with a local dictionary plus the page's allowed words. Unknown words fail the gate unless allow-listed.
- **Lengths:** caption within Instagram's limit (verify; default max 2,000 characters), hashtag count within the configured limit, quote/fact text within its max length, no empty fields.

**Reels (narration)**
- An audio stream must exist when the mode needs one, and its duration must match the video within 0.1 s.
- **Missing voice:** the voice track must contain real speech (energy above a threshold for a minimum share of the spoken interval).
- **Wrong timing:** voice onset must fall within 0.5–1.0 s; narration must end at least 1 s before the video ends; each word's on-screen reveal must be within ±150 ms of its spoken time; no clipping (peak below −1 dBFS); mix loudness −14 LUFS ±1.
- Any failure blocks the narrated reel.

**Fallback on a failed gate (in order):** regenerate the voice once with another configured voice → music-only reel (gates re-run) → image post → skip the slot and raise an alert. Record which fallback was used in state.

Every gate has unit tests with deliberately failing fixtures.

## 10. Publishing flow

1. **Plan** (`plan` job, runs often, usually does nothing): for each enabled page, work out whether a slot is due (section 11) and output a matrix of due page/slot pairs. If none, stop.
2. **Quota:** for each due page, `GET /{ig-user-id}/content_publishing_limit` and stop safely if usage is at or above `quota_stop_at` (default 40 of 50 per 24 h).
3. **Build** (no secrets): pick the content, render the media, run all gates (section 9), build the caption, and write a small manifest (page, slot key, content ID, format, audio mode, caption, hosted URL).
4. **Host** in `build`: add the file to the `media` branch under `<page>/<slot-key>.<ext>` and serve it via `raw.githubusercontent.com`. Each page has its own folder, so two pages can never overwrite each other's files. Never force-push the shared branch: add the file with a normal commit, and on a non-fast-forward error fetch, rebase and retry (up to 5 times with random delay). Confirm the URL returns HTTP 200, the right `Content-Type`, the right `Content-Length`, and (for video) range support before continuing. Fallback: GitHub Pages from a `gh-pages` branch that likewise keeps per-page folders and never deploys a single file over the others. Test both with JPEG and MP4 and use the most reliable.
5. **Publish** (`publish` job, per-page environment, matrix `max-parallel: 1`, concurrency group `instagram-publish-<page>`, `cancel-in-progress: false`):
   - Re-validate the manifest (URL host is the repo's own, caption limits, file reachable).
   - **Write an intent record first** (slot key and container ID) so a crash cannot cause a double post.
   - Images: `POST /{ig-user-id}/media` with `image_url` and `caption`. Reels: `media_type=REELS`, `video_url`, `caption`, `share_to_feed=true`.
   - Poll `GET /{container-id}?fields=status_code` with backoff until `FINISHED` (time out after about 10 minutes; `ERROR` is a failure).
   - `POST /{ig-user-id}/media_publish` with `creation_id`. Retry 3 times with exponential backoff.
   - Fetch `permalink` for the new media.
6. **Record** (`record` job): write date, slot, format, audio mode, fallback used, content ID, media ID and permalink to `pages/<page>/state.json` on the `state` branch (retry with rebase on conflict), then remove the hosted media, then write the run page summary and update `STATUS.md` (section 13).
7. **Reconcile on retry:** if a slot has an intent record but no media ID, check the container's `status_code` (`PUBLISHED` means done) and recent media before publishing again. A slot may never be published twice.
8. **Pin the API version** in the config; warn about 90 days before Meta retires it.
9. Periodically (weekly, in `maintenance.yml`, inside the same concurrency group) delete leftover media and squash the `media` branch history, so history stays small without ever touching live files.

## 11. Reliability and self-healing

- **Idempotency:** each slot has a key such as `wisdom-2026-09-18-morning`. If state shows it posted, do nothing.
- **Schedule and timing:** GitHub cron runs in UTC and may be delayed. `autopilot.yml` runs every 30 minutes at odd minutes (e.g., `17,47`) across the hours that can cover the slots under both BST and GMT, and the `plan` job converts to the owner's timezone (**Europe/London**) itself, so clock changes need no workflow edits. Each slot's target time gets a **deterministic per-day offset of up to ±10 minutes** (derived from a hash of page+date+slot) so posting times look natural without sleeping inside the job. A slot is due once its target time has passed.
- **Catch-up:** a slot that is due, unposted, and still within `catchup_window_hours` (default 5) and not too close to the next slot is posted by the next run. Slots from earlier days are skipped, never back-filled.
- **Staggering:** different pages use different slot times so their runs rarely overlap.
- **Concurrency:** GitHub keeps at most one waiting run per concurrency group, so a waiting run can be replaced. Catch-up covers scheduled slots. The status page flags any cancelled on-demand run, and the README tells the owner to run it again.
- **Fallbacks:** section 9 (format fallbacks); hosting method 1 → 2; publish retries.
- **Keepalive:** GitHub switches off scheduled workflows in repositories with no recent activity. Every week `maintenance.yml` (token permission `actions: write`) re-enables every scheduled workflow through the API and makes a small heartbeat commit on the `state` branch. Test that this actually works and note the result in `docs/HANDOVER.md`. If the repo were ever left idle for 60+ days, the schedules can switch off silently; document this in the README, and optionally support a free external heartbeat check (disabled by default).
- **Weekly health check:** confirms each page's token is valid and not near expiry, the quota is readable, scheduled runs are actually happening, posting has happened in the last 36 hours per page, and the renewal key is not near expiry. Anything wrong raises an alert.

## 12. Tokens and secrets

**Where secrets live**
- Per page, in environment `page-<name>`: `IG_ACCESS_TOKEN`, `IG_USER_ID`.
- In environment `renewal`: `GH_SECRETS_PAT`, a **fine-grained** GitHub token for **this repository only** with the single permission **Secrets: read and write**, expiry about 1 year. Only the `renew` job may use it.
- Optional, in environment `ai-summary`: `AI_API_KEY`.
- Never in code, logs, artifacts, issues, job summaries or chat. Claude Code must never ask the owner to paste a secret into the conversation and must never read, echo or store secret values. The owner sets secrets in their own terminal; `scripts/set-token.sh <page>` prompts at a hidden input and calls `gh secret set --env`.

**Token renewal (every week, `maintenance.yml`)**
Instagram long-lived tokens last about 60 days; refresh via `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token` (verify current rules, such as minimum token age). The environment restriction means one job cannot read both a page's token and the renewal key, so use a **sealed hand-off** (or an equivalent design that keeps all of these properties):
1. `renew` job part 1, environment `renewal`: use `GH_SECRETS_PAT` to fetch each page environment's public key and publish those public keys (not secret) to the `state` branch.
2. Part 2, environment `page-<name>` (no PAT): refresh the token, mask old and new tokens with `::add-mask::`, encrypt the new token to the public key with a libsodium sealed box, and hand over **only the ciphertext** (a 1-day artifact is acceptable because only GitHub can decrypt it).
3. Part 3, environment `renewal`: write the ciphertext to the page's environment secret via the API. The PAT job never sees a plaintext Instagram token; the page job never sees the PAT.
4. If any step fails, keep the old token (verify that it stays valid after a refresh), raise an alert and retry next run. Track token age/expiry in state (dates only).

**Expiry watch:** alert when a page token has fewer than 14 days left, and when `GH_SECRETS_PAT` expires within 30 days (read the expiry from the GitHub API response header `github-authentication-token-expiration`; verify).

## 13. Alerts and status

**Alerts**
- Healthy runs send **no** email. Success and routine news go to the status page only.
- Alerts are GitHub Issues created by the `record`/`maintenance` jobs (`issues: write`), labelled `autopilot-alert`, with the owner `@mentioned` so GitHub emails them, then **locked** so outsiders can't comment. One open issue per alert key (comment on it instead of duplicating); close it automatically when the condition clears.
- **The title says exactly what is wrong and what to do**, in the form `[Page] problem — what to do`. Examples:
  - `ACTION NEEDED [Science]: facts running low — open Claude Code and type: Top up Science`
  - `ACTION NEEDED: renewal key expires 12 Nov — renew it (README step 5)`
  - `[Wisdom] token renewal failed — will retry; if repeated, regenerate the token (README step 6)`
  - `[Wisdom] no post for 30+ hours — check the Status page`
  - `Instagram API version retires on 1 Feb — ask Claude Code to upgrade`
- The issues are public. Wording stays generic: page name, plain problem, plain fix, link to the run. Never include tokens, IDs, API error bodies, stack traces or file contents.
- Unhandled failures still fail the workflow, which makes GitHub send its own failure email. Handled problems (a fallback worked) do not fail the workflow; they raise an alert only if the owner needs to act.
- Test the alert path during setup: create a harmless test alert, confirm the owner receives the email, then close it.

**Status**
- After every posting run, the run page (job summary) shows a small preview and a link to the post.
- One status page, `STATUS.md` on the `state` branch and in the summary of a `Status` run, shows for every page: enabled or not, last post time and link, next scheduled slots, token days left, stock remaining (and roughly how many days it lasts), fallback counts in the last 7 days, last cancelled run. It contains no secrets.

## 14. On-demand workflow (`post-now.yml`)

**Inputs:** `page` (choice of enabled pages), `text` (max 300 characters), `format` (`auto`|`image`|`reel`), `audio` (`auto`|`music`|`voice`|`voice+music`), `interpret_as` (`auto`|`topic`|`exact_text`).

**Interpretation (`auto`):** 1–4 words are a topic (match tags and synonyms, pick the best unused entry); longer text is exact text, posted as written after trimming and checks. If a topic matches no tag, post the typed words as an elegant headline-style post; if the optional AI step is enabled, generate one entry on the topic instead (output validated exactly as in section 15).

**Security:** only users with write access can run it. Pass inputs to scripts **only through environment variables**, never as `${{ inputs.* }}` inside `run:` lines. Sanitise and length-limit all inputs in Python. On-demand posts go through the same build, gates, publish and record jobs as scheduled ones.

## 15. Optional feed and AI step (disabled by default)

Used by `news_digest` pages and the unmatched-topic case. Everything fetched from a feed or typed as free text is **untrusted data, never instructions** ("prompt injection").

- **Protected reading** (`fetch-feeds` job, no secrets): HTTPS only; hosts only from an allowlist in `config.yaml`; redirects must stay on the allowlist; timeouts; response size cap (e.g., 1 MB); parse XML with a safe parser (`defusedxml`); strip HTML; normalise Unicode and drop control and zero-width characters; truncate each field; keep only title, source name, date and a short excerpt.
- **The AI can only write a summary.** `summarise` runs in its own job with its own environment, `permissions: {}`, no repository write access, and network access only to the provider host. One plain completion call: no tools, no browsing, no function calling, no access to other secrets or to the repository. Put feed text inside clearly delimited data blocks with an instruction that it is data only.
- **Automatic rejection of suspicious output.** Accept only a JSON object matching a strict schema with a length limit. Reject (and fall back or skip) if the summary contains URLs, @handles, hashtags, email addresses, phone numbers, markup, instruction-like phrases ("ignore", "system prompt", "as an AI", and similar), characters outside the allowed set, any number or proper noun not present in the source text, or text copied verbatim beyond a short run from the source. Then run the normal quality gates.
- Summaries are written in the page's own words with the source named; never reproduce article text.
- Test suite: adversarial feed samples (hidden instructions, HTML tricks, oversized fields, look-alike characters, redirects to other hosts) must all be neutralised or rejected.

## 16. GitHub Actions and repository security

**Workflows**
- Top-level `permissions: {}` in every workflow; grant each job only what the table in section 3 says.
- Pin **every third-party action to a full commit SHA** with the version in a comment. Pin Python and install dependencies with `--require-hashes`.
- Never use `pull_request_target`. `ci.yml` runs on pull requests from the same repository and on pushes, with no secrets and `contents: read`.
- Use `step-security/harden-runner` (pinned) as the first step of every job. **Outbound connections are blocked unless approved** (`egress-policy: block` with an allowed-endpoints list per job, matching the section 3 table). Start in audit mode during the first rehearsal runs to learn the real list, record it in `docs/EGRESS.md`, then switch to block before enabling the schedule. A compromised dependency must not be able to send data anywhere else.
- Never use `set -x`; never print environment variables, request headers or raw API responses; log structured, whitelisted fields only; mask any token before it could appear.
- `scripts/audit_workflows.py` (run in CI) fails the build on: `${{ inputs.` or `${{ github.event.` inside `run:`, `pull_request_target`, an unpinned action, missing `permissions`, missing `harden-runner`, a job with an environment that also installs render dependencies, or a workflow that references a secret outside its environment.

**Repository settings** (apply through `gh`/API where possible, and verify with `scripts/check_repo_settings.py`)
- Ruleset on `main`: block force-push and deletion, require pull requests and passing `ci.yml`. Ruleset on `state` and `media`: block deletion and (for `state`) force-push only.
- Environments `page-<name>`, `renewal`, `ai-summary`: deployment branches limited to `main`.
- Default workflow permissions: read-only. "Allow GitHub Actions to create and approve pull requests": off.
- Fork pull request workflows: require approval for all outside collaborators.
- Enable secret scanning and push protection; Dependabot security alerts only (no auto-merge); add a secret scanner (such as gitleaks) to CI.
- Disable wiki and projects. Keep Issues enabled (used for alerts).
- Include `SECURITY.md` and a `.gitignore` that excludes `.env`, media output and caches.

## 17. Code standards, tests and rehearsal mode

**Build standards**
- Python pinned (e.g., 3.12), one package (`src/autopilot`), `pyproject.toml` for all tool settings.
- Automatic checks on every change in `ci.yml`: formatter and linter (e.g., Ruff), strict type checking (e.g., mypy or pyright), unit tests with a coverage floor of 85% on logic modules, the workflow audit, the secret scan, a dependency licence check.
- Small modules with docstrings, no dead code, no copy-pasted blocks, and clear error types. All settings come from `config.yaml`, never constants buried in code.

**Tests** cover: selection and tag matching, duplicates and near-duplicates, input sanitisation, idempotency and reconcile, timezone/BST handling and per-day jitter, catch-up, every quality gate (passing and failing fixtures), text fitting, media validation, state write with rebase conflicts, per-page hosting isolation, alert dedupe and wording (no secrets), feed injection samples, and token-renewal hand-off with a fake GitHub API.

**Rehearsal mode**
- `rehearsal.yml` (manual) and `python -m autopilot rehearse --days N` run the **whole system as a practice**: fake clock, all pages, real rendering, real quality gates, real voice, real selection, real state logic against a throwaway state, local file hosting, and a **fake Instagram client** that records what it would have sent. No secrets, no environment, and no Instagram network access (assert this in a test and with the egress policy).
- Output: a report showing every simulated post, which gates passed or failed, fallbacks used, stock use, and the sample media for the owner to download and approve. Samples are public like everything else, so they only ever contain bank content.
- Default rehearsal: 14 simulated days across all enabled pages, including a clock change and a simulated failure of each kind.

## 18. Final checklist (Claude Code must complete before handover)

Run each check for real, record the evidence in `docs/HANDOVER.md` (command or API call and result), and report pass/fail to the owner in plain English. Never mark anything passed that was not run. List anything that could not be verified.

**Security**
1. `check_repo_settings.py` passes: rulesets, environment branch limits, read-only default permissions, fork approval, secret scanning, push protection.
2. `audit_workflows.py` passes; every action is SHA-pinned; every workflow starts with `permissions: {}`.
3. A branch other than `main` cannot read any environment secret (test with a throwaway branch and a harmless workflow that tries to read a dummy secret).
4. The renewal key is in `renewal` only; no job other than `renew` can read it.
5. Logs from the rehearsal and the first live runs contain no tokens, IDs or secrets (scan them).
6. Egress is in block mode and the first live runs succeed under it.
7. Feed and AI injection tests all pass.
8. No secrets in git history (scanner result).

**Reliability**
9. State writes work with `main` protected; two pages can write at once; media files of two pages never collide.
10. Idempotency and reconcile tests pass; a simulated crash after "publish" causes no double post.
11. The weekly keepalive re-enables schedules, and a simulated missing slot is caught up.
12. The alert path works end to end (test alert received and closed).

**Quality**
13. Lint, type checks and all tests pass, with coverage at or above the floor.
14. A 14-day rehearsal shows no failed gates, or each failure is explained and fixed.
15. Content: bank sizes meet sections 5.2/5.3, licences recorded, near-duplicate report clean, pronunciation report reviewed.
16. The Status page renders correctly for every page.

## 19. `config.yaml` defaults

```yaml
timezone: Europe/London
graph_api_version: "PIN_CURRENT"
jitter_minutes: 10
catchup_window_hours: 5
limits: { caption_max_chars: 2000, max_hashtags: 8, quota_stop_at: 40 }
safe_zones:                      # verify against current Instagram guidance
  reel: { top: 0.14, bottom: 0.22, sides: 0.08 }
  image: { width: 0.90, height: 0.80 }
gates: { min_font_px: 44, min_contrast: 4.5, voice_onset_s: [0.5, 1.0], word_sync_ms: 150, loudness_lufs: [-15, -13] }
audio_weights: { "voice+music": 0.5, music: 0.3, voice: 0.2 }
voices: [bm_george, bm_lewis]    # verify names
voice_style: { rate: 0.88, pitch_semitones: -1.5, reverb: light }
music_source: procedural         # or: library
ai_disclosure_caption: true
ai_generation: { enabled: false }
alerts: { low_stock_warning: 80, token_days_warning: 14, pat_days_warning: 30, no_post_hours: 30 }
topup_batch: 150
pages:
  wisdom:
    enabled: true
    type: quotes
    handle: "@CHANGE_ME"         # ask the owner during setup
    theme: daily_wisdom
    slots:
      morning: { time: "08:30", format: image }
      evening: { time: "19:30", format: reel }
  science:
    enabled: false               # enable after its account and secrets are set up
    type: facts
    handle: "@CHANGE_ME"
    theme: science_facts
    slots:
      midday: { time: "12:20", format: reel }
      night:  { time: "21:10", format: image }
# news_digest pages (optional) also need: feeds: [allowlisted https hosts and URLs]
```

## 20. Owner steps (guide the owner through each one, click by click)

1. Switch Instagram to a **Professional** account (Creator or Business). Turn on two-factor authentication. (Repeat for each additional page.)
2. Create a **GitHub** account with two-factor authentication. Install GitHub CLI and run `gh auth login`. Install the **GitHub mobile app** and sign in.
3. Create a **Meta developer app** (Business type), add Instagram with **Instagram Login**, add each Instagram account as a tester and accept the invite, then generate a long-lived token for each page. Because the owner is the app's own user, App Review is not needed.
4. Create the fine-grained `GH_SECRETS_PAT` exactly as in section 12 (this repository only, Secrets read and write, 1-year expiry).
5. In their own terminal, run `scripts/set-token.sh <page>` for each page, and `gh secret set GH_SECRETS_PAT --env renewal`, typing each value at the hidden prompt. **Never paste a secret into this chat.**
6. Give Claude Code each page's Instagram handle.
7. Review the rehearsal samples, approve the look and voice, and optionally spot-check 20 facts.
8. Confirm the test alert email arrived.
9. Claude Code then does, with the owner: publish one real image and one real reel with narration per enabled page using `post-now`; run the token renewal once and confirm the next post still publishes; switch egress to block mode and publish again; and only then enable `autopilot.yml`.

## 21. `README.md` for the owner

Plain English. **Open with a cheat sheet of the few things the owner will ever need, most from a phone** (the GitHub mobile app can run these workflows directly):
1. **Post something now:** GitHub app → the repo → Actions → "Post now" → Run workflow → choose the page and type your text.
2. **Check everything is healthy:** open the Status page (one link at the top of the README).
3. **Pause or resume:** Actions → Autopilot → "…" → Disable or Enable workflow.
4. **Add more content:** when the "running low" email arrives, open Claude Code in this folder and type `Top up Science` (or the page name).
5. **Yearly:** renew the renewal key when the reminder email arrives (about 5 minutes, steps included).
6. **If a token renewal fails:** how to generate a fresh token and re-run `set-token.sh`.

Then cover: what each alert email title means and what to do; how to change posting times, the image/reel ratio and the voice; how to add a page; the fact that the repository, its logs and its alerts are public and contain nothing secret; and what to do if a "Post now" run shows as cancelled (run it again).