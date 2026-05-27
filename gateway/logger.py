"""Structured request/response logging."""

import time
import logging

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

    def finish(self, status: int):
        self.status = status
        elapsed = (time.monotonic() - self.start) * 1000
        logger.info(
            f"{self.method} {self.path} | {self.client_type} | "
            f"model={self.model or '-'} | stream={self.streaming} | "
            f"{status} | {elapsed:.0f}ms"
        )


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
