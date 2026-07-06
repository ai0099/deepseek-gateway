"""Protocol translation: OpenAI Responses API ↔ Chat Completions API.
Non-streaming path. For streaming see sse_transcoder.py.
"""

import uuid
from .mapper import get_mapper
from .cache_prefix import inject_prefix_chat


class ResponseCache:
    """In-memory LRU cache for previous_response_id lookups. Uses OrderedDict for O(1) eviction."""
    def __init__(self, max_entries: int = 100):
        self._cache: "OrderedDict[str, dict]" = __import__("collections").OrderedDict()
        self._max = max_entries

    def store(self, response_id: str, messages: list[dict], model: str, usage: dict):
        if response_id in self._cache:
            del self._cache[response_id]
        elif len(self._cache) >= self._max:
            self._cache.popitem(last=False)
        self._cache[response_id] = {"messages": messages, "model": model, "usage": usage}

    def lookup(self, response_id: str) -> dict | None:
        return self._cache.get(response_id)


class ResponsesTranslator:
    def __init__(self):
        self._mapper = get_mapper()
        self.cache = ResponseCache(max_entries=200)

    # ── Request: Responses API → Chat Completions ──

    def translate_request(self, req: dict) -> tuple[dict, str, bool]:
        """Translate a Responses API request dict to Chat Completions request dict.
        Returns (chat_req_body, response_id_for_tracking, use_beta_endpoint).
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
            pending_reasoning = ""
            for item in input_data:
                msg = self._convert_input_item(item)
                if msg is None:
                    # Track reasoning content for cross-turn continuity
                    if isinstance(item, dict) and item.get("type") == "reasoning":
                        pending_reasoning = item.get("encrypted_content", "") or "".join(
                            s.get("text", "") for s in (item.get("summary") or [])
                        )
                    continue
                # Attach pending reasoning to assistant messages with tool_calls
                if pending_reasoning and msg.get("role") == "assistant" and msg.get("tool_calls"):
                    msg["reasoning_content"] = pending_reasoning
                    pending_reasoning = ""
                # Attach pending reasoning to the last assistant message if it's the first user/tool msg
                if pending_reasoning and msg.get("role") in ("user", "tool"):
                    # Inject reasoning into the previous assistant message in the list
                    for m in reversed(messages):
                        if m.get("role") == "assistant":
                            m["reasoning_content"] = pending_reasoning
                            break
                    pending_reasoning = ""
                messages.append(msg)

        # tools → Chat Completions tools (filter function type only)
        tools = self._convert_tools(req.get("tools"))

        # model resolution
        client_model = req.get("model") or "gpt-5.5"
        upstream_model = self._mapper.resolve_responses(client_model)

        # Post-process: move system messages that interrupt tool call sequences
        messages = self._merge_consecutive_tool_calls(messages)
        messages = self._fix_tool_call_continuity(messages)

        # Inject stable AGENTS.md cache prefix for KV cache pooling across clients
        messages = inject_prefix_chat(messages)

        stream_mode = req.get("stream", False)
        chat_req = {
            "model": upstream_model,
            "messages": messages,
            "stream": stream_mode,
        }
        # Always enable DeepSeek thinking mode with max effort
        chat_req["thinking"] = {"type": "enabled"}
        chat_req["reasoning_effort"] = "max"
        # Request usage stats in streaming mode (for cache hit tracking)
        if stream_mode:
            chat_req["stream_options"] = {"include_usage": True}
        chat_req["tool_choice"] = req.get("tool_choice", "auto")
        if tools:
            chat_req["tools"] = tools
        if req.get("response_format"):
            chat_req["response_format"] = req["response_format"]
        if req.get("temperature") is not None:
            chat_req["temperature"] = req["temperature"]
        if req.get("max_output_tokens"):
            chat_req["max_tokens"] = req["max_output_tokens"]
        if req.get("top_p") is not None:
            chat_req["top_p"] = req["top_p"]

        # Determine if we need beta endpoint for strict-mode tools
        use_beta = any(t.get("strict") for t in (tools or []))

        return chat_req, response_id, use_beta

    def _merge_consecutive_tool_calls(self, messages: list[dict]) -> list[dict]:
        """Merge consecutive assistant(tool_calls) messages into one with multiple tool_calls.
        DeepSeek API rejects consecutive assistant tool_calls messages that aren't
        separated by their corresponding tool responses."""
        result: list[dict] = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                if result and result[-1].get("role") == "assistant" and result[-1].get("tool_calls"):
                    # Merge tool_calls into the previous assistant message
                    result[-1]["tool_calls"].extend(msg["tool_calls"])
                    # Merge reasoning_content if present
                    if msg.get("reasoning_content"):
                        existing = result[-1].get("reasoning_content", "")
                        result[-1]["reasoning_content"] = existing + "\n" + msg["reasoning_content"]
                    continue
            result.append(msg)
        return result

    def _fix_tool_call_continuity(self, messages: list[dict]) -> list[dict]:
        """Move system/developer messages that appear between an assistant
        with tool_calls and its tool message responses, so DeepSeek doesn't
        reject the tool call sequence."""
        result: list[dict] = []
        deferred_system: list[dict] = []
        in_tool_sequence = False

        for msg in messages:
            is_assistant_tc = msg.get("role") == "assistant" and msg.get("tool_calls")
            is_system = msg.get("role") == "system"
            is_tool = msg.get("role") == "tool"

            if is_assistant_tc:
                result.append(msg)
                in_tool_sequence = True
                deferred_system = []
                continue

            if in_tool_sequence and is_system:
                deferred_system.append(msg)
                continue

            if in_tool_sequence and is_tool:
                result.append(msg)
                continue

            # End of tool sequence or other role
            if in_tool_sequence and not is_tool:
                # Flush any deferred system messages BEFORE the tool sequence
                insert_pos = len(result)
                for idx in range(len(result) - 1, -1, -1):
                    if result[idx].get("role") == "assistant" and result[idx].get("tool_calls"):
                        insert_pos = idx
                        break
                for ds in deferred_system:
                    result.insert(insert_pos, ds)
                deferred_system = []
                in_tool_sequence = False
                result.append(msg)
                continue

            result.append(msg)

        # Flush remaining deferred at end of tool sequence
        if deferred_system:
            insert_pos = len(result)
            for idx in range(len(result) - 1, -1, -1):
                if result[idx].get("role") == "assistant" and result[idx].get("tool_calls"):
                    insert_pos = idx
                    break
            for ds in deferred_system:
                result.insert(insert_pos, ds)

        return result

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
            assistant_msg: dict = {"role": "assistant", "content": content_text}
            if message.get("tool_calls"):
                assistant_msg["tool_calls"] = message["tool_calls"]
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning
            flat_messages.append(assistant_msg)
        self.cache.store(response_id, flat_messages, model, chat_resp.get("usage", {}))

        return {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": model,
            "output": output,
            "usage": self._map_usage(chat_resp.get("usage")),
            "parallel_tool_calls": req_body.get("parallel_tool_calls", True),
            "tool_choice": req_body.get("tool_choice", "auto"),
            "reasoning": req_body.get("reasoning", {"effort": None, "summary": None}),
            "text": req_body.get("text", {"format": {"type": "text"}}),
            "incomplete_details": None,
            "error": None,
            "metadata": req_body.get("metadata", {}),
            "previous_response_id": req_body.get("previous_response_id"),
            "instructions": req_body.get("instructions"),
            "temperature": req_body.get("temperature"),
            "top_p": req_body.get("top_p"),
            "max_output_tokens": req_body.get("max_output_tokens"),
            "tools": req_body.get("tools", []),
            "truncation": "disabled",
        }

    def _map_usage(self, usage: dict | None) -> dict | None:
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

    # ── Helpers ──

    def _convert_input_item(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None

        item_type = item.get("type", "")

        # Reasoning items are handled by translate_request's cross-turn logic
        if item_type == "reasoning":
            return None

        # Responses API function_call → Chat Completions assistant message with tool_calls
        if item_type == "function_call":
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                }],
            }

        # Responses API function_call_output → Chat Completions tool message
        if item_type == "function_call_output":
            return {
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": item.get("output", ""),
            }

        role = item.get("role", "user")
        # DeepSeek Chat Completions doesn't support "developer" role
        if role == "developer":
            role = "system"

        content = item.get("content", [])
        parts: list[dict] = []
        if isinstance(content, str):
            return {"role": role, "content": content}

        tool_calls: list[dict] = []
        for part in (content if isinstance(content, list) else [content]):
            if part is None:
                continue
            ptype = part.get("type", "")
            if ptype in ("input_text", "output_text"):
                parts.append({"type": "text", "text": part.get("text", "")})
            elif ptype == "input_image":
                # DeepSeek V4 does not support image_url; replace with placeholder text
                parts.append({"type": "text", "text": "[image]"})
            elif ptype == "refusal":
                parts.append({"type": "text", "text": f"[refusal] {part.get('refusal', '')}"})
            elif ptype == "function_call":
                tool_calls.append({
                    "id": part.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": part.get("name", ""),
                        "arguments": part.get("arguments", ""),
                    },
                })
            elif ptype == "text":
                parts.append(part)

        if not parts and not tool_calls:
            return None

        msg = {"role": role}
        if tool_calls:
            msg["tool_calls"] = tool_calls
            msg["content"] = None
        elif len(parts) == 1 and parts[0]["type"] == "text":
            msg["content"] = parts[0]["text"]
        elif parts:
            msg["content"] = parts
        return msg

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        result = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            ttype = tool.get("type", "")
            if ttype in ("function", "web_search", "web_search_preview", "code_interpreter"):
                params = tool.get("parameters", {"type": "object", "properties": {}, "required": []})
                if isinstance(params, dict):
                    # strict mode: auto-populate required from properties keys
                    props = params.get("properties", {})
                    if isinstance(props, dict) and props:
                        params = dict(params)  # copy to avoid mutating input
                    params.setdefault("required", list(props.keys()))
                    params["additionalProperties"] = False
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ttype),
                        "description": tool.get("description", f"Built-in {ttype} tool"),
                        "parameters": params,
                        "strict": tool.get("strict", False),
                    }
                })
            elif ttype == "custom":
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": {
                            "type": "object",
                            "properties": {"input": {"type": "string"}},
                            "required": ["input"],
                            "additionalProperties": False,
                        },
                        "strict": tool.get("strict", False),
                    }
                })
        return result or None
