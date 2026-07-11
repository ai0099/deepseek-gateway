"""Model ID masquerade — bidirectional mapping between
Claude model names (client-visible) and DeepSeek model names (upstream)."""

from .config import load_config


def _strip_suffix(model_name: str) -> str:
    """Strip context-window suffixes like [1m] from model names."""
    bracket = model_name.find("[")
    return model_name[:bracket] if bracket != -1 else model_name


class ModelMapper:
    def __init__(self):
        config = load_config()
        self._slot_map: dict[str, str] = dict(config.model_slots)
        self._responses_map: dict[str, str] = dict(config.responses_model_map)
        # Reverse: deepseek name → first Claude slot name
        self._reverse: dict[str, str] = {}
        seen: set[str] = set()
        for claude_name, ds_name in self._slot_map.items():
            base = _strip_suffix(ds_name)
            if base not in seen:
                self._reverse[base] = claude_name
                seen.add(base)
        # Reverse: deepseek name → first Codex model name
        self._reverse_responses: dict[str, str] = {}
        for codex_name, ds_name in self._responses_map.items():
            if ds_name not in self._reverse_responses:
                self._reverse_responses[ds_name] = codex_name

    # ── Anthropic (Claude Desktop / Claude Code) ──

    @property
    def slot_names(self) -> list[str]:
        return list(self._slot_map.keys())

    def get_model_list(self) -> dict:
        """Return OpenAI-format model list with [1m] suffix for 1M context window."""
        data = []
        created_at = 1686935002
        seen: set[str] = set()
        for name in self._slot_map.keys():
            base = _strip_suffix(name)
            if base in seen:
                continue
            seen.add(base)
            display_name = base + "[1m]"
            data.append({
                "id": display_name,
                "object": "model",
                "created": created_at + len(data),
                "owned_by": "anthropic",
                "context_window": 1050000,
                "max_output_tokens": 393216,
            })
        # Codex model names from responses_map
        for name in self._responses_map.keys():
            if name in seen:
                continue
            seen.add(name)
            data.append({
                "id": name,
                "object": "model",
                "created": created_at + len(data),
                "owned_by": "openai",
                "context_window": 1050000,
                "max_output_tokens": 393216,
                "supports_tools": True,
                "supports_computer_use": True,
                "supports_parallel_tool_calls": True,
                "supports_streaming": True,
            })
        return {"object": "list", "data": data}

    def resolve_anthropic(self, client_model: str) -> str:
        """claude-sonnet-4-20250514[1m] → deepseek-v4-pro[1m] (strips [1m] suffix first)."""
        base = _strip_suffix(client_model)
        return self._slot_map.get(base, self._slot_map.get(self.slot_names[0], "deepseek-v4-pro"))

    def reverse_anthropic(self, upstream_model: str) -> str:
        """deepseek-v4-pro → claude-fable-5[1m]. Always appends [1m] suffix."""
        base = self._reverse.get(_strip_suffix(upstream_model), self.slot_names[0])
        base = _strip_suffix(base)
        return base + "[1m]"

    # ── Responses API (Codex) ──

    def resolve_responses(self, client_model: str) -> str:
        """gpt-5.6-sol[1m] → deepseek-v4-pro. Falls back to first slot if unknown.
        Strips context suffix from resolved model for DeepSeek Chat Completions API."""
        result = self._responses_map.get(client_model) or self._slot_map.get(self.slot_names[0], "deepseek-v4-pro")
        return _strip_suffix(result)

    def reverse_responses(self, upstream_model: str) -> str:
        """deepseek-v4-pro → gpt-5.6-sol[1m]. Returns matching Codex model name."""
        base = _strip_suffix(upstream_model)
        return self._reverse_responses.get(base, "gpt-5.6-sol[1m]")


# Singleton
_mapper: ModelMapper | None = None


def get_mapper() -> ModelMapper:
    global _mapper
    if _mapper is None:
        _mapper = ModelMapper()
    return _mapper
