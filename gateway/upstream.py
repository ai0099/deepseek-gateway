"""httpx async client factory and upstream streaming helpers."""

import httpx
from .config import load_config, Settings

_client: httpx.AsyncClient | None = None


def get_upstream_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        config = load_config()
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout),
            limits=limits,
            verify=True,
        )
    return _client


async def close_upstream_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def stream_anthropic(
    body: dict,
    endpoint_url: str,
    api_key: str,
) -> httpx.Response:
    """POST a streaming request to upstream Anthropic-compatible endpoint."""
    client = get_upstream_client()
    headers = _build_anthropic_headers(api_key)
    return await client.send(
        client.build_request("POST", endpoint_url, json=body, headers=headers),
        stream=True,
    )


async def stream_chat_completions(
    body: dict,
    endpoint_url: str,
    api_key: str,
) -> httpx.Response:
    """POST a streaming request to upstream Chat Completions endpoint."""
    client = get_upstream_client()
    headers = _build_openai_headers(api_key)
    req = client.build_request("POST", endpoint_url, json=body, headers=headers)
    resp = await client.send(req, stream=True)
    if resp.status_code >= 400:
        error_body = ""
        try:
            error_body = (await resp.aread()).decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        raise Exception(f"Upstream {resp.status_code}: {error_body}")
    return resp


async def post_non_streaming(
    body: dict,
    endpoint_url: str,
    api_key: str,
) -> dict:
    """POST a non-streaming request and return JSON response."""
    client = get_upstream_client()
    headers = _build_openai_headers(api_key)
    resp = await client.post(endpoint_url, json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def post_anthropic_non_streaming(
    body: dict,
    endpoint_url: str,
    api_key: str,
) -> dict:
    """POST a non-streaming request to Anthropic endpoint and return JSON."""
    client = get_upstream_client()
    headers = _build_anthropic_headers(api_key)
    resp = await client.post(endpoint_url, json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _build_anthropic_headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _build_openai_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
