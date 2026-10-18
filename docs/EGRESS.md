# Outbound connections per job (harden-runner allow-list)

Every job starts with `step-security/harden-runner`. It runs in **audit** mode for the first
real runs, so the connections each job makes can be observed in StepSecurity's run insights
(linked from each run). After those runs are reviewed, every job switches to
`egress-policy: block` with the `allowed-endpoints` below, **before the schedule is enabled**.
A compromised dependency then cannot send data anywhere else.

Status: **audit mode** (no real run observed yet). The lists below are the expected endpoints
and must be confirmed against the audit results.

| Job (workflow) | Secrets | Expected endpoints (all :443) |
|---|---|---|
| `test`, `slow-tests` (ci), `build` (_pipeline), `rehearse` (rehearsal) | none | `github.com`, `api.github.com`, `codeload.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` (uv, Python, spaCy model), `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org`, `huggingface.co`, `cdn-lfs.hf.co`, `cas-bridge.xethub.hf.co` (voice model, cache miss only), Actions cache/artifact hosts* |
| `security` (ci) | none | `github.com`, `api.github.com`, `objects.githubusercontent.com` (Python, gitleaks), `pypi.org`, `files.pythonhosted.org` |
| `plan` (autopilot), `host`, `record` (_pipeline), maintenance `record-maintenance`, `keepalive`, `cleanup`, `alert-test` | none | `github.com`, `api.github.com`, `objects.githubusercontent.com`, `pypi.org`, `files.pythonhosted.org`, `raw.githubusercontent.com` and `lumo-spec.github.io` (host: URL checks), artifact hosts* |
| `publish` (_pipeline) | `IG_ACCESS_TOKEN`, `IG_USER_ID` | `graph.instagram.com`, `raw.githubusercontent.com`, `lumo-spec.github.io` (re-check media URLs), `github.com` (checkout), `objects.githubusercontent.com`, `pypi.org`, `files.pythonhosted.org`, artifact hosts* |
| `health`, `renew-refresh` (maintenance) | `IG_ACCESS_TOKEN` (+ `IG_USER_ID` for health) | `graph.instagram.com`, `github.com`, `objects.githubusercontent.com`, `pypi.org`, `files.pythonhosted.org`, artifact hosts* |
| `renew-keys`, `renew-store` (maintenance) | `GH_SECRETS_PAT` | `api.github.com`, `github.com`, `objects.githubusercontent.com`, `pypi.org`, `files.pythonhosted.org`, artifact hosts* |

\* Artifact and cache traffic goes to GitHub-run hosts such as
`results-receiver.actions.githubusercontent.com` and `*.blob.core.windows.net`
(`productionresultssa*.blob.core.windows.net`); the exact names come from the audit results.

Notes
- Instagram fetches the media itself from `raw.githubusercontent.com` (images, carousel slides)
  and `lumo-spec.github.io` (reels); no job uploads media to Meta directly.
- The rehearsal additionally blocks Instagram/Meta hosts inside Python (`no_instagram_network`).
