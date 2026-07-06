"""SSE state machine: Chat Completions SSE chunks → Responses API SSE events.

Chat Completions SSE format:
  data: {"id":"...","choices":[{"delta":{"role":"assistant","content":"..."},"index":0}]}

Responses API SSE format:
  event: response.output_text.delta
  data: {"type":"response.output_text.delta","delta":"...","item_id":"...","content_index":0}
"""

import json
import uuid
import time


class SSETranscoder:
    def __init__(self, client_model: str, response_id: str = "", req_body: dict | None = None):
        self._model = client_model
        self._response_id = response_id or f"resp_{uuid.uuid4().hex[:12]}"
        self._req_body = req_body or {}
        self._reset()
        # Accumulated full response for cross-turn caching
        self.full_text = ""
        self.full_reasoning = ""
        self.full_tool_calls: list[dict] = []
        self.usage: dict = {}
        self.web_search_calls: list[dict] = []

    def _reset(self):
        self._msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        self._rs_id = f"rs_{uuid.uuid4().hex[:8]}"
        self._content_index = 0
        self._text_buffer = ""
        self._state = "INITIAL"
        self._sent_created = False
        self._sent_message_item = False
        self._sent_content_part = False
        self._current_reasoning = ""
        self._tool_calls: list[dict] = []
        self._finish_reason = ""
        self._usage: dict = {}

    async def transcode_stream(self, upstream_resp):
        """Read Chat Completions SSE from upstream, yield Responses API SSE events."""
        # Check for upstream HTTP errors before streaming
        if upstream_resp.status_code >= 400:
            try:
                error_body = await upstream_resp.aread()
                error_text = error_body.decode("utf-8", errors="replace")[:500]
            except Exception:
                error_text = f"HTTP {upstream_resp.status_code}"
            yield self._sse_event("response.failed", {
                "type": "response.failed",
                "response": {
                    "id": self._response_id,
                    "status": "failed",
                    "error": {"message": error_text},
                },
            })
            return

        try:
            async for line in upstream_resp.aiter_lines():
                if not line.startswith("data: ") or len(line) < 7:
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    for event in self._handle_done():
                        yield event
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                # Log raw chunk for debugging usage/cache hit data
                try:
                    import os as _os2
                    _raw_chunk_log = _os2.path.join(_os2.path.dirname(_os2.path.dirname(__file__)), 'raw_chunks.log')
                    # Only log chunks with usage or specific patterns
                    if "usage" in chunk or "finish_reason" in str(chunk)[:200]:
                        with open(_raw_chunk_log, 'a', encoding='utf-8') as _rf:
                            import json as _json2
                            _rf.write(_json2.dumps(chunk, ensure_ascii=False) + '\n')
                except: pass
                for event in self._process_chunk(chunk):
                    yield event
        except Exception as e:
            yield self._sse_event("response.failed", {
                "type": "response.failed",
                "response": {
                    "id": self._response_id,
                    "status": "failed",
                    "error": {"message": str(e)},
                },
            })

        # Always emit completed if not already done (stream dropped, etc.)
        if self._state != "COMPLETED":
            if not self._finish_reason:
                self._finish_reason = "stop"
            for event in self._emit_finish():
                yield event

    def _process_chunk(self, chunk: dict) -> list[str]:
        """Dispatch one Chat Completions chunk to the right handler. Returns list of SSE strings."""
        choices = chunk.get("choices") or []
        # Capture usage from any chunk (DeepSeek includes it in every chunk with stream_options)
        if "usage" in chunk:
            self._usage = chunk["usage"]
            self.usage = chunk["usage"]
        if not choices:
            return []

        delta = choices[0].get("delta", {})
        finish_reason = choices[0].get("finish_reason") or ""
        if finish_reason:
            self._finish_reason = finish_reason

        events: list[str] = []

        if delta.get("role") == "assistant" and not self._sent_created:
            events.extend(self._emit_response_start())

        reasoning = delta.get("reasoning_content", "")
        if reasoning:
            events.extend(self._emit_reasoning(reasoning))
            self.full_reasoning += reasoning

        content = delta.get("content")
        if content:
            if not self._sent_message_item:
                events.extend(self._emit_message_item())
            if not self._sent_content_part:
                events.extend(self._emit_content_part("output_text"))
            self._text_buffer += content
            self.full_text += content
            events.append(self._sse_event("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": self._msg_id,
                "content_index": self._content_index,
                "delta": content,
            }))

        tc_deltas = delta.get("tool_calls")
        if tc_deltas:
            events.extend(self._emit_tool_calls(tc_deltas))

        if finish_reason:
            events.extend(self._emit_finish())

        return events

    def _emit_response_start(self) -> list[str]:
        self._sent_created = True
        self._state = "RESPONSE_STARTED"
        now = int(time.time())
        return [
            self._sse_event("response.created", {
                "type": "response.created",
                "response": {
                    "id": self._response_id, "object": "response",
                    "status": "in_progress", "model": self._model,
                    "output": [], "usage": None, "created_at": now,
                },
            }),
            self._sse_event("response.in_progress", {
                "type": "response.in_progress",
                "response": {"id": self._response_id, "object": "response", "status": "in_progress"},
            }),
        ]

    def _emit_message_item(self) -> list[str]:
        self._sent_message_item = True
        self._state = "MESSAGE_OPEN"
        self._content_index = 0
        return [
            self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "item": {
                    "id": self._msg_id, "type": "message",
                    "role": "assistant", "status": "in_progress", "content": [],
                },
            }),
        ]

    def _emit_content_part(self, part_type: str = "output_text") -> list[str]:
        self._sent_content_part = True
        self._state = "CONTENT_PART_OPEN"
        ptype = "output_text" if part_type == "output_text" else "reasoning_summary_text"
        return [
            self._sse_event("response.content_part.added", {
                "type": "response.content_part.added",
                "part": {"type": ptype, "text": ""},
            }),
        ]

    def _emit_reasoning(self, reasoning: str) -> list[str]:
        events: list[str] = []
        if not self._current_reasoning:
            self._rs_id = f"rs_{uuid.uuid4().hex[:8]}"
            events.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "item": {"id": self._rs_id, "type": "reasoning", "status": "in_progress"},
            }))
            events.append(self._sse_event("response.content_part.added", {
                "type": "response.content_part.added",
                "part": {"type": "reasoning_summary_text", "text": ""},
            }))
        self._current_reasoning += reasoning
        events.append(self._sse_event("response.reasoning_summary_text.delta", {
            "type": "response.reasoning_summary_text.delta",
            "item_id": self._rs_id, "content_index": 0, "delta": reasoning,
        }))
        return events

    def _emit_tool_calls(self, tc_deltas: list[dict]) -> list[str]:
        events: list[str] = []
        if self._state == "CONTENT_PART_OPEN":
            events.extend([
                self._sse_event("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": self._msg_id, "content_index": self._content_index,
                    "text": self._text_buffer,
                }),
                self._sse_event("response.content_part.done", {
                    "type": "response.content_part.done",
                    "item_id": self._msg_id, "content_index": self._content_index,
                    "part": {"type": "output_text", "text": self._text_buffer},
                }),
                self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item": {
                        "id": self._msg_id, "type": "message", "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": self._text_buffer, "annotations": []}],
                    },
                }),
            ])

        for tc_delta in tc_deltas:
            idx = tc_delta.get("index", 0)
            while len(self._tool_calls) <= idx:
                self._tool_calls.append({"id": "", "name": "", "arguments": ""})
            while len(self.full_tool_calls) <= idx:
                self.full_tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if "id" in tc_delta and tc_delta["id"]:
                self._tool_calls[idx]["id"] = tc_delta["id"]
                self.full_tool_calls[idx]["id"] = tc_delta["id"]
            func = tc_delta.get("function", {})
            if "name" in func and func["name"]:
                self._tool_calls[idx]["name"] = func["name"]
                self.full_tool_calls[idx]["function"]["name"] = func["name"]
                call_id = self._tool_calls[idx]["id"] or f"call_{uuid.uuid4().hex[:8]}"
                if func["name"] == "web_search":
                    continue
                events.append(self._sse_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "item": {
                        "id": call_id, "type": "function_call",
                        "name": func["name"], "call_id": call_id,
                        "arguments": "", "status": "in_progress",
                    },
                }))
            if "arguments" in func:
                self._tool_calls[idx]["arguments"] += func["arguments"]
                tc_name = self._tool_calls[idx].get("name", "")
                if tc_name == "web_search":
                    continue
                self.full_tool_calls[idx]["function"]["arguments"] += func["arguments"]
                call_id = self._tool_calls[idx]["id"] or f"call_{uuid.uuid4().hex[:8]}"
                events.append(self._sse_event("response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta",
                    "item_id": call_id, "delta": func["arguments"],
                }))

        self.web_search_calls = [
            {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in self._tool_calls if tc["name"] == "web_search"
        ]
        self._state = "FUNCTION_CALL_OPEN"
        return events

    def _emit_finish(self) -> list[str]:
        events: list[str] = []

        if self._state == "CONTENT_PART_OPEN":
            events.extend([
                self._sse_event("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": self._msg_id, "content_index": self._content_index,
                    "text": self._text_buffer,
                }),
                self._sse_event("response.content_part.done", {
                    "type": "response.content_part.done",
                    "item_id": self._msg_id, "content_index": self._content_index,
                    "part": {"type": "output_text", "text": self._text_buffer},
                }),
                self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item": {
                        "id": self._msg_id, "type": "message", "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": self._text_buffer, "annotations": []}],
                    },
                }),
            ])

        if self._current_reasoning:
            events.extend([
                self._sse_event("response.reasoning_summary_text.done", {
                    "type": "response.reasoning_summary_text.done",
                    "item_id": self._rs_id, "content_index": 0,
                    "text": self._current_reasoning,
                }),
                self._sse_event("response.content_part.done", {
                    "type": "response.content_part.done",
                    "item_id": self._rs_id, "content_index": 0,
                    "part": {"type": "reasoning_summary_text", "text": self._current_reasoning},
                }),
                self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item": {
                        "id": self._rs_id, "type": "reasoning", "status": "completed",
                        "summary": [{"type": "summary_text", "text": self._current_reasoning}],
                        "encrypted_content": self._current_reasoning,
                    },
                }),
            ])

        for tc in self._tool_calls:
            call_id = tc["id"] or f"call_{uuid.uuid4().hex[:8]}"
            events.extend([
                self._sse_event("response.function_call_arguments.done", {
                    "type": "response.function_call_arguments.done",
                    "item_id": call_id, "arguments": tc["arguments"],
                }),
                self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item": {
                        "id": call_id, "type": "function_call",
                        "name": tc["name"], "call_id": call_id,
                        "arguments": tc["arguments"], "status": "completed",
                    },
                }),
            ])

        # Build output items for response.completed
        output_items = []
        if self._current_reasoning:
            output_items.append({
                "id": self._rs_id, "type": "reasoning", "status": "completed",
                "summary": [{"type": "summary_text", "text": self._current_reasoning}],
                "encrypted_content": self._current_reasoning,
            })
        if self._text_buffer:
            output_items.append({
                "id": self._msg_id, "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": self._text_buffer, "annotations": []}],
            })
        for tc in self._tool_calls:
            output_items.append({
                "id": tc["id"], "type": "function_call",
                "call_id": tc["id"], "name": tc["name"],
                "arguments": tc["arguments"], "status": "completed",
            })

        self._state = "COMPLETED"
        res_obj = {
            "id": self._response_id,
            "object": "response",
            "status": "completed" if self._finish_reason != "length" else "incomplete",
            "model": self._model,
            "output": output_items,
            "usage": self._map_usage_sse(self.usage),
            "parallel_tool_calls": self._req_body.get("parallel_tool_calls", True),
            "tool_choice": self._req_body.get("tool_choice", "auto"),
            "reasoning": self._req_body.get("reasoning", {"effort": None, "summary": None}),
            "text": self._req_body.get("text", {"format": {"type": "text"}}),
            "incomplete_details": {"reason": "max_output_tokens"} if self._finish_reason == "length" else None,
            "error": None,
            "metadata": self._req_body.get("metadata", {}),
            "previous_response_id": self._req_body.get("previous_response_id"),
            "instructions": self._req_body.get("instructions"),
            "temperature": self._req_body.get("temperature"),
            "top_p": self._req_body.get("top_p"),
            "max_output_tokens": self._req_body.get("max_output_tokens"),
            "tools": self._req_body.get("tools", []),
            "truncation": "disabled",
        }
        events.append(self._sse_event("response.completed", {
            "type": "response.completed",
            "response": res_obj,
        }))
        return events

    def _handle_done(self) -> list[str]:
        if self._state != "COMPLETED":
            if not self._finish_reason:
                self._finish_reason = "stop"
            return self._emit_finish()
        return []

    def _map_usage_sse(self, usage: dict | None) -> dict | None:
        if not usage:
            return None
        result = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        if "prompt_tokens_details" in usage:
            result["input_tokens_details"] = usage["prompt_tokens_details"]
        if "completion_tokens_details" in usage:
            result["output_tokens_details"] = usage["completion_tokens_details"]
        return result

    def _sse_event(self, event_type: str, data: dict) -> str:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event_type}\ndata: {payload}\n\n"
