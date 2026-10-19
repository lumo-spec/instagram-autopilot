# Set a page's Instagram secrets. Run this in YOUR OWN PowerShell window:
#
#     cd D:\Projects\instagram-autopilot
#     .\scripts\set-token.ps1                # page simplestorymode
#
# You paste the long-lived Instagram token ONCE at a hidden prompt. The script asks Instagram
# (over HTTPS, from this PC only) which account the token belongs to, shows you the username
# (never the token or the numeric ID), and after you confirm stores both secrets encrypted in
# GitHub. Nothing is written to disk or shown on screen. Never paste a token into a chat,
# an issue or a file.
param([string]$Page = "simplestorymode", [string]$Handle = "simplestorymode")
$ErrorActionPreference = "Stop"
$repo = "lumo-spec/instagram-autopilot"
if ($Page -notmatch '^[a-z][a-z0-9_]{1,30}$') { throw "Invalid page name." }
$envName = "page-$Page"
$apiVersion = "v26.0"   # same as graph_api_version in config.yaml

Write-Host ""
Write-Host "Setting the Instagram secrets for '$Page' (GitHub environment $envName)." -ForegroundColor Cyan
Write-Host ""
Write-Host "Paste the long-lived Instagram ACCESS TOKEN, then press Enter."
Write-Host "(The prompt shows only stars while you paste - that is expected.)"
$secure = Read-Host -AsSecureString "Token"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ($token.Length -lt 50 -or $token -match '\s') { throw "That does not look like a token (too short or has spaces). Nothing was stored." }

Write-Host ""
Write-Host "Asking Instagram which account this token belongs to..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try {
    $me = Invoke-RestMethod -Method Get `
        -Uri "https://graph.instagram.com/$apiVersion/me?fields=user_id,username" `
        -Headers @{ Authorization = "Bearer $token" }
} catch {
    $token = $null
    throw "Instagram did not accept this token (expired, copied incompletely, or missing permissions). Nothing was stored."
}
$userId = [string]$me.user_id
if ($userId -notmatch '^\d{5,25}$') { $token = $null; throw "Instagram did not return an account ID. Nothing was stored." }

Write-Host ""
Write-Host "This token belongs to: @$($me.username)" -ForegroundColor Cyan
if ($me.username -ne $Handle) {
    Write-Host "WARNING: expected @$Handle." -ForegroundColor Yellow
}
$answer = Read-Host "Store it for '$Page'? Type yes to continue"
if ($answer -ne "yes") { $token = $null; Write-Host "Stopped. Nothing was stored."; exit 1 }

$token | gh secret set IG_ACCESS_TOKEN --env $envName --repo $repo
if ($LASTEXITCODE -ne 0) { $token = $null; throw "Could not set IG_ACCESS_TOKEN." }
$userId | gh secret set IG_USER_ID --env $envName --repo $repo
if ($LASTEXITCODE -ne 0) { $token = $null; throw "Could not set IG_USER_ID." }
$token = $null
$userId = $null

Write-Host ""
Write-Host "Recording today's date as the token's start date (no secret is sent)..."
gh workflow run maintenance.yml --repo $repo -f task=token-set -f page=$Page
if ($LASTEXITCODE -ne 0) { Write-Host "Could not start the Maintenance run; tell Claude Code." -ForegroundColor Yellow }

Write-Host ""
Write-Host "Done. Both secrets are stored encrypted in GitHub. Close this window." -ForegroundColor Green
