# Set a page's Instagram secrets. Run this in YOUR OWN PowerShell window:
#
#     cd D:\Projects\instagram-autopilot
#     .\scripts\set-token.ps1                # page simplestorymode
#
# GitHub CLI asks for each value at a hidden prompt (nothing is shown or saved on this PC).
# Never paste a token into a chat, an issue or a file.
param([string]$Page = "simplestorymode")
$ErrorActionPreference = "Stop"
$repo = "lumo-spec/instagram-autopilot"
if ($Page -notmatch '^[a-z][a-z0-9_]{1,30}$') { throw "Invalid page name." }
$envName = "page-$Page"

Write-Host ""
Write-Host "Setting the Instagram secrets for '$Page' (GitHub environment $envName)." -ForegroundColor Cyan
Write-Host ""
Write-Host "1 of 2: paste the long-lived Instagram ACCESS TOKEN, then press Enter."
Write-Host "        (The prompt shows nothing while you paste - that is expected.)"
gh secret set IG_ACCESS_TOKEN --env $envName --repo $repo
if ($LASTEXITCODE -ne 0) { throw "Could not set IG_ACCESS_TOKEN." }

Write-Host ""
Write-Host "2 of 2: paste the Instagram USER ID (digits only), then press Enter."
gh secret set IG_USER_ID --env $envName --repo $repo
if ($LASTEXITCODE -ne 0) { throw "Could not set IG_USER_ID." }

Write-Host ""
Write-Host "Recording today's date as the token's start date (no secret is sent)..."
gh workflow run maintenance.yml --repo $repo -f task=token-set -f page=$Page
if ($LASTEXITCODE -ne 0) { Write-Host "Could not start the Maintenance run; tell Claude Code." -ForegroundColor Yellow }

Write-Host ""
Write-Host "Done. Both secrets are stored encrypted in GitHub. Close this window." -ForegroundColor Green
