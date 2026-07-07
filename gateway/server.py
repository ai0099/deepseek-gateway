"""FastAPI application factory."""

import os as _os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_config
from .logger import setup_logging, rotate_log_file
from .upstream import get_upstream_client, close_upstream_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    setup_logging(config.log_level)
    get_upstream_client()
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

    # Catch-all request logger
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
        return {
            "status": "ok",
            "configured": config.is_configured,
            "anthropic_endpoint": config.anthropic_endpoint,
        }

    @app.get("/")
    async def root():
        return {
            "service": "DeepSeek Gateway",
            "version": "1.0.0",
            "endpoints": {
                "anthropic": "/anthropic/v1/messages",
                "models": "/v1/models",
                "responses": "/v1/responses",
                "health": "/health",
            },
        }

    # Catch-all route to log unmatched requests (debugging)
    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def catch_all(request, full_path: str):
        config = load_config()
        if config.debug:
            import os as _os
            _debug_log = _os.path.join(_os.path.dirname(__file__), "..", "debug_requests.log")
            try:
                with open(_debug_log, "a", encoding="utf-8") as _f:
                    _f.write(f"[UNMATCHED] {request.method} /{full_path} from {request.client.host if request.client else chr(63)}")
            except Exception: pass
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found", "path": f"/{full_path}"}, status_code=404)

    return app
