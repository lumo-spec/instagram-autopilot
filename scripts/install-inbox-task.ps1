<#
.SYNOPSIS
  Starts (or stops) automatic posting of a video page from this PC.

.DESCRIPTION
  Registers a Windows scheduled task that runs
      .venv\Scripts\python.exe -m autopilot inbox run <page>
  every 10 minutes for the signed-in user. The program itself decides which slot is due
  (UK time), so the task only has to run often. The task wakes the PC from sleep and catches
  up when the PC was off at a slot (within 90 minutes).

  Run it ONLY after the first live test worked and you said "continue".
  Stop everything at once: create PAUSE.txt in the page's folder, or run this with -Remove.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-inbox-task.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File .\scripts\install-inbox-task.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\install-inbox-task.ps1 -Remove
#>
param(
    [string]$Page = "wellitstarted",
    [int]$EveryMinutes = 10,
    [switch]$Remove,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project ".venv\Scripts\python.exe"
$name = "Autopilot inbox $Page"

if ($Remove) {
    if ($DryRun) { Write-Host "Would remove the task '$name'."; exit 0 }
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Automatic posting for $Page is OFF (task '$name' removed)."
    exit 0
}

if (-not (Test-Path $python)) { throw "Python not found at $python. Run 'uv sync' first." }

# One check before switching on: the folder, keys and queue must be readable.
& $python -m autopilot inbox status $Page
if ($LASTEXITCODE -ne 0) { throw "The status check failed; fix the message above first." }

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m autopilot inbox run $Page" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

if ($DryRun) {
    Write-Host "Would register '$name': every $EveryMinutes minutes, wake the PC, run:"
    Write-Host "  $python -m autopilot inbox run $Page   (in $project)"
    exit 0
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Posts $Page videos from its postthem folder on schedule." `
    -Force | Out-Null
Write-Host "Automatic posting for $Page is ON (task '$name', every $EveryMinutes minutes)."
Write-Host "Log: the page folder's .autopilot\log.txt. Stop: create PAUSE.txt there, or -Remove."
