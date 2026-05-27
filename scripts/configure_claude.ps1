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
    "$env:APPDATA\Claude-3p",
    "$env:LOCALAPPDATA\Claude-3p",
    "$env:APPDATA\Claude",
    "$env:LOCALAPPDATA\Claude",
    "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude-3p"
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

# --- Write claude_desktop_config.json ---
$desktopConfig = Join-Path $configRoot "claude_desktop_config.json"
$content = @{ deploymentMode = "3p" } | ConvertTo-Json -Compress
Set-Content -Path $desktopConfig -Value $content
Write-Host "  Wrote: $desktopConfig" -ForegroundColor Gray

# --- Write configLibrary entry ---
$libDir = Join-Path $configRoot "configLibrary"
if (-not (Test-Path $libDir)) {
    New-Item -ItemType Directory -Path $libDir -Force | Out-Null
}

$uuid = "a0a0a0a0-b1b1-4c2c-9d3d-e4e4e4e4e4e4"
$libFile = Join-Path $libDir "$uuid.json"

$entry = @{
    coworkEgressAllowedHosts = @("*")
    inferenceProvider = "gateway"
    inferenceGatewayBaseUrl = $GatewayUrl
    inferenceGatewayApiKey = $GatewayKey
    inferenceGatewayAuthScheme = "bearer"
    inferenceModels = @(
        @{ name = "claude-sonnet-4-20250514";    supports1m = $true }
        @{ name = "claude-opus-4-20250514";      supports1m = $true }
        @{ name = "claude-3-5-sonnet-20241022";  supports1m = $true }
        @{ name = "claude-3-opus-20240229";       supports1m = $true }
        @{ name = "claude-3-haiku-20240307";      supports1m = $true }
        @{ name = "claude-3-5-haiku-20241022";    supports1m = $true }
        @{ name = "claude-3-5-sonnet-20240620";   supports1m = $true }
        @{ name = "claude-3-sonnet-20240229";     supports1m = $true }
    )
} | ConvertTo-Json -Depth 4

Set-Content -Path $libFile -Value $entry
Write-Host "  Wrote: $libFile" -ForegroundColor Gray

# --- Write _meta.json ---
$metaFile = Join-Path $libDir "_meta.json"
$meta = @{
    entries = @(
        @{
            uuid = $uuid
            name = "DeepSeek Gateway"
            description = "Local proxy — DeepSeek via Anthropic Messages API"
        }
    )
} | ConvertTo-Json -Depth 3
Set-Content -Path $metaFile -Value $meta

Write-Host ""
Write-Host "Claude Desktop configured. Restart Claude Desktop now." -ForegroundColor Green
Write-Host ""

# --- Claude Code CLI instructions ---
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Claude Code CLI Configuration" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Set these environment variables:" -ForegroundColor Yellow
Write-Host '  set ANTHROPIC_BASE_URL=http://127.0.0.1:8080/anthropic/v1'
Write-Host '  set ANTHROPIC_AUTH_TOKEN=proxy'
Write-Host ""
Write-Host "Or add to your shell config (~/.bashrc or profile):" -ForegroundColor Gray
Write-Host '  export ANTHROPIC_BASE_URL=http://127.0.0.1:8080/anthropic/v1'
Write-Host '  export ANTHROPIC_AUTH_TOKEN=proxy'
Write-Host ""

Write-Host "Make sure the gateway is running before launching Claude Desktop or Claude Code." -ForegroundColor Cyan
Write-Host "  Start gateway: .\scripts\start.ps1" -ForegroundColor Cyan
