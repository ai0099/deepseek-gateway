"""Entry point for DeepSeek Gateway."""

import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from gateway.config import load_config


def main():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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
        print("WARNING: DS_GW_DEEPSEEK_API_KEY not set. Copy .env and set your API key.")
        print(f"  .env → {Path(__file__).resolve().parent / '.env'}")

    print(f"  DeepSeek Gateway")
    print(f"  http://{host}:{port}")
    print(f"  Anthropic upstream: {config.anthropic_endpoint}/v1/messages")
    print(f"  ChatCompletions upstream: {config.chat_completions_endpoint}")
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
