# Store the image-generation API key (Google Gemini by default). Run this in YOUR OWN
# PowerShell window:
#
#     cd D:\Projects\instagram-autopilot
#     powershell -ExecutionPolicy Bypass -File .\scripts\set-image-key.ps1
#
# You paste the key at a hidden prompt (nothing is shown). The script then:
#   1. saves it in the file .env in this folder (git-ignored: it is never committed or sent),
#   2. stores it as the encrypted GitHub secret IMAGE_API_KEY in the "art-generation"
#      environment (only workflows on main can use it),
#   3. runs a free check that the key works and the model names in config.yaml exist.
# Never paste the key into a chat, an issue or any other file.
# -Name CHECK_API_KEY stores a second key, only needed when config.yaml's art_generation.checker
# uses a different provider than art_generation.provider. -LocalOnly skips GitHub.
param(
    [ValidateSet("IMAGE_API_KEY", "CHECK_API_KEY")][string]$Name = "IMAGE_API_KEY",
    [switch]$LocalOnly
)
$ErrorActionPreference = "Stop"
$repo = "lumo-spec/instagram-autopilot"
$envName = "art-generation"
Set-Location (Split-Path -Parent $PSScriptRoot)

git check-ignore -q .env
if ($LASTEXITCODE -ne 0) { throw ".env is not git-ignored here; stopping so the key cannot be committed." }

Write-Host ""
Write-Host "Paste the API key, then press Enter." -ForegroundColor Cyan
Write-Host "(Nothing is shown while you paste - that is expected.)"
$secure = Read-Host -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
if ($key.Length -lt 20 -or $key -match '\s') { throw "That does not look like an API key. Nothing was saved." }

# 1. Local .env (keep any other lines, replace this key's line)
$path = Join-Path (Get-Location) ".env"
$lines = @()
if (Test-Path $path) { $lines = @(Get-Content $path | Where-Object { $_ -notmatch "^\s*$Name\s*=" }) }
$lines += "$Name=$key"
[System.IO.File]::WriteAllLines($path, [string[]]$lines)
Write-Host "Saved in .env (git-ignored)." -ForegroundColor Green

# 2. GitHub Environment secret
if (-not $LocalOnly) {
    gh api --method PUT "repos/$repo/environments/$envName" -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not create the GitHub environment $envName." }
    $policies = gh api "repos/$repo/environments/$envName/deployment-branch-policies" --jq '.branch_policies[].name'
    if (-not ($policies -contains "main")) {
        gh api --method POST "repos/$repo/environments/$envName/deployment-branch-policies" -f name=main -f type=branch | Out-Null
    }
    $key | gh secret set $Name --env $envName --repo $repo
    if ($LASTEXITCODE -ne 0) { throw "Could not store $Name in GitHub." }
    Write-Host "Stored encrypted in GitHub (environment $envName)." -ForegroundColor Green
}
$key = $null

# 3. Free check
Write-Host ""
Write-Host "Checking the key (free)..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m autopilot art-key-check
if ($LASTEXITCODE -ne 0) { Write-Host "The check failed - tell Claude Code what it printed (never the key)." -ForegroundColor Yellow; exit 1 }
Write-Host ""
Write-Host "Done. Close this window." -ForegroundColor Green
