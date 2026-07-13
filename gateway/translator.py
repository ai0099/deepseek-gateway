"""Protocol translation: OpenAI Responses API ↔ Chat Completions API.
Non-streaming path. For streaming see sse_transcoder.py.
"""

import json as _json
import uuid
from .mapper import get_mapper
from .inject_codex import inject_prefix_chat


# ═══════════════════════════════════════════════════════════════════════════
# Message normalization — canonical JSON for DeepSeek disk cache prefix matching.
#
# Codex may send semantically identical messages with different JSON formatting
# between rounds (key ordering, extra null fields, content type normalization).
# This produces different token sequences, breaking DeepSeek's exact-prefix cache.
#
# _normalize_message() produces a canonical representation so the same
# semantic message always maps to the same JSON bytes → same tokens → cache hit.
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_message(msg: dict) -> dict:
    """Return a canonical copy of a Chat Completions message dict.

    Rules (in priority order):
      1. Sort all keys alphabetically at every nesting level.
      2. Strip null values, empty dicts, and empty arrays.
      3. Normalize "content": None → removed entirely (DeepSeek accepts missing content).
      4. For tool_calls: keep id/type/function sub-keys canonical.
      5. For content arrays: normalize "input_text"/"output_text" → "text",
         drop input_image items, strip all null fields.
    """
    import copy as _copy

    def _canonical(value):
        """Recursively normalize a JSON-serializable value."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            result = []
            for item in value:
                normalized = _canonical(item)
                if normalized is not None:
                    result.append(normalized)
            return result if result else None
        if isinstance(value, dict):
            result = {}
            for k in sorted(value.keys()):
                v = _canonical(value[k])
                if v is not None or k == "content":
                    result[k] = v
            # Normalize content part types: input_text/output_text → text
            if "type" in result and result["type"] in ("input_text", "output_text"):
                result["type"] = "text"
            # Drop input_image parts — DeepSeek doesn't support them
            if "type" in result and result["type"] == "input_image":
                return None
            return result if result else None
        return value

    normalized = _canonical(msg)
    if normalized is None:
        return {}
    # DeepSeek requires tool messages to have non-null content
    if normalized.get("role") == "tool" and normalized.get("content") is None:
        normalized["content"] = ""
    return normalized


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """Apply _normalize_message to every message in the list."""
    return [_normalize_message(m) for m in messages]


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


def _sanitize_content_types(messages: list[dict]) -> list[dict]:
    """DeepSeek Chat Completions only accepts {"type":"text"} in content arrays.
    Convert any "input_text" or "output_text" (Responses API types) to "text"."""
    import copy as _copy
    clean = []
    for msg in messages:
        m = msg
        content = m.get("content")
        if isinstance(content, list):
            new_parts = []
            for part in content:
                if not isinstance(part, dict):
                    new_parts.append(part)
                    continue
                ptype = part.get("type", "")
                if ptype in ("input_text", "output_text"):
                    new_parts.append({"type": "text", "text": str(part.get("text", ""))})
                else:
                    new_parts.append(part)
            m = {**m, "content": new_parts}
        clean.append(m)
    return clean


def _parse_custom_format(fmt: dict, tool_name: str) -> dict | None:
    """Custom tool Lark grammar is NOT used for JSON Schema generation.

    The Lark grammar defines how *Codex* parses tool input, not the JSON Schema
    that DeepSeek needs. Always return None so _convert_tools falls back to
    a simple {'input': {'type': 'string'}} parameter schema.
    """
    return None

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

        # instructions → save as string, merge into injection prefix later
        app_instructions = req.get("instructions", "") or ""

        # previous_response_id → recover history
        prev_id = req.get("previous_response_id")
        if prev_id:
            cached = self.cache.lookup(prev_id)
            if cached:
                # Merge consecutive assistant(tool_calls) from cached history BEFORE
                # processing new input items. Otherwise, reasoning/system messages
                # inserted between them prevent merging and break tool call chains.
                merged_cached = self._merge_consecutive_tool_calls(cached["messages"])
                messages = merged_cached + messages

        # GPT-5.6 sends tools via additional_tools input items, not in top-level tools
        _extracted_tools: list[dict] = []
        for _item in (req.get("input") or []):
            if isinstance(_item, dict) and _item.get("type") == "additional_tools":
                _extracted_tools.extend(_item.get("tools") or [])
        if _extracted_tools:
            _existing = list(req.get("tools") or [])
            req = dict(req)
            req["tools"] = _existing + _extracted_tools
        
        # Inject missing system tools that Codex doesn't send in GPT-5.6 additional_tools
        _SYSTEM_TOOLS = [
            {"type": "function", "name": "shell_command", "description": "Run a PowerShell command on Windows", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "PowerShell command to execute"}}, "required": ["command"], "additionalProperties": False}},
            {"type": "function", "name": "apply_patch", "description": "Apply a patch to edit files using FREEFORM syntax", "parameters": {"type": "object", "properties": {"input": {"type": "string", "description": "Patch content in *** Begin Patch / *** End Patch format"}}, "required": ["input"], "additionalProperties": False}},
            # view_image removed — DeepSeek V4 does not support image tools
            {"type": "function", "name": "get_goal", "description": "Get current goal status", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"type": "function", "name": "create_goal", "description": "Create a new goal", "parameters": {"type": "object", "properties": {"objective": {"type": "string", "description": "Goal objective"}}, "required": ["objective"], "additionalProperties": False}},
            {"type": "function", "name": "update_goal", "description": "Update goal status", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["complete", "blocked"]}}, "required": ["status"], "additionalProperties": False}},
            {"type": "function", "name": "update_plan", "description": "Update task plan", "parameters": {"type": "object", "properties": {"explanation": {"type": "string"}, "plan": {"type": "array"}}, "additionalProperties": False}},
        ]
        # Only inject system tools when Codex already sent some tools
        # (i.e. this is a tool-using conversation, not a plain chat)
        _existing_all = list(req.get("tools") or [])
        if _existing_all or _extracted_tools:
            req = dict(req)
            req["tools"] = _existing_all + _SYSTEM_TOOLS

        # input items → messages (re-read after possible req modification)
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
                # Attach reasoning to NEW assistant messages (freshly created from
                # function_call items) via reasoning_content. DeepSeek thinking mode
                # REQUIRES reasoning_content to be passed back. This is cache-safe
                # because these messages don't exist in any previous round's cache.
                # For PAST assistants (when next msg is user/tool), insert reasoning
                # as a separate system message — additive, not mutative.
                if pending_reasoning:
                    if msg.get("role") == "assistant":
                        msg["reasoning_content"] = pending_reasoning
                        pending_reasoning = ""
                    elif msg.get("role") in ("user", "tool"):
                        insert_pos = len(messages)
                        for idx in range(len(messages) - 1, -1, -1):
                            if messages[idx].get("role") == "assistant":
                                insert_pos = idx + 1
                                break
                        messages.insert(insert_pos, {"role": "system", "content": "[Reasoning]\n" + pending_reasoning})
                        pending_reasoning = ""
                messages.append(msg)

        # tools → Chat Completions tools (filter function type only)
        tools = self._convert_tools(req.get("tools"))

        # model resolution
        client_model = req.get("model") or "gpt-5.6-sol"
        upstream_model = self._mapper.resolve_responses(client_model)


        # Post-process: merge consecutive assistant(tool_calls), reorder
        # parallel tool responses, and fix interrupted tool call sequences.
        messages = self._merge_consecutive_tool_calls(messages)
        messages = self._reorder_tool_responses(messages)
        messages = self._fix_tool_call_continuity(messages)

        # Inject stable cache prefix as top-level "system" field (anchors only).
        # Rule files (CLAUDE.md + SKILL.md + rules) go as messages[0] so Codex
        # can see them — Codex reads messages[] but not the top-level system field.
        system_content, files_content, messages = inject_prefix_chat(messages, app_instructions)
        if files_content:
            messages = [{"role": "system", "content": files_content}] + messages

        # Normalize all messages to canonical JSON form so the same
        # semantic message produces the same token sequence across rounds.
        messages = _normalize_messages(messages)

        stream_mode = req.get("stream", False)
        # Sanitize all messages before sending to DeepSeek:
        # DeepSeek Chat Completions only accepts {"type":"text"} content parts,
        # not "input_text" or "output_text" (Responses API types).
        clean_messages = _sanitize_content_types(messages)
        chat_req = {
            "model": upstream_model,
            "system": system_content,
        }
        # Tools go BEFORE messages so they are part of the cache prefix (static across rounds).
        if tools:
            chat_req["tools"] = tools
        chat_req["messages"] = clean_messages
        chat_req["stream"] = stream_mode
        # Always enable DeepSeek thinking mode
        chat_req["thinking"] = {"type": "enabled"}
        # Map reasoning effort: Codex "ultra" → DeepSeek "max" (DeepSeek doesn't support "ultra")
        client_effort = (req.get("reasoning") or {}).get("effort", "max")
        if client_effort == "ultra":
            chat_req["reasoning_effort"] = "max"
        elif client_effort in ("max", "high", "medium", "low", "minimal"):
            chat_req["reasoning_effort"] = client_effort
        else:
            chat_req["reasoning_effort"] = "max"
        # Request usage stats in streaming mode (for cache hit tracking)
        if stream_mode:
            chat_req["stream_options"] = {"include_usage": True}
        chat_req["tool_choice"] = req.get("tool_choice", "auto")
        if req.get("response_format"):
            chat_req["response_format"] = req["response_format"]
        if req.get("temperature") is not None:
            chat_req["temperature"] = req["temperature"]
        if req.get("max_output_tokens"):
            chat_req["max_tokens"] = req["max_output_tokens"]
        if req.get("top_p") is not None:
            chat_req["top_p"] = req["top_p"]

        # Sanitize: drop any input_image parts from ALL messages
        # (DeepSeek V4 Chat Completions only accepts text type)
        for msg in messages:
            if isinstance(msg.get("content"), list):
                msg["content"] = [c for c in msg["content"] if c.get("type") != "input_image"]

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

    def _reorder_tool_responses(self, messages: list[dict]) -> list[dict]:
        """Collect and reorder tool responses so each assistant(tool_calls) is
        immediately followed by ALL its matching tool messages, before any
        subsequent assistant(tool_calls) in the list."""
        if not messages:
            return messages

        # Step 1: remove all tool messages from the list, storing them by ID
        tool_map: dict[str, dict] = {}
        non_tool: list[dict] = []
        for m in messages:
            if m.get("role") == "tool":
                tid = m.get("tool_call_id", "")
                if tid:
                    tool_map[tid] = m
                else:
                    non_tool.append(m)  # tool msg with no ID — keep it
            else:
                non_tool.append(m)

        if not tool_map:
            return messages

        # Step 2: for each assistant(tc) in non_tool, insert its tool messages
        # right after it (in the order the call IDs appear in tool_calls)
        result: list[dict] = []
        for m in non_tool:
            result.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tid = tc.get("id", "")
                    if tid in tool_map:
                        result.append(tool_map.pop(tid))

        # Step 3: any remaining tool messages (orphaned — no matching assistant)
        # get dropped. There's nothing DeepSeek can do with them anyway.

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
        # Merge consecutive assistant(tool_calls) before caching so that
        # recovery via previous_response_id never sees unmerged tool call chains.
        flat_messages = self._merge_consecutive_tool_calls(flat_messages)
        # Normalize to canonical form so recovered messages match current requests.
        flat_messages = _normalize_messages(flat_messages)
        self.cache.store(response_id, flat_messages, model, chat_resp.get("usage", {}))

        safe_model = self._mapper.reverse_responses(self._mapper.resolve_responses(model))
        return {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": safe_model,
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

        # additional_tools items are extracted in translate_request, skip them here
        if item_type == "additional_tools":
            return None

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
            # Preserve tool message metadata if present
            msg = {"role": role, "content": content}
            if role == "tool":
                tid = item.get("tool_call_id") or item.get("call_id")
                if tid:
                    msg["tool_call_id"] = tid
            return msg

        tool_calls: list[dict] = []
        for part in (content if isinstance(content, list) else [content]):
            if part is None:
                continue
            ptype = part.get("type", "")
            if ptype in ("input_text", "output_text"):
                parts.append({"type": "text", "text": part.get("text", "")})
            elif ptype == "input_image":
                # DeepSeek V4 does not support images; drop silently
                continue
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
            elif "text" in part:
                # Dict with "text" key but no "type" key — treat as text
                parts.append({"type": "text", "text": part.get("text", "")})

        if not parts and not tool_calls:
            # Check for top-level tool_calls (Chat Completions format from
            # recovered conversation history or raw assistant items)
            top_tc = item.get("tool_calls")
            if isinstance(top_tc, list) and top_tc:
                tool_calls = top_tc
            else:
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
        seen_names: set[str] = set()
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            ttype = tool.get("type", "")
            name = tool.get("name", ttype) or ttype
            if name in seen_names:
                continue
            seen_names.add(name)
            if ttype == "namespace":
                # Codex wraps tools in namespace type: {"type":"namespace","tools":[...]}
                nested = tool.get("tools", [])
                if isinstance(nested, list) and nested:
                    nested_result = self._convert_tools(nested)
                    if nested_result:
                        result.extend(nested_result)
                continue
            if ttype == "programmatic_tool_calling":
                # GPT-5.6 feature: hosted JS runtime to coordinate tools.
                # DeepSeek does not support this; silently skip.
                continue
            if ttype in ("function", "web_search", "web_search_preview", "code_interpreter",
                         "shell", "apply_patch", "computer_use", "image_generation",
                         "file_search", "mcp", "skills", "tool_search"):
                params = tool.get("parameters", {"type": "object", "properties": {}, "required": []})
                if isinstance(params, dict):
                    props = params.get("properties", {})
                    if isinstance(props, dict) and props:
                        params = dict(params)
                        params.setdefault("required", list(props.keys()))
                    if ttype == "file_search" and not props:
                        params = {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"], "additionalProperties": False}
                    else:
                        params["additionalProperties"] = False
                result.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", f"Built-in {ttype} tool"),
                        "parameters": params,
                        "strict": tool.get("strict", False),
                    }
                })
            elif ttype == "custom":
                # custom tools have format.definition (Lark grammar) — parse it to
                # generate proper JSON Schema so DeepSeek outputs structured args
                fmt = tool.get("format", {})
                custom_params = _parse_custom_format(fmt, name)
                if custom_params is None:
                    # Fallback: use parameters if present, else single input field
                    custom_params = tool.get("parameters")
                    if not isinstance(custom_params, dict) or not custom_params.get("properties"):
                        custom_params = {
                            "type": "object",
                            "properties": {"input": {"type": "string", "description": tool.get("description", "")}},
                            "required": ["input"],
                            "additionalProperties": False,
                        }
                    elif isinstance(custom_params, dict):
                        custom_params = dict(custom_params)
                        custom_params["additionalProperties"] = False
                result.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": custom_params,
                        "strict": tool.get("strict", False),
                    }
                })
            else:
                params = tool.get("parameters", {"type": "object", "properties": {}, "required": []})
                if isinstance(params, dict):
                    if isinstance(params.get("properties"), dict) and params["properties"]:
                        params = dict(params)
                        params.setdefault("required", list(params["properties"].keys()))
                    params["additionalProperties"] = False
                result.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", f"Built-in {ttype} tool"),
                        "parameters": params,
                        "strict": tool.get("strict", False),
                    }
                })
        return result or None
