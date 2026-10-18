# Security

This repository is **public**. It never contains secrets, and nothing it writes (logs, job
summaries, artifacts, issues, commits, branches) may contain secrets, tokens, Instagram user IDs,
raw API responses or stack traces from secret-holding jobs.

## Design (summary of spec §3 and §16)
- **Trust zones.** Jobs that hold a secret run only small, audited code with a minimal, hashed
  dependency set (`publish` uses the Python standard library plus PyYAML). Jobs that run large
  dependency trees (rendering, TTS) or handle untrusted text hold no secrets.
- **Secrets only in GitHub Environments** (`page-wisdom`, `renewal`, optional `ai-summary`), each
  restricted to the `main` branch, so forks, pull requests, Dependabot and other branches cannot
  read them.
- **Token renewal** uses a sealed hand-off: the job holding the renewal key never sees an
  Instagram token, and the job holding the Instagram token never sees the renewal key.
- **Workflows**: `permissions: {}` at the top, every action pinned to a full commit SHA,
  `step-security/harden-runner` with an egress allow-list per job, no `pull_request_target`,
  inputs passed to scripts only through environment variables.
- **Alerts** are public issues with generic wording; a public-safety check rejects anything that
  looks like a token, an ID or an error dump.

## Status
Stage 1 (local rehearsal) is complete. The GitHub Actions security controls above are built and
verified in stage 2; their verification results will be recorded in `docs/HANDOVER.md`.

## Reporting a problem
Please open a GitHub security advisory (Security tab → "Report a vulnerability") rather than a
public issue.
