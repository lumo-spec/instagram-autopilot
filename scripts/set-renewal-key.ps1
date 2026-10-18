# Store the fine-grained GitHub token used for weekly Instagram token renewal (GH_SECRETS_PAT).
# Run this in YOUR OWN PowerShell window:
#
#     cd D:\Projects\instagram-autopilot
#     .\scripts\set-renewal-key.ps1
#
# GitHub CLI asks for the value at a hidden prompt. Never paste the token into a chat or a file.
$ErrorActionPreference = "Stop"
$repo = "lumo-spec/instagram-autopilot"

Write-Host ""
Write-Host "Paste the fine-grained token (starts with github_pat_), then press Enter." -ForegroundColor Cyan
Write-Host "(The prompt shows nothing while you paste - that is expected.)"
gh secret set GH_SECRETS_PAT --env renewal --repo $repo
if ($LASTEXITCODE -ne 0) { throw "Could not set GH_SECRETS_PAT." }
Write-Host ""
Write-Host "Done. The renewal key is stored encrypted in the 'renewal' environment." -ForegroundColor Green
