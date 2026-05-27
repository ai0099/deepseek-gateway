@echo off
cd /d "E:\Claude\deepseek-gateway"

:: Kill any existing gateway on port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Start silently in background (pythonw.exe = no console window)
start "" "C:\Users\Administrator\.claude\venv\Scripts\pythonw.exe" main.py --port 8080
