@echo off
title DeepSeek Gateway
cd /d "E:\Claude\deepseek-gateway"

echo ========================================
echo   DeepSeek Gateway
echo   http://127.0.0.1:8080
echo ========================================
echo.

netstat -ano | findstr ":8080.*LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Gateway is already running on port 8080.
    echo      Open http://127.0.0.1:8080/health to verify.
    echo.
    pause
    exit /b 0
)

echo Starting...
echo.

"C:\Users\Administrator\.claude\venv\Scripts\python.exe" main.py --port 8080

pause
