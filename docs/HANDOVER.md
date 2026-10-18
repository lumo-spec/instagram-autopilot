# Handover checklist (spec section 18)

Each line records what was actually run and its result. Nothing is marked passed unless it was
run. "Not run" items are waiting for a later stage.

## Security
| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | `scripts/repo_settings.py check` (rulesets, environment branch limits, read-only token, fork approval, secret scanning, push protection, SHA pinning) | PASS (2026-10-18) | 18/18 checks, see below |
| 2 | `scripts/audit_workflows.py` passes; actions SHA-pinned; `permissions: {}` everywhere; no schedules | PASS (local, 2026-10-18) | `workflow audit: passed`; 16 audit tests |
| 3 | Another branch cannot read an environment secret | not run (stage 3: needs a dummy secret) | |
| 4 | Renewal key only in `renewal`; only renew jobs use it | PASS by audit rule (secret allowed only in its environment's jobs) | `test_audit.py` |
| 5 | Logs of rehearsal and first live runs contain no tokens/IDs | not run (stage 4) | |
| 6 | Egress in block mode and first live runs pass under it | not run (audit mode now; docs/EGRESS.md) | |
| 7 | Feed and AI injection tests | n/a (no feed/AI step in this build) | |
| 8 | No secrets in git history | PASS (2026-10-18) | gitleaks full history (CI workflow_dispatch run 39167502589): 5 commits, no leaks; privacy check PASS |

## Reliability
| # | Check | Result | Evidence |
|---|---|---|---|
| 9 | State writes with `main` protected; two writers at once; per-page media folders | PASS (local git origin) | `test_concurrent_writers_both_land_without_force`, `test_push_media_puts_files_in_page_folder` |
| 10 | Idempotency/reconcile; crash after publish causes no double post | PASS (fake Instagram) | `test_crash_after_publish_is_reconciled_not_reposted`, `test_lost_record_step_does_not_double_post`, rehearsal |
| 11 | Keepalive re-enables schedules; missed slot caught up | catch-up PASS (rehearsal); keepalive API call unit-tested only — live test in stage 4 | |
| 12 | Alert path end to end (test alert received and closed) | issue #1 opened + locked by Maintenance run 39167833815; **waiting for the owner to confirm the e-mail**, then close it | |

## Quality
| # | Check | Result | Evidence |
|---|---|---|---|
| 13 | Lint, types, all tests, coverage ≥ 85 % | PASS (2026-10-18, Windows) | 248 tests, 90 % coverage; ruff, mypy strict clean |
| 14 | 14-day rehearsal | PASS (stage 1b) | see CLAUDE.md |
| 15 | Content, licences, near-duplicates, pronunciation | licences PASS (`check_licenses.py`: 117 packages, 3 reviewed exceptions) | |
| 16 | Status page renders for every page | PASS (unit test) | `test_status_page` |

## Live runs on GitHub (2026-10-18)
- CI on Linux (push + full-history dispatch): test, slow-tests, security, ci-ok all success.
- Maintenance `status`: record-maintenance wrote `STATUS.md` + heartbeat to the `state` branch
  under the rulesets (run 39167830046).
- Rehearsal (2 days) on GitHub: 1 published, 0 double posts, 0 reused (run 39167831880).

## Repository settings (`python scripts/repo_settings.py apply`, 2026-10-18)
All 18 PASS: public; wiki/projects off, issues on; secret scanning + push protection; Dependabot
alerts; read-only default token; Actions cannot approve PRs; SHA pinning required; fork PR
approval for all external contributors; rulesets main (PR + `ci-ok`, no force-push/deletion),
state (no force-push/deletion), media (no deletion); Pages from `media`; environments
`page-simplestorymode` and `renewal` limited to `main`; branches `state` and `media` exist.
Environment secrets: none set yet.
