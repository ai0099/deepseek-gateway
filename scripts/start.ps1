# Start DeepSeek Gateway
$venv = "C:\Users\Administrator\.claude\venv\Scripts\python.exe"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $dir "..")

Write-Host "Starting DeepSeek Gateway..." -ForegroundColor Green
& $venv main.py $args
