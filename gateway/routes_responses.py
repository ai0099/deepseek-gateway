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
    chat_req, response_id = _translator.translate_request(body)
    rlog.model = chat_req.get("model", "-")
    rlog.streaming = chat_req.get("stream", False)

    if chat_req.get("stream"):
        upstream_resp = await stream_chat_completions(
            chat_req, config.chat_completions_endpoint, config.deepseek_api_key
        )
        rlog.finish(upstream_resp.status_code)

        from .sse_transcoder import SSETranscoder
        transcoder = SSETranscoder(body.get("model", "gpt-4o"))

        return StreamingResponse(
            transcoder.transcode_stream(upstream_resp),
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
            upstream_json, body, body.get("model", "gpt-4o"), response_id
        )
        rlog.finish(200)
        return JSONResponse(result)


@router.get("/v1/responses/{response_id}")
async def get_response(response_id: str):
    cached = _translator.cache.lookup(response_id)
    if cached:
        return JSONResponse({"id": response_id, "status": "completed", "cached": True})
    return JSONResponse({"error": "not found"}, status_code=404)
