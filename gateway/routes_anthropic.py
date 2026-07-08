"""POST /anthropic/v1/messages and /v1/messages — Anthropic Messages API proxy to DeepSeek.
Anthropic-format requests are forwarded to DeepSeek's /anthropic endpoint with
model ID masquerade: claude-* ↔ deepseek-* names, with thinking always enabled
and effort forced to xhigh.
"""

import json
import os as _os
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from .config import load_config, MAX_TOOL_RESULT_CHARS, MAX_OUTPUT_TOKENS
from .mapper import get_mapper
from .logger import RequestLog, detect_client_type, rotate_log_file
from .upstream import stream_anthropic, post_anthropic_non_streaming
from .cache_prefix import inject_system_prefix   # backwards compat — delegates to inject_rules
from .inject_rules import verify_injection_order   # injection order verification

router = APIRouter()

# Anthropic SSE events that carry a "model" field (need masquerade on response)
ANTHROPIC_MODEL_EVENTS = {"message_start"}


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
    _debug_log = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'debug_requests.log')
    rotate_log_file(_debug_log)
    try:
        with open(_debug_log, 'a', encoding='utf-8') as _f:
            _f.write(f"\n[ANTHROPIC] model={client_model} -> {upstream_model} stream={body.get('stream')} max_tokens={body.get('max_tokens')} msgs={len(body.get('messages',[]))}\n")
            _f.write(f"  thinking={body.get('thinking','N/A')} budget_tokens={body.get('budget_tokens','N/A')}\n")
    except Exception: pass

    body["model"] = upstream_model
    rlog.model = client_model
    rlog.streaming = body.get("stream", False)

    _clean_anthropic_body(body)

    # Debug: log system field prefix + message count + token estimate + injection order
    system_raw = str(body.get("system", "N/A"))
    system_preview = system_raw[:10000].replace("\n", "\\n")
    system_chars = len(system_raw)
    msg_chars = len(str(body.get("messages", [])))
    total_est = int(system_chars * 0.35 + msg_chars * 0.3)

    ok, inject_details = verify_injection_order(body.get("system"))
    inject_status = "OK" if ok else f"MISMATCH: {inject_details}"

    try:
        with open(_debug_log, 'a', encoding='utf-8') as _f:
            _f.write(f"  system[:10000]={system_preview}\n")
            _f.write(f"  system_chars={system_chars:,} msgs_chars={msg_chars:,} est_total_tokens={total_est:,}\n")
            _f.write(f"  inject_order={inject_status}\n")
            _f.write(f"  msgs={len(body.get('messages',[]))} messages[0].role={body.get('messages', [{}])[0].get('role', 'N/A') if body.get('messages') else 'none'}\n")
    except Exception: pass

    if body.get("stream"):
        try:
            upstream_resp = await stream_anthropic(body, f"{config.anthropic_endpoint}/v1/messages", config.deepseek_api_key)
            rlog.finish(upstream_resp.status_code)
        except Exception as _e:
            try:
                with open(_debug_log, 'a', encoding='utf-8') as _f:
                    _f.write(f"  ANTHROPIC UPSTREAM ERROR: {_e}\n")
            except Exception: pass
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
            except Exception: pass
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
    """Apply safe limits, enforce max thinking effort, fix sub-agent conflicts,
    and inject stable KV-cache prefix into the system field.

    NOTE: mutates body dict in-place.
    """
    if body.get("max_tokens", 0) > MAX_OUTPUT_TOKENS:
        body["max_tokens"] = MAX_OUTPUT_TOKENS

    _ensure_thinking_enabled(body)
    _enforce_max_effort(body)

    # Inject stable anchors + all rule files into the system field so EVERY
    # request (main agent + sub-agents) shares the same prefix for KV-cache.
    body["system"] = inject_system_prefix(body.get("system"))

    _truncate_tool_results(body.get("messages", []))


def _enforce_max_effort(body: dict):
    """Always set output_config.effort to max for Anthropic requests."""
    output_config = body.get("output_config")
    if not isinstance(output_config, dict):
        output_config = {}
    output_config["effort"] = "xhigh"
    body["output_config"] = output_config


def _ensure_thinking_enabled(body: dict):
    """Force thinking to enabled on every request.

    DeepSeek V4 ships with thinking enabled by default. This function
    catches any client-side disable (Claude Code, OpenCode, etc.) and
    re-enables it — DeepSeek's /anthropic endpoint requires thinking on
    when reasoning_effort is set.
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
