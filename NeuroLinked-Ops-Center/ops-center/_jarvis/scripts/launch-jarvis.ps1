# Jarvis â€” Minimal Launcher
#
# 1. If the Jarvis server isn't already running on port 8340, start it.
# 2. Open Chrome to http://localhost:8340 so the boot sequence runs.
#
# This is the script clap-trigger.py calls when it detects a double clap.

$ErrorActionPreference = "SilentlyContinue"

# Resolve the jarvis folder (one level up from /scripts)
$jarvisDir = Split-Path -Parent $PSScriptRoot
$pythonExe = "python.exe"
if (-not (Test-Path $pythonExe)) { $pythonExe = "python" }

# Check if port 8340 already has a listener
$listening = Get-NetTCPConnection -LocalPort 8340 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Write-Host "[jarvis-launch] Starting server..."
    Start-Process -FilePath $pythonExe `
                  -ArgumentList "server.py" `
                  -WorkingDirectory $jarvisDir `
                  -WindowStyle Hidden

    # Wait up to 12 seconds for the server to start listening
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Milliseconds 500
        $listening = Get-NetTCPConnection -LocalPort 8340 -State Listen -ErrorAction SilentlyContinue
        if ($listening) { break }
    }
} else {
    Write-Host "[jarvis-launch] Server already running."
}

# Open Chrome to the Jarvis URL â€” the boot sequence runs in the browser
$chromePaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList "--new-window", "http://localhost:8340"
} else {
    # Fall back to default browser
    Start-Process "http://localhost:8340"
}
Write-Host "[jarvis-launch] Jarvis opening at http://localhost:8340"
