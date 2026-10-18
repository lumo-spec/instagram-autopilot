# Handover checklist (spec section 18)

Each line records what was actually run and its result. Nothing is marked passed unless it was
run. "Not run" items are waiting for a later stage.

## Security
| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | `scripts/repo_settings.py check` (rulesets, environment branch limits, read-only token, fork approval, secret scanning, push protection, SHA pinning) | see below | |
| 2 | `scripts/audit_workflows.py` passes; actions SHA-pinned; `permissions: {}` everywhere; no schedules | PASS (local, 2026-10-18) | `workflow audit: passed`; 16 audit tests |
| 3 | Another branch cannot read an environment secret | not run (stage 3: needs a dummy secret) | |
| 4 | Renewal key only in `renewal`; only renew jobs use it | PASS by audit rule (secret allowed only in its environment's jobs) | `test_audit.py` |
| 5 | Logs of rehearsal and first live runs contain no tokens/IDs | not run (stage 4) | |
| 6 | Egress in block mode and first live runs pass under it | not run (audit mode now; docs/EGRESS.md) | |
| 7 | Feed and AI injection tests | n/a (no feed/AI step in this build) | |
| 8 | No secrets in git history | local: privacy check PASS; gitleaks runs in CI | |

## Reliability
| # | Check | Result | Evidence |
|---|---|---|---|
| 9 | State writes with `main` protected; two writers at once; per-page media folders | PASS (local git origin) | `test_concurrent_writers_both_land_without_force`, `test_push_media_puts_files_in_page_folder` |
| 10 | Idempotency/reconcile; crash after publish causes no double post | PASS (fake Instagram) | `test_crash_after_publish_is_reconciled_not_reposted`, `test_lost_record_step_does_not_double_post`, rehearsal |
| 11 | Keepalive re-enables schedules; missed slot caught up | catch-up PASS (rehearsal); keepalive API call unit-tested only — live test in stage 4 | |
| 12 | Alert path end to end (test alert received and closed) | not run (needs the owner to confirm the e-mail) | |

## Quality
| # | Check | Result | Evidence |
|---|---|---|---|
| 13 | Lint, types, all tests, coverage ≥ 85 % | PASS (2026-10-18, Windows) | 248 tests, 90 % coverage; ruff, mypy strict clean |
| 14 | 14-day rehearsal | PASS (stage 1b) | see CLAUDE.md |
| 15 | Content, licences, near-duplicates, pronunciation | licences PASS (`check_licenses.py`: 117 packages, 3 reviewed exceptions) | |
| 16 | Status page renders for every page | PASS (unit test) | `test_status_page` |

## Repository settings (`python scripts/repo_settings.py check`)
Recorded after `apply` — see the section below once run.
