@echo off
cd /d "E:\Claude\deepseek-gateway"

:: Kill any existing gateway on port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Launch gateway with visible console for diagnostics (warmup, cache stats)
echo ================================================================
echo   DeepSeek Gateway starting...
echo   Watch for [inject_codex] and [warmup] messages below
echo ================================================================
echo.

C:\Users\Administrator\.claude\venv\Scripts\python.exe E:\Claude\deepseek-gateway\main.py --port 8080
