"""POST /v1/responses — translate OpenAI Responses API to DeepSeek Chat Completions.
Streaming and non-streaming paths.
"""

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from .config import load_config
from .translator import ResponsesTranslator
from .upstream import stream_chat_completions, post_non_streaming
from .logger import RequestLog, detect_client_type

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

    body = await request.json()

    # DEBUG: Log full request to file
    import json as _json, os as _os
    _debug_log = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'debug_requests.log')
    try:
        with open(_debug_log, 'a', encoding='utf-8') as _f:
            _f.write(f"\n{'='*60}\n")
            _f.write(f"TIME: {__import__('datetime').datetime.now().isoformat()}\n")
            _f.write(f"REQUEST:\n{_json.dumps(body, ensure_ascii=False, indent=2)[:8000]}\n")
    except: pass

    chat_req, response_id = _translator.translate_request(body)

    # Log translated Chat Completions request
    try:
        with open(_debug_log, 'a', encoding='utf-8') as _f:
            _f.write(f"TRANSLATED:\n{_json.dumps(chat_req, ensure_ascii=False, indent=2)[:8000]}\n")
    except: pass

    rlog.model = chat_req.get("model", "-")
    rlog.streaming = chat_req.get("stream", False)
    import logging
    _log = logging.getLogger("gateway")
    _log.info("Responses req: model=%s stream=%s tools=%s input_items=%s msgs=%s",
              body.get("model"), body.get("stream"),
              len(body.get("tools") or []), len(body.get("input") or []),
              len(chat_req.get("messages") or []))

    if chat_req.get("stream"):
        try:
            upstream_resp = await stream_chat_completions(
                chat_req, config.chat_completions_endpoint, config.deepseek_api_key
            )
            rlog.finish(upstream_resp.status_code)
        except Exception as e:
            rlog.finish(500)
            return JSONResponse({
                "error": {"message": f"Upstream connection failed: {str(e)}", "type": "upstream_error"},
            }, status_code=502)

        from .sse_transcoder import SSETranscoder
        transcoder = SSETranscoder(body.get("model", "gpt-5.5"), response_id, body)

        async def cached_stream():
            try:
                async for event in transcoder.transcode_stream(upstream_resp):
                    yield event
            except Exception as _e:
                # Log streaming error
                try:
                    with open(_debug_log, 'a', encoding='utf-8') as _f:
                        _f.write(f"STREAM ERROR: {_e}\n")
                        import traceback as _tb
                        _tb.print_exc(file=_f)
                except: pass
                yield transcoder._sse_event("response.failed", {
                    "type": "response.failed",
                    "response": {"id": transcoder._response_id, "status": "failed",
                    "error": {"message": str(_e)}},
                })
            # Cache the completed response for previous_response_id support
            msgs = list(chat_req.get("messages", []))
            assistant_msg = {"role": "assistant", "content": transcoder.full_text}
            if transcoder.full_reasoning:
                assistant_msg["reasoning_content"] = transcoder.full_reasoning
            if transcoder.full_tool_calls:
                assistant_msg["tool_calls"] = transcoder.full_tool_calls
            msgs.append(assistant_msg)
            _translator.cache.store(response_id, msgs, body.get("model", "gpt-5.5"), transcoder.usage)

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
        upstream_json = await post_non_streaming(
            chat_req, config.chat_completions_endpoint, config.deepseek_api_key
        )
        result = _translator.translate_nonstreaming_response(
            upstream_json, body, body.get("model", "gpt-5.5"), response_id
        )
        rlog.finish(200)
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
