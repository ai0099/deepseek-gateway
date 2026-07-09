# Configure Codex CLI + Desktop to use DeepSeek Gateway
param(
    [string]$GatewayUrl = "http://127.0.0.1:8080"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Configure Codex for DeepSeek" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

$codexDir = "$env:USERPROFILE\.codex"
if (-not (Test-Path $codexDir)) {
    New-Item -ItemType Directory -Path $codexDir -Force | Out-Null
}

# --- Write auth.json ---
$authFile = Join-Path $codexDir "auth.json"
$authContent = @{ OPENAI_API_KEY = "deepseek-gateway-local" } | ConvertTo-Json -Compress
Set-Content -Path $authFile -Value $authContent
Write-Host "  Wrote: $authFile" -ForegroundColor Gray

# --- Write config.toml ---
$configFile = Join-Path $codexDir "config.toml"
$tomlContent = @'
# DeepSeek Gateway -- Codex Configuration
model = "gpt-5.5[1m]"
model_provider = "deepseek-gateway"

[model_providers.deepseek-gateway]
name = "DeepSeek (via Gateway)"
base_url = "{GATEWAY_URL}/v1"
wire_api = "responses"
requires_openai_auth = true
request_max_retries = 1
'@ -replace "{GATEWAY_URL}", $GatewayUrl

Set-Content -Path $configFile -Value $tomlContent -Encoding UTF8
Write-Host "  Wrote: $configFile" -ForegroundColor Gray

Write-Host ""
Write-Host "Codex configured. Restart Codex CLI/Desktop now." -ForegroundColor Green
Write-Host ""
Write-Host "Verify:" -ForegroundColor Cyan
Write-Host "  codex --version" -ForegroundColor Gray
Write-Host "  codex (should connect to DeepSeek via gateway)" -ForegroundColor Gray
Write-Host ""
Write-Host "Make sure the gateway is running before launching Codex." -ForegroundColor Cyan
Write-Host "  Start gateway: .\scripts\start.ps1" -ForegroundColor Cyan
