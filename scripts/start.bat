@echo off
cd /d "E:\Claude\deepseek-gateway"

:: Kill any existing gateway on port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Launch gateway via VBS (detached, no console window)
set LOG=E:\Claude\deepseek-gateway\gateway.log
echo Gateway starting... log: %LOG%

set VBS=%TEMP%\start_gateway.vbs
(
echo Set WshShell = CreateObject("WScript.Shell"^)
echo cmd = "cmd /c """"C:\Users\Administrator\.claude\venv\Scripts\python.exe"" ""E:\Claude\deepseek-gateway\main.py"" --port 8080 ^> ""%LOG%"" 2^>^&1"""
echo WshShell.Run cmd, 0, False
) > "%VBS%"

cscript //nologo "%VBS%"
del "%VBS%"

:: Wait for startup
set /a COUNT=0
:waitloop
timeout /t 1 /nobreak >nul
set /a COUNT+=1
curl -s http://127.0.0.1:8080/health | findstr "ok" >nul && goto :started
if %COUNT% lss 15 goto :waitloop
echo WARNING: Gateway did not respond within 15s. Check %LOG%
goto :end

:started
echo Gateway is running on http://127.0.0.1:8080

:end
