"""Structured request/response logging."""

import time
import logging
import json
from pathlib import Path

logger = logging.getLogger("deepseek-gateway")


def setup_logging(level: str = "info"):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))


class RequestLog:
    def __init__(self, method: str, path: str, client_type: str = "unknown"):
        self.method = method
        self.path = path
        self.client_type = client_type
        self.start = time.monotonic()
        self.model = ""
        self.streaming = False
        self.status = 0
        self.usage = None  # token usage dict

    def finish(self, status: int, usage: dict = None):
        self.status = status
        self.usage = usage
        elapsed = (time.monotonic() - self.start) * 1000
        
        # Token stats line
        token_info = ""
        if usage:
            inp = usage.get("input_tokens", usage.get("prompt_tokens", 0))
            out = usage.get("output_tokens", usage.get("completion_tokens", 0))
            total = usage.get("total_tokens", inp + out)
            # DeepSeek disk cache fields
            cache_hit = usage.get("prompt_cache_hit_tokens", 0)
            cache_miss = usage.get("prompt_cache_miss_tokens", 0)
            if cache_hit or cache_miss:
                hit_rate = cache_hit / (cache_hit + cache_miss) * 100 if (cache_hit + cache_miss) > 0 else 0
                cache_info = f" cache: {cache_hit/1e3:.1f}K hit + {cache_miss/1e3:.1f}K miss = {hit_rate:.0f}%"
            else:
                cache_info = ""
            token_info = f" | {inp/1e3:.0f}K+{out/1e3:.0f}K={total/1e3:.0f}K{cache_info}"
        
        logger.info(
            f"{self.method} {self.path} | {self.client_type} | "
            f"model={self.model or '-'} | stream={self.streaming} | "
            f"{status} | {elapsed:.0f}ms{token_info}"
        )
        
        # Write token usage to dedicated log file
        if usage:
            token_log = Path(__file__).parent.parent / "token_usage.log"
            try:
                entry = {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "client": self.client_type,
                    "model": self.model,
                    "usage": usage,
                }
                with open(token_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass


def rotate_log_file(filepath: str, max_size: int = 10 * 1024 * 1024, backups: int = 3):
    """Rotate log file if it exceeds max_size. Keeps up to `backups` old copies."""
    import os as _os
    try:
        if _os.path.exists(filepath) and _os.path.getsize(filepath) > max_size:
            for i in range(backups - 1, 0, -1):
                old = f"{filepath}.{i}"
                new = f"{filepath}.{i + 1}"
                if _os.path.exists(old):
                    _os.replace(old, new)
            _os.replace(filepath, f"{filepath}.1")
    except OSError:
        pass


def trim_debug_log(filepath: str, keep_requests: int = 10):
    """Keep only the last N request blocks in the debug log.

    A request block starts with ``[ANTHROPIC]`` or ``[MIDDLEWARE]``.
    Trims from the top, keeping the most recent entries.
    """
    import os as _os
    try:
        if not _os.path.exists(filepath):
            return
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return

    # Split on request boundaries
    lines = content.splitlines(keepends=True)
    if not lines:
        return

    # Find all block start positions
    block_starts = []
    for i, line in enumerate(lines):
        if line.startswith('[ANTHROPIC]') or line.startswith('[MIDDLEWARE]'):
            block_starts.append(i)

    if len(block_starts) <= keep_requests:
        return

    # Trim: keep everything from the Nth-last block start
    trim_from = block_starts[-keep_requests]
    trimmed = ''.join(lines[trim_from:])

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(trimmed)
    except Exception:
        pass


def detect_client_type(request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    accept = (request.headers.get("accept") or "").lower()

    if "codex" in ua:
        return "codex-cli"
    if "claude-code" in ua or "claude_code" in ua:
        return "claude-code"
    if "vnd.anthropic" in accept:
        return "claude-desktop"
    if "openai" in ua or "codex" in ua:
        return "codex-desktop"
    return "unknown"
