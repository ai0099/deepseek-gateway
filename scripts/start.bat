@echo off
title DeepSeek Gateway
cd /d "E:\Claude\deepseek-gateway"

echo ========================================
echo   DeepSeek Gateway
echo   http://127.0.0.1:8080
echo ========================================
echo.
echo Starting...
echo.

"C:\Users\Administrator\.claude\venv\Scripts\python.exe" main.py --port 8080

pause
