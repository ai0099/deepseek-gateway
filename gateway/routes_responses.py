"""POST /v1/responses — translate OpenAI Responses API to DeepSeek Chat Completions.
Streaming and non-streaming paths.
"""

import json
import logging
import os as _os
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from .config import load_config
from .logger import RequestLog, detect_client_type, rotate_log_file, trim_debug_log
from .translator import ResponsesTranslator
from .upstream import stream_chat_completions, post_non_streaming

router = APIRouter()
_translator = ResponsesTranslator()


@router.api_route("/v1/responses", methods=["POST", "OPTIONS"])
async def proxy_responses(request: Request):
    if request.method == "OPTIONS":
        return JSONResponse({"ok": True}, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        })

    config = load_config()
    rlog = RequestLog("POST", "/v1/responses", detect_client_type(request))

    try:
        body = await request.json()
    except Exception as _e:
        return JSONResponse({"error": {"message": str(_e)}}, status_code=400)


    chat_req, response_id, use_beta = _translator.translate_request(body)

    rlog.model = chat_req.get("model", "-")
    rlog.streaming = chat_req.get("stream", False)

    # ── Injection order verification (always runs) ──
    client_model = body.get("model", "?")
    upstream_model = chat_req.get("model", "?")
    msgs = chat_req.get("messages", [])
    # Find first system message content for injection verification
    sys_content = ""
    for m in msgs:
        if m.get("role") == "system":
            sys_content = str(m.get("content", ""))
            break
    system_chars = len(sys_content)
    msg_chars = len(str(msgs))
    total_est = int(system_chars * 0.35 + msg_chars * 0.3)

    # Verify injection order (check anchor hash in first system message)
    from .inject_rules import _ANCHOR_SHA256 as _ANCHOR_SHA, _ANCHOR_LENGTH
    import hashlib as _hashlib
    anchor_ok = False
    if len(sys_content) >= _ANCHOR_LENGTH:
        actual_sha = _hashlib.sha256(sys_content[:_ANCHOR_LENGTH].encode("utf-8")).hexdigest()
        anchor_ok = (actual_sha == _ANCHOR_SHA)
    inject_status = "OK" if anchor_ok else "MISMATCH"
    if not anchor_ok:
        _log = logging.getLogger("gateway")
        _log.error("INJECTION MISMATCH on /v1/responses: anchor SHA256 differs! Expected %s..., got %s...",
                   _ANCHOR_SHA[:16], actual_sha[:16] if len(sys_content) >= _ANCHOR_LENGTH else "N/A")

    # Debug: log chat request info + token estimate
    _debug_log = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'debug_requests.log')
    rotate_log_file(_debug_log)
    try:
        system_preview = sys_content[:300].replace("\n", "\\n")
        with open(_debug_log, 'a', encoding='utf-8') as _f:
            _f.write(f"\n[RESPONSES] model={client_model} -> {upstream_model} stream={chat_req.get('stream')} msgs={len(msgs)}\n")
            _f.write(f"  UA: {(request.headers.get('user-agent') or 'none')[:200]}\n")
            _f.write(f"  system_preview={system_preview}\n")
            _f.write(f"  system_chars={system_chars:,} msg_chars={msg_chars:,} est_total_tokens={total_est:,}\n")
            _f.write(f"  inject_order={inject_status}\n")
    except Exception: pass

    _log = logging.getLogger("gateway")
    _log.info("Responses req: model=%s stream=%s tools=%s input_items=%s msgs=%s",
              body.get("model"), body.get("stream"),
              len(body.get("tools") or []), len(body.get("input") or []),
              len(chat_req.get("messages") or []))

    try:
        _stream_mode = chat_req.get("stream")
    except Exception as _e:
        return JSONResponse({"error": {"message": str(_e)}}, status_code=500)

    if _stream_mode:
        try:
            upstream_resp = await stream_chat_completions(
                chat_req, config.beta_chat_completions_endpoint if use_beta else config.chat_completions_endpoint, config.deepseek_api_key
            )
            rlog.status = upstream_resp.status_code  # defer finish until usage available
        except Exception as e:
            rlog.finish(500)
            return JSONResponse({
                "error": {"message": f"Upstream connection failed: {str(e)}", "type": "upstream_error"},
            }, status_code=502)

        from .sse_transcoder import SSETranscoder
        transcoder = SSETranscoder(body.get("model", "gpt-5.5"), response_id, body)

        async def cached_stream():
            # Buffer events to handle web_search tool call interception
            _events: list[str] = []
            try:
                async for event in transcoder.transcode_stream(upstream_resp):
                    _events.append(event)
            except Exception as _e:
                _events.append(transcoder._sse_event("response.failed", {
                    "type": "response.failed",
                    "response": {"id": transcoder._response_id, "status": "failed",
                    "error": {"message": str(_e)}},
                }))
                for ev in _events:
                    yield ev
                return

            # --- web_search interception ---
            if transcoder.web_search_calls:
                import json as _json, logging as _logging
                _ws_log = _logging.getLogger("gateway.web_search")
                _ws_log.info("Intercepted %d web_search call(s)", len(transcoder.web_search_calls))

                # Execute searches
                msgs = list(chat_req.get("messages", []))
                assistant_msg = {"role": "assistant", "content": None,
                                 "tool_calls": transcoder.web_search_calls}
                if transcoder.full_reasoning:
                    assistant_msg["reasoning_content"] = transcoder.full_reasoning
                msgs.append(assistant_msg)

                for tc in transcoder.web_search_calls:
                    try:
                        args = _json.loads(tc["function"]["arguments"])
                    except Exception:
                        args = {}
                    query = args.get("query", "") or args.get("q", "")
                    try:
                        from duckduckgo_search import DDGS
                        with DDGS() as ddgs:
                            results = list(ddgs.text(query, max_results=5))
                        formatted = []
                        for r in results:
                            formatted.append({"title": r.get("title", ""),
                                              "url": r.get("href", ""),
                                              "snippet": r.get("body", "")})
                        result_str = _json.dumps({"query": query, "results": formatted},
                                                 ensure_ascii=False, indent=2)
                    except Exception as _se:
                        result_str = _json.dumps({"query": query, "error": str(_se)},
                                                 ensure_ascii=False)
                    msgs.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": result_str})
                    _ws_log.info("Search completed: '%s'", query)

                # Second-round request to DeepSeek with search results
                chat_req2 = dict(chat_req)
                chat_req2["messages"] = msgs
                chat_req2["stream"] = True

                try:
                    upstream_resp2 = await stream_chat_completions(
                        chat_req2, config.beta_chat_completions_endpoint if use_beta else config.chat_completions_endpoint,
                        config.deepseek_api_key
                    )
                    transcoder2 = SSETranscoder(
                        body.get("model", "gpt-5.5"), response_id, body
                    )
                    async for event in transcoder2.transcode_stream(upstream_resp2):
                        yield event
                except Exception as _re:
                    _ws_log.error("Second-round request failed: %s", _re)
                    yield transcoder._sse_event("response.failed", {
                        "type": "response.failed",
                        "response": {"id": response_id, "status": "failed",
                        "error": {"message": f"Search round failed: {_re}"}},
                    })
                return

            # Normal path (no web_search): yield buffered events
            for ev in _events:
                yield ev

            # Cache the completed response for previous_response_id support
            msgs = list(chat_req.get("messages", []))
            assistant_msg = {"role": "assistant", "content": transcoder.full_text}
            if transcoder.full_reasoning:
                assistant_msg["reasoning_content"] = transcoder.full_reasoning
            if transcoder.full_tool_calls:
                assistant_msg["tool_calls"] = transcoder.full_tool_calls
            msgs.append(assistant_msg)
            _translator.cache.store(response_id, msgs, body.get("model", "gpt-5.5"), transcoder.usage)
            # Record token usage
            if transcoder.usage is not None:
                try:
                    rlog.finish(rlog.status, transcoder.usage)
                except Exception:
                    pass
                # Debug: log cache performance
                try:
                    usage = transcoder.usage
                    cache_hit = usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0)
                    cache_miss = usage.get("prompt_cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0)
                    total_in = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                    total_out = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                    cache_msg = (
                        f"  responses_usage: in={total_in/1e3:.1f}K out={total_out/1e3:.1f}K | "
                        f"cache_hit={cache_hit/1e3:.1f}K cache_miss={cache_miss/1e3:.1f}K"
                    )
                    with open(_debug_log, 'a', encoding='utf-8') as _f:
                        _f.write(cache_msg + "\n")
                    trim_debug_log(_debug_log, keep_requests=50)
                except Exception: pass

        return StreamingResponse(
            cached_stream(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
            },
        )
    else:
        try:
            upstream_json = await post_non_streaming(
                chat_req, config.beta_chat_completions_endpoint if use_beta else config.chat_completions_endpoint, config.deepseek_api_key
            )
        except Exception as e:
            error_msg = str(e)
            rlog.finish(502)
            return JSONResponse({
                "error": {
                    "message": f"Upstream error: {error_msg}",
                    "type": "upstream_error",
                },
            }, status_code=502)

        result = _translator.translate_nonstreaming_response(
            upstream_json, body, body.get("model", "gpt-5.5"), response_id
        )
        rlog.finish(200, _translator.last_usage if hasattr(_translator, 'last_usage') else None)
        # Debug: log cache performance for non-streaming
        try:
            usage = _translator.last_usage if hasattr(_translator, 'last_usage') else {}
            if usage:
                cache_hit = usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0)
                cache_miss = usage.get("prompt_cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0)
                total_in = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                total_out = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                cache_msg = (
                    f"  responses_usage: in={total_in/1e3:.1f}K out={total_out/1e3:.1f}K | "
                    f"cache_hit={cache_hit/1e3:.1f}K cache_miss={cache_miss/1e3:.1f}K"
                )
            with open(_debug_log, 'a', encoding='utf-8') as _f:
                _f.write(cache_msg + "\n")
        except Exception: pass
        trim_debug_log(_debug_log, keep_requests=50)
        return JSONResponse(result)


@router.get("/v1/responses/{response_id}")
async def get_response(response_id: str):
    cached = _translator.cache.lookup(response_id)
    if cached:
        return JSONResponse({
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": cached.get("model", "gpt-5.5"),
            "output": [],
            "usage": None,
        })
    return JSONResponse({"id": response_id, "object": "response", "status": "completed",
                         "model": "gpt-5.5", "output": [], "usage": None})
