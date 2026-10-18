# Security

This repository is **public**. It never contains secrets, and nothing it writes (logs, job
summaries, artifacts, issues, commits, branches) may contain secrets, tokens, Instagram user IDs,
raw API responses or stack traces from secret-holding jobs.

## Design (summary of spec §3 and §16)
- **Trust zones.** Jobs that hold a secret run only small, audited code with a minimal, hashed
  dependency set (`publish` uses the Python standard library plus PyYAML). Jobs that run large
  dependency trees (rendering, TTS) or handle untrusted text hold no secrets.
- **Secrets only in GitHub Environments** (`page-simplestorymode`, `renewal`, optional `ai-summary`), each
  restricted to the `main` branch, so forks, pull requests, Dependabot and other branches cannot
  read them.
- **Token renewal** uses a sealed hand-off: the job holding the renewal key never sees an
  Instagram token, and the job holding the Instagram token never sees the renewal key.
- **Workflows**: `permissions: {}` at the top, every action pinned to a full commit SHA,
  `step-security/harden-runner` with an egress allow-list per job, no `pull_request_target`,
  inputs passed to scripts only through environment variables.
- **Alerts** are public issues with generic wording; a public-safety check rejects anything that
  looks like a token, an ID or an error dump.

## Privacy
- The owner's reference images (which include a photo of the owner) live only in the local,
  git-ignored `references/` folder and are never committed or uploaded.
- Queued posts (text and art) are public in this repository before they are published.
- Posts only ever come from owner-approved queue items; an approval records the SHA-256 of every
  input file, so text or art changed after approval is never published.

## Additional controls (stage 2)
- **Jobs and powers**: `build` renders with the big dependency tree but holds no secret and only
  a read-only token; `host` (write token, minimal hashed deps) puts media on the `media` branch;
  `publish` (Instagram token, standard library + PyYAML only) has a read-only token; `record`
  (write token for state/media + issues) holds no secret.
- **Safety switch**: the publish job runs only when the repository variable
  `PUBLISHING_ENABLED` is `true`.
- **Schedules OFF**: `scripts/audit_workflows.py` fails CI if any workflow has a schedule while
  `autopilot.schedule_enabled` is false.
- **Privacy**: `scripts/privacy_check.py` (CI and before every push) proves no reference file is
  tracked, staged or in history — locally also by comparing file contents.
- **Repository settings** are applied and verified with `scripts/repo_settings.py`.
- **Licences**: `scripts/check_licenses.py` in CI; GPL/LGPL build-time tools are listed with reasons.

## Status
Stage 2 (workflows, hosting, alerts, status page, token renewal) is built and tested locally
against fakes; results and what is still unverified live are in `docs/HANDOVER.md`.

## Reporting a problem
Please open a GitHub security advisory (Security tab → "Report a vulnerability") rather than a
public issue.
