"""FastAPI application factory — with cache warmup on startup."""

import os as _os
import asyncio as _asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_config
from .logger import setup_logging, rotate_log_file
from .upstream import get_upstream_client, close_upstream_client


async def _warmup_cache(config):
    """Send minimal requests to pre-build KV cache for BOTH Claude and Codex routes.

    DeepSeek disk cache is built on first use. Without warmup, first real request
    has 0% cache hit. Claude and Codex have different prefixes and endpoints,
    so both must be warmed separately.
    """
    client = get_upstream_client()

    # ── Codex route (Chat Completions endpoint) ──
    try:
        from .inject_codex import inject_prefix_chat
        _, fm, _ = inject_prefix_chat([], "")
        resp = await client.post(
            config.chat_completions_endpoint,
            json={
                "model": "deepseek-v4-pro",
                "messages": fm + [{"role": "user", "content": "ping"}],
                "stream": False,
                "max_tokens": 1,
                "thinking": {"type": "enabled"},
            },
            headers={
                "Authorization": f"Bearer {config.deepseek_api_key}",
                "content-type": "application/json",
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            u = resp.json().get("usage", {})
            h = u.get("prompt_cache_hit_tokens", 0)
            m = u.get("prompt_cache_miss_tokens", 0)
            t = h + m
            print(f"\n  >>> [warmup:codex] OK {t/1e3:.1f}K tokens "
                  f"{round(h/t*100) if t else 0}% hit\n")
        else:
            print(f"  [warmup:codex] HTTP {resp.status_code}")
    except Exception as e:
        print(f"  [warmup:codex] failed: {e}")

    # ── Claude route (Anthropic endpoint) ──
    try:
        from .inject_rules import _INJECTION_STRING
        resp = await client.post(
            config.anthropic_endpoint + "/v1/messages",
            json={
                "model": "deepseek-v4-pro[1m]",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
                "system": _INJECTION_STRING + "\n\nping",
                "thinking": {"type": "enabled"},
            },
            headers={
                "x-api-key": config.deepseek_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            u = resp.json().get("usage", {})
            h = u.get("cache_read_input_tokens", 0)
            t = u.get("input_tokens", 0)
            print(f"  >>> [warmup:claude] OK {t/1e3:.1f}K tokens "
                  f"{round(h/t*100) if t else 0}% hit\n")
        else:
            print(f"  [warmup:claude] HTTP {resp.status_code}")
    except Exception as e:
        print(f"  [warmup:claude] failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    setup_logging(config.log_level)
    get_upstream_client()

    if config.is_configured:
        _task = _asyncio.create_task(_warmup_cache(config))

    yield

    await close_upstream_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="DeepSeek Gateway",
        description="Local proxy — Anthropic Messages API & OpenAI Responses API → DeepSeek",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_all_requests(request, call_next):
        config = load_config()
        if not config.debug:
            return await call_next(request)
        _debug_log = _os.path.join(_os.path.dirname(__file__), '..', 'debug_requests.log')
        rotate_log_file(_debug_log)
        try:
            with open(_debug_log, 'a', encoding='utf-8') as _f:
                _f.write(f"\n[MIDDLEWARE] {request.method} {request.url.path} from {request.client.host if request.client else '?'}\n")
                _f.write(f"  UA: {request.headers.get('user-agent', 'none')[:200]}\n")
                if request.query_params:
                    _f.write(f"  query: {dict(request.query_params)}\n")
        except Exception: pass
        response = await call_next(request)
        try:
            with open(_debug_log, 'a', encoding='utf-8') as _f:
                _f.write(f"  -> {response.status_code}\n")
        except Exception: pass
        return response

    from .routes_models import router as models_router
    from .routes_anthropic import router as anthropic_router
    from .routes_responses import router as responses_router

    app.include_router(models_router)
    app.include_router(anthropic_router)
    app.include_router(responses_router)

    @app.get("/health")
    async def health():
        config = load_config()
        return {"status": "ok", "configured": config.is_configured}

    @app.get("/")
    async def root():
        return {"service": "DeepSeek Gateway", "version": "1.0.0"}

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def catch_all(request, full_path: str):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found", "path": f"/{full_path}"}, status_code=404)

    return app
