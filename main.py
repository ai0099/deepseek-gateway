"""Entry point for DeepSeek Gateway."""

import sys
import io
import os
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

CRASH_LOG = Path(__file__).resolve().parent / "crash.log"

def _log_crash(exc_type, exc_value, exc_tb):
    """Write ALL unhandled exceptions to crash.log before process dies."""
    msg = "\n" + "=" * 70 + "\n"
    msg += f"CRASH [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {exc_type.__name__}: {exc_value}\n"
    msg += "=" * 70 + "\n"
    msg += "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    msg += "\n"
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _log_crash


def main():
    # Route ALL output (stdout+stderr) to gateway.log with line buffering
    log_path = Path(__file__).resolve().parent / "gateway.log"
    log_fh = open(str(log_path), "a", encoding="utf-8", buffering=1)

    class _Tee:
        def __init__(self, *files): self.files = files
        def write(self, s):
            for f in self.files: f.write(s); f.flush()
        def flush(self):
            for f in self.files: f.flush()

    sys.stdout = _Tee(sys.stdout, log_fh)
    sys.stderr = _Tee(sys.stderr, log_fh)

    print(f"=== Gateway started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    import uvicorn
    from gateway.config import load_config

    import argparse
    p = argparse.ArgumentParser(description="DeepSeek Gateway")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()

    config = load_config()
    host = args.host or config.host
    port = args.port or config.port

    if not config.is_configured:
        print("WARNING: DS_GW_DEEPSEEK_API_KEY not set.")

    print(f"  DeepSeek Gateway  http://{host}:{port}")
    print()

    uvicorn.run(
        "gateway.server:create_app",
        host=host,
        port=port,
        log_level=config.log_level,
        reload=args.reload,
        factory=True,
    )


if __name__ == "__main__":
    main()
