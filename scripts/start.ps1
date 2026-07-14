# DeepSeek Gateway — Start (PowerShell)
# Double-click to launch gateway in background

$gatewayDir = "E:\Claude\deepseek-gateway"
$python = "C:\Users\Administrator\.claude\venv\Scripts\python.exe"
$port = 8080

# 1. Kill old process
$pids = (netstat -ano | Select-String ":$port.*LISTENING" | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique)
foreach ($p in $pids) { taskkill /F /PID $p 2>$null }

# 2. Launch
$log = Join-Path $gatewayDir "gateway.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$ts] Starting gateway..." | Out-File $log -Encoding utf8

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $python
$psi.Arguments = "`"$gatewayDir\main.py`" --port $port"
$psi.WorkingDirectory = $gatewayDir
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true

$proc = [System.Diagnostics.Process]::Start($psi)

# 3. Health check
$url = "http://127.0.0.1:$port/health"
for ($i = 1; $i -le 10; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod -Uri $url -TimeoutSec 3
        if ($r.status -eq "ok") {
            Write-Host "Gateway ready — PID $($proc.Id), http://127.0.0.1:$port"
            exit 0
        }
    } catch {}
}
Write-Host "Gateway started (PID $($proc.Id)). Check $log"