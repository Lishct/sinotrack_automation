<#
Daily SinoTrack pipeline runner.
 
Runs, in order:
  1. sinotrack_scraper.py  -- captures yesterday's data (headless)
  2. parse_report.py       -- builds the report + updates history
  3. generate_dashboard.py -- rebuilds dashboard.html
 
Stops at the first failure (a script exiting non-zero) so a broken step
doesn't silently produce a stale or empty dashboard. Everything is logged
to logs\YYYY-MM-DD_run.log so a failure can be diagnosed after the fact
without needing to be watching when it runs.
 
Intended to be called by Windows Task Scheduler -- see the setup notes
at the bottom of this file for how to register it.
#>
 
$ErrorActionPreference = "Stop"
 
# --- Config: adjust these two paths for your machine ---
$ProjectDir = $PSScriptRoot                     # folder this script lives in
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
# If you're not using a venv, point this at your python.exe instead, e.g.:
# $VenvPython = "python"
 
# Where the finished dashboard gets copied so your handler can just open a
# link -- point this at a OneDrive/SharePoint-synced folder they already
# have access to. $env:OneDrive is set automatically by the OneDrive
# client when it's installed and signed in; adjust the subfolder name to
# whatever makes sense. Leave blank ("") to skip delivery entirely and
# only keep the report in sinotrack_data\ locally.
$DeliveryDir = Join-Path $env:OneDrive "SinoTrack Reports"
 
Set-Location $ProjectDir
 
$LogDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "$(Get-Date -Format 'yyyy-MM-dd')_run.log"
 
function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}
 
function Run-Step {
    param([string]$Name, [string[]]$ScriptArgs, [int]$TimeoutSeconds = 900)
 
    # 900s (15 min) default ceiling per step. A hang used to block forever --
    # Task Scheduler would then report "task is currently running" on the
    # NEXT trigger, because the previous one never actually finished. This
    # kills and logs a hang explicitly instead of leaving a zombie process.
 
    Write-Log "START  $Name"
 
    $stdoutFile = Join-Path $LogDir "$Name.stdout.tmp"
    $stderrFile = Join-Path $LogDir "$Name.stderr.tmp"
 
    # Windows PowerShell's Start-Process -ArgumentList just joins the array
    # with spaces -- it does NOT auto-quote elements that contain spaces
    # (unlike .NET's ArgumentList). Quoting any argument with a space here
    # avoids a real path (e.g. a folder name with a space in it) getting
    # silently split into two arguments.
    $quotedArgs = ($ScriptArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
 
    try {
        $proc = Start-Process -FilePath $VenvPython -ArgumentList $quotedArgs -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
        # Touching .Handle forces the process object to retain the access
        # rights needed to read .ExitCode later. Skip this and .ExitCode
        # comes back $null even after the process has exited -- and
        # "$null -ne 0" is $true in PowerShell, which is exactly what
        # made a SUCCESSFUL run get logged as FAILED (exit code ) just now.
        $proc.Handle | Out-Null
    } catch {
        # Covers e.g. $VenvPython pointing at a path that doesn't exist --
        # previously this threw a terminating error that skipped logging
        # entirely, which is exactly why past failures showed up as a
        # silent gap in the log instead of an explanation.
        Write-Log "FAILED $Name -- could not launch process: $_"
        exit 1
    }
 
    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)
 
    if (-not $finished) {
        Write-Log "FAILED $Name -- timed out after $TimeoutSeconds sec, killing it"
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
        Get-Content $stdoutFile -ErrorAction SilentlyContinue | Add-Content -Path $LogFile
        Get-Content $stderrFile -ErrorAction SilentlyContinue | Add-Content -Path $LogFile
        Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
        exit 1
    }
 
    $proc.Refresh()  # make sure ExitCode reflects the actual finished state, not a cached value
 
    Get-Content $stdoutFile -ErrorAction SilentlyContinue | Add-Content -Path $LogFile
    Get-Content $stderrFile -ErrorAction SilentlyContinue | Add-Content -Path $LogFile
    Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
 
    if ($proc.ExitCode -ne 0) {
        Write-Log "FAILED $Name (exit code $($proc.ExitCode)) -- stopping pipeline"
        exit 1
    }
    Write-Log "OK     $Name"
}
 
