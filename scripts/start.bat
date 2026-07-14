@echo off
cd /d "E:\Claude\deepseek-gateway"

:: Kill any existing gateway on port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Launch gateway detached via VBS, output to log file
set LOG=E:\Claude\deepseek-gateway\gateway.log
echo Gateway starting... output -> %LOG%

set VBS=%TEMP%\start_gateway.vbs
echo Set WshShell = CreateObject("WScript.Shell") > "%VBS%"
echo WshShell.Run """C:\Users\Administrator\.claude\venv\Scripts\python.exe"" ""E:\Claude\deepseek-gateway\main.py"" --port 8080 ^> ""%LOG%"" 2^>^&1", 0, False >> "%VBS%"
cscript //nologo "%VBS%"
del "%VBS%"

echo Gateway launched in background.
echo Check log: type %LOG%
