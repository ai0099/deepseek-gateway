"""Gateway launcher — starts main.py detached, no console window."""
import subprocess, sys, time, urllib.request, json

GATEWAY_DIR = r"E:\Claude\deepseek-gateway"
PYTHON = r"C:\Users\Administrator\.claude\venv\Scripts\python.exe"
PORT = 8080

def main():
    # Kill existing
    import os
    os.system(f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr ":{PORT}.*LISTENING"\') do taskkill /F /PID %a >nul 2>&1')
    time.sleep(1)

    # Launch detached
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    log_path = os.path.join(GATEWAY_DIR, "gateway.log")
    
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting gateway...\n")
    
    with open(log_path, "a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [PYTHON, os.path.join(GATEWAY_DIR, "main.py"), "--port", str(PORT)],
            cwd=GATEWAY_DIR,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    
    print(f"Gateway PID: {proc.pid}")
    
    # Wait for health
    url = f"http://127.0.0.1:{PORT}/health"
    for i in range(15):
        time.sleep(2)
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            data = json.loads(resp.read())
            if data.get("status") == "ok":
                print(f"Gateway ready: {data}")
                return
        except Exception:
            pass
    
    print("WARNING: Gateway did not respond. Check gateway.log")
    sys.exit(1)

if __name__ == "__main__":
    main()
