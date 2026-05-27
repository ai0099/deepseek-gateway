"""Protocol translation: OpenAI Responses API ↔ Chat Completions API.
Non-streaming path. For streaming see sse_transcoder.py.
"""

import uuid
from .mapper import get_mapper


class ResponseCache:
    """In-memory LRU cache for previous_response_id lookups."""
    def __init__(self, max_entries: int = 100):
        self._cache: dict[str, dict] = {}
        self._order: list[str] = []
        self._max = max_entries

    def store(self, response_id: str, messages: list[dict], model: str, usage: dict):
        if len(self._order) >= self._max:
            oldest = self._order.pop(0)
            self._cache.pop(oldest, None)
        self._cache[response_id] = {"messages": messages, "model": model, "usage": usage}
        self._order.append(response_id)

    def lookup(self, response_id: str) -> dict | None:
        return self._cache.get(response_id)


class ResponsesTranslator:
    def __init__(self):
        self._mapper = get_mapper()
        self.cache = ResponseCache(max_entries=200)

    # ── Request: Responses API → Chat Completions ──

    def translate_request(self, req: dict) -> tuple[dict, str]:
        """Translate a Responses API request dict to Chat Completions request dict.
        Returns (chat_req_body, response_id_for_tracking).
        """
        response_id = f"resp_{uuid.uuid4().hex[:12]}"
        messages: list[dict] = []

        # instructions → system message (prepended)
        if req.get("instructions"):
            messages.append({"role": "system", "content": req["instructions"]})

        # previous_response_id → recover history
        prev_id = req.get("previous_response_id")
        if prev_id:
            cached = self.cache.lookup(prev_id)
            if cached:
                messages = cached["messages"] + messages

        # input items → messages
        input_data = req.get("input", [])
        if isinstance(input_data, str):
            messages.append({"role": "user", "content": input_data})
        elif isinstance(input_data, list):
            for item in input_data:
                msg = self._convert_input_item(item)
                if msg:
                    messages.append(msg)

        # tools → Chat Completions tools (filter function type only)
        tools = self._convert_tools(req.get("tools"))

        # model resolution
        client_model = req.get("model") or "gpt-4o"
        upstream_model = self._mapper.resolve_responses(client_model)

        chat_req = {
            "model": upstream_model,
            "messages": messages,
            "stream": req.get("stream", False),
            # Enable DeepSeek thinking/reasoning mode
            "thinking": {"type": "enabled"},
        }
        if tools:
            chat_req["tools"] = tools
        if req.get("tool_choice"):
            chat_req["tool_choice"] = req["tool_choice"]
        if req.get("temperature") is not None:
            chat_req["temperature"] = req["temperature"]
        if req.get("max_output_tokens"):
            chat_req["max_tokens"] = req["max_output_tokens"]
        if req.get("top_p") is not None:
            chat_req["top_p"] = req["top_p"]

        return chat_req, response_id

    # ── Response: Chat Completions → Responses API ──

    def translate_nonstreaming_response(
        self, chat_resp: dict, req_body: dict, model: str, response_id: str
    ) -> dict:
        """Translate Chat Completions response → Responses API format.
        req_body is the original Responses API request (used for caching)."""
        choice = (chat_resp.get("choices") or [{}])[0]
        message = choice.get("message", {})
        output: list[dict] = []

        # reasoning_content → reasoning output item
        reasoning = message.get("reasoning_content", "")
        if reasoning:
            rs_id = f"rs_{uuid.uuid4().hex[:8]}"
            output.append({
                "id": rs_id,
                "type": "reasoning",
                "status": "completed",
                "summary": [{"type": "summary_text", "text": reasoning}],
                "encrypted_content": reasoning,
            })

        # text content → message output item
        content_text = message.get("content")
        if content_text:
            msg_id = f"msg_{uuid.uuid4().hex[:8]}"
            output.append({
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": content_text,
                    "annotations": [],
                }],
            })

        # tool_calls → function_call output items
        for tc in message.get("tool_calls") or []:
            func = tc.get("function", {})
            output.append({
                "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function_call",
                "call_id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": func.get("arguments", ""),
                "status": "completed",
            })

        # Cache for previous_response_id support
        input_items = req_body.get("input") if isinstance(req_body.get("input"), list) else []
        flat_messages = [msg for item in input_items if (msg := self._convert_input_item(item))]
        if output:
            flat_messages.append({"role": "assistant", "content": content_text, "tool_calls": message.get("tool_calls")})
        self.cache.store(response_id, flat_messages, model, chat_resp.get("usage", {}))

        return {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": model,
            "output": output,
            "usage": chat_resp.get("usage"),
        }

    # ── Helpers ──

    def _convert_input_item(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        role = item.get("role", "user")
        content = item.get("content", [])
        parts: list[dict] = []
        if isinstance(content, str):
            return {"role": role, "content": content}
        for part in (content if isinstance(content, list) else [content]):
            ptype = part.get("type", "")
            if ptype in ("input_text", "output_text"):
                parts.append({"type": "text", "text": part.get("text", "")})
            elif ptype == "input_image":
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": part.get("image_url", ""), "detail": part.get("detail", "auto")},
                })
            elif ptype == "refusal":
                parts.append({"type": "text", "text": f"[refusal] {part.get('refusal', '')}"})
            elif ptype == "text":
                parts.append(part)
        if not parts:
            return None
        return {"role": role, "content": parts[0]["text"] if len(parts) == 1 and parts[0]["type"] == "text" else parts}

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        result = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            ttype = tool.get("type", "")
            if ttype == "function":
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    }
                })
            elif ttype == "custom":
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": {"type": "object", "properties": {"input": {"type": "string"}}},
                    }
                })
        return result or None
