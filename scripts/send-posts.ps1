# Send new or changed posts (post.yaml, art, approved.json) to GitHub in one go.
# Run this in YOUR OWN PowerShell window, once per batch of art:
#
#     cd D:\Projects\instagram-autopilot
#     powershell -ExecutionPolicy Bypass -File .\scripts\send-posts.ps1
#
# What it does, in order (it stops at the first problem; nothing half-done reaches main):
#   1. Makes sure you are on main and up to date with GitHub (and finishes an earlier batch).
#   2. Runs the privacy check (nothing from references/ may ever be committed).
#   3. Checks every queued post (python -m autopilot validate).
#   4. Collects ONLY files inside pages/<page>/queue/<post-id>/ named post.yaml,
#      approved.json or art-NN.png/.jpg/.jpeg/.webp. Anything else is listed and left alone.
#   5. Puts them on a new branch, opens a pull request, asks GitHub to merge it by itself once
#      the required checks pass, waits for that (about 5-15 minutes), then returns you to an
#      up-to-date main. If you close the window early, just run the script again later.
# It never force-pushes, never touches references/ and never asks for a password or token.
# Add -DryRun to see what would be sent without sending anything.
param([switch]$DryRun)
$ErrorActionPreference = "Stop"
$repo = "lumo-spec/instagram-autopilot"
$py = ".\.venv\Scripts\python.exe"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Step($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }

function Finish-Batch($name) {
    # Wait until GitHub has merged the batch, then go back to an up-to-date main.
    for ($i = 0; $i -lt 40; $i++) {
        $state = gh pr view $name --repo $repo --json state --jq .state
        if ($state -eq "MERGED") {
            git switch --quiet main
            git pull --quiet --ff-only origin main
            if ($LASTEXITCODE -ne 0) { Fail "main could not be updated. Ask Claude Code to look at it." }
            git branch --quiet -D $name
            return $true
        }
        if ($state -ne "OPEN") { Fail "the pull request for $name is $state. Ask Claude Code to look at it." }
        if ($i -eq 0) { Write-Host "Waiting for GitHub's checks and merge (about 5-15 minutes)..." }
        Start-Sleep -Seconds 30
    }
    return $false
}

Step "1/5 Checking you are on main and up to date..."
$branch = (git branch --show-current).Trim()
if ($branch -like "posts-*") {
    Write-Host "An earlier batch ($branch) is still on its way - finishing it first."
    if (-not (Finish-Batch $branch)) { Fail "the earlier batch is not merged yet. Open it on GitHub: gh pr view $branch --web" }
    $branch = "main"
}
if ($branch -ne "main") { Fail "you are on branch '$branch'. Ask Claude Code to switch back to main first." }
git fetch --quiet origin
if ($LASTEXITCODE -ne 0) { Fail "could not reach GitHub (check your internet connection)." }
git merge --ff-only --quiet origin/main
if ($LASTEXITCODE -ne 0) { Fail "main could not be updated cleanly. Ask Claude Code to look at it." }

Step "2/5 Privacy check..."
& $py scripts\privacy_check.py
if ($LASTEXITCODE -ne 0) { Fail "the privacy check failed. Nothing was sent." }

Step "3/5 Checking every queued post..."
& $py -m autopilot validate
if ($LASTEXITCODE -ne 0) { Fail "a post has a problem (listed above). Nothing was sent." }

Step "4/5 Collecting new and changed post files..."
$allowed = '^pages/[a-z0-9-]+/queue/[a-z0-9-]+/(post\.yaml|approved\.json|art-\d{2}\.(png|jpg|jpeg|webp))$'
$changed = git -c core.quotepath=off status --porcelain=v1 --untracked-files=all -- pages
$send = @(); $skipped = @()
foreach ($line in $changed) {
    $path = $line.Substring(3).Trim('"')
    if ($line.Substring(0, 2).Contains("D")) { $skipped += "$path (deleted - ask Claude Code)"; continue }
    if ($path -match $allowed) { $send += $path } else { $skipped += $path }
}
foreach ($s in $skipped) { Write-Host "  not sent: $s" -ForegroundColor Yellow }
if ($send.Count -eq 0) { Write-Host "Nothing new to send." -ForegroundColor Green; exit 0 }
$big = $send | Where-Object { (Get-Item $_).Length -gt 15MB }
if ($big) { Fail "these files are over 15 MB, please save them smaller: $($big -join ', ')" }
$posts = ($send | ForEach-Object { ($_ -split '/')[3] } | Sort-Object -Unique) -join ", "
foreach ($p in $send) { Write-Host "  sending: $p" }
if ($DryRun) { Write-Host ""; Write-Host "Dry run: nothing was sent." -ForegroundColor Green; exit 0 }

Step "5/5 Sending to GitHub..."
$new = "posts-" + (Get-Date -Format "yyyyMMdd-HHmmss")
git switch --quiet -c $new
git add -- $send
git commit --quiet -m "Add/update posts: $posts"
if ($LASTEXITCODE -ne 0) { Fail "the commit failed. Your files are unchanged on branch $new." }
git push --quiet -u origin $new
if ($LASTEXITCODE -ne 0) { Fail "the upload failed. Run the script again later; it will retry." }
$url = gh pr create --repo $repo --base main --head $new --title "Posts: $posts" `
    --body "Art and post files for: $posts (sent with scripts/send-posts.ps1)."
if ($LASTEXITCODE -ne 0) { Fail "could not open the pull request. Ask Claude Code to finish it." }
$allowAuto = gh repo view $repo --json autoMergeAllowed --jq .autoMergeAllowed
if ($allowAuto -ne "true") {
    Write-Host ""
    Write-Host "GitHub's 'auto-merge' option is off for this repository." -ForegroundColor Yellow
    $answer = Read-Host "Turn it on so batches merge by themselves once the checks pass? (y/n)"
    if ($answer -eq "y") { gh repo edit $repo --enable-auto-merge | Out-Null }
}
gh pr merge $url --auto --squash --delete-branch | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Sent: $posts" -ForegroundColor Green
    Write-Host "Open $url and click 'Squash and merge' when the checks are green,"
    Write-Host "then run this script again (it will bring your computer up to date)."
    exit 0
}
if (Finish-Batch $new) {
    Write-Host ""
    Write-Host "Done: $posts are on GitHub and your computer is up to date." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Sent: $posts - GitHub is still checking. $url" -ForegroundColor Yellow
    Write-Host "You can close this window. Run the script again later to finish."
}