Write-Log "===== Daily SinoTrack pipeline starting ====="
 
# The scraper always pulls "yesterday" relative to when it runs -- compute
# the same date here so we know which file to hand to the parser next.
$TargetDate = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
$RawFile = Join-Path $ProjectDir "sinotrack_data\${TargetDate}_raw_responses.json"
 
Run-Step "scraper"   @("sinotrack_scraper.py")
 
if (-not (Test-Path $RawFile)) {
    Write-Log "FAILED expected capture file not found: $RawFile"
    exit 1
}
 
Run-Step "parser"    @("parse_report.py", $RawFile)
Run-Step "dashboard" @("generate_dashboard.py")
 
$DashboardFile = Join-Path $ProjectDir "sinotrack_data\dashboard.html"
 
if ($DeliveryDir) {
    try {
        New-Item -ItemType Directory -Force -Path $DeliveryDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $DeliveryDir "archive") | Out-Null
 
        # Stable filename -- this is the one link your handler bookmarks;
        # it's overwritten every run so it's always today's report.
        Copy-Item -Path $DashboardFile -Destination (Join-Path $DeliveryDir "Fleet Manifest - Latest.html") -Force
 
        # Dated copy -- so a specific past day can still be checked later
        # even after several more runs have overwritten the "latest" one.
        Copy-Item -Path $DashboardFile -Destination (Join-Path $DeliveryDir "archive\dashboard_$TargetDate.html") -Force
 
        Write-Log "OK     delivery (copied to $DeliveryDir)"
    } catch {
        # Delivery failing doesn't mean the report itself failed -- log it
        # as a warning rather than treating it as a pipeline failure, so a
        # OneDrive hiccup doesn't get confused with the scraper breaking.
        Write-Log "WARN   delivery copy failed: $_"
    }
} else {
    Write-Log "SKIP   delivery (DeliveryDir not set)"
}
 
Write-Log "===== Daily SinoTrack pipeline finished OK ====="
exit 0
 
<#
--- Task Scheduler setup (one-time) ---
 
1. Open Task Scheduler -> Create Task (not "Basic Task", so you get the
   full options below).
 
2. General tab:
   - Name: SinoTrack Daily Report
   - Run whether user is logged on or not
   - (optional) Run with highest privileges -- not usually needed here
 
3. Triggers tab -> New:
   - Daily, start time e.g. 06:00 -- run after midnight so "yesterday"
     is a complete day, and with enough buffer that the portal's own
     data for the day has settled.
 
4. Actions tab -> New:
   - Action: Start a program
   - Program/script:  powershell.exe
   - Add arguments:   -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\sinotrack_automation\run_daily.ps1"
   - Start in:        C:\path\to\sinotrack_automation
 
5. Conditions tab:
   - Uncheck "Start the task only if the computer is on AC power" if
     this runs on a laptop that might be on battery.
   - Check "Wake the computer to run this task" if the machine sleeps.
 
6. Settings tab:
   - Check "Run task as soon as possible after a scheduled start is
     missed" (covers the machine being off at 6am).
   - "If the task fails, restart every" -- e.g. 15 minutes, up to 2
     attempts, in case of a transient portal/network hiccup.
 
7. Save, then right-click the task -> Run, once, to confirm it works
   end-to-end unattended (headless=True means no browser window will
   appear -- check logs\ for that day to confirm success).
 
Every run's log is in logs\YYYY-MM-DD_run.log regardless of whether it
succeeded or failed, so that's the first place to check.
 
--- Delivery to your handler ---
 
Once this is deployed, send your handler ONE link, once:
  $DeliveryDir\Fleet Manifest - Latest.html
(the actual OneDrive/SharePoint sharing link to that file). It's
overwritten every morning, so that one link is always the current
report -- they never need anything sent to them again. Dated copies
pile up in the "archive" subfolder if a specific past day ever needs
to be checked.
 
If $env:OneDrive isn't set on this machine (OneDrive not installed or
not signed in), $DeliveryDir will resolve incorrectly -- hardcode a
full path instead, e.g.:
  $DeliveryDir = "C:\Users\YourName\OneDrive\SinoTrack Reports"
#>