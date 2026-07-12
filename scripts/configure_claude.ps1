# Configure Claude Desktop to use DeepSeek Gateway
param(
    [string]$GatewayUrl = "http://127.0.0.1:8080",
    [string]$GatewayKey = "proxy"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Configure Claude Desktop for DeepSeek" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# --- Find Claude Desktop config directory ---
$possiblePaths = @(
    "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude",
    "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude-3p",
    "$env:APPDATA\Claude-3p",
    "$env:LOCALAPPDATA\Claude-3p",
    "$env:APPDATA\Claude",
    "$env:LOCALAPPDATA\Claude"
)

$configRoot = $null
foreach ($p in $possiblePaths) {
    if (Test-Path $p) {
        $configRoot = $p
        Write-Host "Found Claude config: $configRoot" -ForegroundColor Cyan
        break
    }
}

if (-not $configRoot) {
    Write-Host "Claude Desktop not found in standard locations." -ForegroundColor Yellow
    Write-Host "Checking: $($possiblePaths -join ', ')" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "If Claude Desktop is installed, run it once first, then re-run this script." -ForegroundColor Yellow
    exit 1
}

# --- Load existing config or create new ---
$desktopConfig = Join-Path $configRoot "claude_desktop_config.json"
$cfg = @{}
if (Test-Path $desktopConfig) {
    try { $cfg = Get-Content $desktopConfig -Raw | ConvertFrom-Json -AsHashtable } catch { $cfg = @{} }
}

$cfg["deploymentMode"] = "3p"
$cfg["enterpriseConfig"] = @{
    inferenceProvider            = "gateway"
    inferenceGatewayBaseUrl      = $GatewayUrl
    inferenceGatewayApiKey       = $GatewayKey
    inferenceGatewayAuthScheme   = "bearer"
    inferenceModels              = @(
        "claude-fable-5"
    )
    disableEssentialTelemetry    = $true
    disableNonessentialTelemetry = $true
    disableNonessentialServices  = $true
}

$cfg | ConvertTo-Json -Depth 6 | Set-Content -Path $desktopConfig
Write-Host "  Wrote: $desktopConfig" -ForegroundColor Gray
Write-Host ""
Write-Host "Claude Desktop configured. Restart Claude Desktop now." -ForegroundColor Green
