"""POST /anthropic/v1/messages — reverse-proxy to DeepSeek /anthropic endpoint.
With model ID masquerade: claude-* names → real deepseek names, and reverse in responses.
"""

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from .config import load_config
from .mapper import get_mapper
from .upstream import stream_anthropic, post_anthropic_non_streaming
from .logger import RequestLog, detect_client_type
from .cache_prefix import inject_prefix_anthropic

router = APIRouter()

# Anthropic SSE events that carry a "model" field (need masquerade on response)
ANTHROPIC_MODEL_EVENTS = {"message_start"}
MAX_TOOL_RESULT_CHARS = 100000
MAX_OUTPUT_TOKENS = 256000


@router.api_route("/anthropic/v1/messages", methods=["POST", "OPTIONS"])
@router.api_route("/v1/messages", methods=["POST", "OPTIONS"])
async def proxy_anthropic(request: Request):
    if request.method == "OPTIONS":
        return _cors_response()

    config = load_config()
    mapper = get_mapper()
    rlog = RequestLog("POST", "/anthropic/v1/messages", detect_client_type(request))

    body = await request.json()
    client_model = body.get("model", "")
    upstream_model = mapper.resolve_anthropic(client_model)

    # Log Anthropic request
    import os as _os
    _debug_log = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'debug_requests.log')
    try:
        with open(_debug_log, 'a', encoding='utf-8') as _f:
            _f.write(f"\n[ANTHROPIC] model={client_model} -> {upstream_model} stream={body.get('stream')} max_tokens={body.get('max_tokens')} msgs={len(body.get('messages',[]))}\n")
            _f.write(f"  thinking={body.get('thinking','N/A')} budget_tokens={body.get('budget_tokens','N/A')}\n")
    except: pass

    body["model"] = upstream_model
    rlog.model = client_model
    rlog.streaming = body.get("stream", False)

    _clean_anthropic_body(body)

    # Inject stable CLAUDE.md cache prefix for KV cache pooling across clients
    body["messages"] = inject_prefix_anthropic(body.get("messages", []))

    if body.get("stream"):
        try:
            upstream_resp = await stream_anthropic(body, f"{config.anthropic_endpoint}/v1/messages", config.deepseek_api_key)
            rlog.finish(upstream_resp.status_code)
        except Exception as _e:
            try:
                with open(_debug_log, 'a', encoding='utf-8') as _f:
                    _f.write(f"  ANTHROPIC UPSTREAM ERROR: {_e}\n")
            except: pass
            return JSONResponse({"error": {"message": str(_e)}}, status_code=502)
        return StreamingResponse(
            _sse_masquerade(upstream_resp, mapper),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
            },
        )
        try:
            with open(_debug_log, 'a', encoding='utf-8') as _f:
                _f.write(f"  ANTHROPIC -> {upstream_resp.status_code} (streaming)\n")
        except: pass
    else:
        try:
            upstream_json = await post_anthropic_non_streaming(body, f"{config.anthropic_endpoint}/v1/messages", config.deepseek_api_key)
        except Exception as _e:
            try:
                with open(_debug_log, 'a', encoding='utf-8') as _f:
                    _f.write(f"  ANTHROPIC UPSTREAM ERROR (non-streaming): {_e}\n")
            except: pass
            return JSONResponse({"error": {"message": str(_e)}}, status_code=502)
        if "model" in upstream_json:
            upstream_json["model"] = mapper.reverse_anthropic(upstream_json["model"])
        rlog.finish(200)
        return JSONResponse(upstream_json)


async def _sse_masquerade(upstream_resp, mapper):
    """Stream SSE bytes from upstream, replacing model ID in message_start events."""
    async for line in upstream_resp.aiter_lines():
        if line.startswith("data: ") and len(line) > 6:
            data_str = line[6:]
            try:
                data = json.loads(data_str)
                event_type = data.get("type", "")
                if event_type in ANTHROPIC_MODEL_EVENTS:
                    # Anthropic SSE: model is nested inside response/message
                    if "message" in data and "model" in data["message"]:
                        data["message"]["model"] = mapper.reverse_anthropic(data["message"]["model"])
                    if "response" in data and "model" in data.get("response", {}):
                        data["response"]["model"] = mapper.reverse_anthropic(data["response"]["model"])
                if event_type in ANTHROPIC_MODEL_EVENTS or "model" in data:
                    if "model" in data and not isinstance(data.get("message"), dict) and not isinstance(data.get("response"), dict):
                        data["model"] = mapper.reverse_anthropic(data["model"])
                line = f"data: {json.dumps(data, ensure_ascii=False)}"
            except json.JSONDecodeError:
                pass  # pass through non-JSON SSE lines
        yield f"{line}\n"


def _clean_anthropic_body(body: dict):
    """Apply safe limits and fix Claude Code sub-agent thinking conflict.

    Claude Code >=2.1.166 hardcodes thinking:{type:"disabled"} alongside
    reasoning_effort for sub-agents — DeepSeek rejects this combination.
    We override disabled→enabled so thinking mode actually works.
    """
    if body.get("max_tokens", 0) > MAX_OUTPUT_TOKENS:
        body["max_tokens"] = MAX_OUTPUT_TOKENS

    _fix_thinking_disabled(body)

    _truncate_tool_results(body.get("messages", []))


def _fix_thinking_disabled(body: dict):
    """If thinking is explicitly disabled, flip it to enabled.

    Claude Code sends thinking:{type:"disabled"} for sub-agents even when
    CLAUDE_CODE_EFFORT_LEVEL=max is set.  This is a known conflict with
    DeepSeek's /anthropic endpoint — reasoning_effort demands thinking on.
    """
    thinking = body.get("thinking")
    if not isinstance(thinking, dict):
        return
    if thinking.get("type") != "disabled":
        return

    thinking["type"] = "enabled"
    # Note: budget_tokens is ignored by DeepSeek API (per official docs).
    # Don't inject it — it pollutes request body consistency and hurts KV cache hit rate.
    body["thinking"] = thinking

    # Also patch output_config if present (Anthropic-format effort param)
    output_config = body.get("output_config")
    if isinstance(output_config, dict) and "effort" in output_config:
        # effort is set → keep it (it was conflicting with disabled; now fixed)
        pass


def _truncate_tool_results(messages: list):
    """Truncate tool_result content > MAX_TOOL_RESULT_CHARS to avoid upstream issues."""
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_result":
                continue
            inner = block.get("content")
            if isinstance(inner, str) and len(inner) > MAX_TOOL_RESULT_CHARS:
                block["content"] = inner[:MAX_TOOL_RESULT_CHARS] + (
                    f"\n...[truncated from {len(inner)} to {MAX_TOOL_RESULT_CHARS} chars by gateway]"
                )
            elif isinstance(inner, list):
                total = sum(len(c.get("text", "")) for c in inner if isinstance(c, dict))
                if total > MAX_TOOL_RESULT_CHARS:
                    truncated = []
                    remaining = MAX_TOOL_RESULT_CHARS
                    for c in inner:
                        if not isinstance(c, dict) or c.get("type") != "text":
                            truncated.append(c)
                            continue
                        t = c.get("text", "")
                        if len(t) <= remaining:
                            truncated.append(c)
                            remaining -= len(t)
                        else:
                            truncated.append({**c, "text": t[:remaining] + "\n...[truncated by gateway]"})
                            break
                    block["content"] = truncated


def _cors_response():
    return JSONResponse({"ok": True}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })
