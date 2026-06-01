# =============================================================================
# NeuroLinked Ops Center - distribution build (Windows)
# =============================================================================
# Produces a SHIPPABLE zip with:
#   - Cross-platform launchers (start.bat, start.sh, install.ps1, install.sh)
#   - All source code
#   - SANITIZED config.json (no API keys)
#   - EMPTY state.json + agents.json
#   - NO brain_state (will be created fresh on first run)
#   - NO __pycache__ / backups / logs / Playwright browsers / Ollama models
#
# Output:  D:\NeuroLinked-Distribution.zip   (configurable below)
# =============================================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Staging  = "D:\NeuroLinked-Distribution-Staging\NeuroLinked-Ops-Center"
$ZipPath  = "D:\NeuroLinked-Distribution.zip"

function Step($t) { Write-Host ""; Write-Host "==== $t ====" -ForegroundColor Cyan }

Step "1. Stage clean copy on D:\"
$root = Split-Path $Staging -Parent
if (Test-Path $root) { Remove-Item $root -Recurse -Force }
New-Item -ItemType Directory -Path $Staging -Force | Out-Null

# Use robocopy to copy the project tree, excluding bloat + personal state
$exclDirs = @(
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "logs",
    "playwright-browsers",
    "ollama-models",
    "brain_state",       # personal accumulated brain memory — fresh on first run
    "backups",
    "recordings",
    "workspace"          # Jarvis dev workspace can have user code in it
)
$exclFiles = @(
    "*.pyc", "*.pyo", "*.pyd", "*.log", "*.lnk", "*.stdout",
    "vault_sync_state.json"
)

$rcArgs = @($ProjectRoot, $Staging, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP")
$rcArgs += "/XD"
$rcArgs += $exclDirs
$rcArgs += "frontend.backup-*"
$rcArgs += "/XF"
$rcArgs += $exclFiles

robocopy @rcArgs | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "robocopy failed (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
Write-Host "  staged $((Get-ChildItem $Staging -Recurse -File | Measure-Object).Count) files" -ForegroundColor Green

# -----------------------------------------------------------------------------
Step "2. Sanitize ops-center/_jarvis/config.json (blank API keys)"
$cfgPath = Join-Path $Staging "ops-center\_jarvis\config.json"
$expPath = Join-Path $Staging "ops-center\_jarvis\config.example.json"
$template = $null
if (Test-Path $expPath) {
    $template = Get-Content $expPath -Raw | ConvertFrom-Json
}
$cleanConfig = [ordered]@{
    "_comment"                 = "Open the gear icon in the dashboard to set keys, or fill these in by hand. All API keys optional except ONE LLM key (Anthropic, OpenAI, Groq, Ollama, or xAI)."
    "llm_provider"             = "anthropic"
    "llm_model"                = "claude-haiku-4-5-20251001"
    "anthropic_api_key"        = ""
    "openai_api_key"           = ""
    "groq_api_key"             = ""
    "ollama_api_key"           = "ollama"
    "xai_api_key"              = ""
    "tts_provider"             = "auto"
    "elevenlabs_api_key"       = ""
    "elevenlabs_voice_id"      = ""
    "brain_voice_id"           = ""
    "user_name"                = "Friend"
    "user_address"             = "sir"
    "city"                     = ""
    "workspace_path"           = ""
    "brain_path"               = ""
    "dev_workspace"            = ""
    "obsidian_inbox_path"      = ""
    "neurolink_url"            = "http://localhost:8020"
    "auto_connect_neurolink"   = $true
    "spotify_track"            = ""
    "browser_url"              = ""
    "apps"                     = @()
}
$cleanConfig | ConvertTo-Json -Depth 6 | Set-Content -Path $cfgPath -Encoding UTF8
Write-Host "  config.json sanitized (all keys blank)" -ForegroundColor Green

# -----------------------------------------------------------------------------
Step "3. Reset ops-center/state.json (empty operator state)"
$statePath = Join-Path $Staging "ops-center\state.json"
$emptyState = [ordered]@{
    brain_stats = @{ total_notes = 0; folders = 0; folder_breakdown = @{} }
    docs        = @()
    calendar    = @()
    slack_inbox = @()
}
$emptyState | ConvertTo-Json -Depth 4 | Set-Content -Path $statePath -Encoding UTF8

# -----------------------------------------------------------------------------
Step "4. Reset ops-center agent files"
foreach ($f in @("agents.json", "custom_agents.json")) {
    $p = Join-Path $Staging "ops-center\$f"
    if (Test-Path $p) {
        # Replace with empty list / dict — preserve whichever shape was there
        $orig = $null
        try { $orig = Get-Content $p -Raw | ConvertFrom-Json } catch { }
        if ($orig -is [System.Array]) {
            "[]" | Set-Content -Path $p -Encoding UTF8
        } else {
            "{}" | Set-Content -Path $p -Encoding UTF8
        }
    }
}

# -----------------------------------------------------------------------------
Step "5. Mark .sh files executable on POSIX (LF line endings + permissions)"
# Convert CRLF -> LF on the shell scripts so bash on macOS/Linux doesn't choke
foreach ($sh in Get-ChildItem $Staging -Recurse -Filter "*.sh") {
    $content = Get-Content $sh.FullName -Raw
    [System.IO.File]::WriteAllText($sh.FullName, ($content -replace "`r`n", "`n"))
}
Write-Host "  .sh files normalized to LF" -ForegroundColor Green

# -----------------------------------------------------------------------------
Step "6. Final size + zip"
$totalSize = (Get-ChildItem $Staging -Recurse | Measure-Object Length -Sum).Sum
Write-Host ("  staging total: {0:N1} MB" -f ($totalSize / 1MB))

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path "$Staging\*" -DestinationPath $ZipPath -CompressionLevel Optimal -Force

$zipSize = (Get-Item $ZipPath).Length
Write-Host ""
Write-Host "===============================================================" -ForegroundColor Green
Write-Host " DISTRIBUTION READY" -ForegroundColor Green
Write-Host ("   path : {0}" -f $ZipPath) -ForegroundColor White
Write-Host ("   size : {0:N1} MB" -f ($zipSize / 1MB)) -ForegroundColor White
Write-Host ""
Write-Host " End-user steps:" -ForegroundColor Green
Write-Host "   Windows:  unzip → run install.ps1 → double-click start.bat" -ForegroundColor White
Write-Host "   macOS:    unzip → bash install.sh   → ./start.sh" -ForegroundColor White
Write-Host "   Linux:    unzip → bash install.sh   → ./start.sh" -ForegroundColor White
Write-Host "===============================================================" -ForegroundColor Green
