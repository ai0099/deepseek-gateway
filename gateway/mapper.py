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
        # Reverse-only entries: upstream models that map to existing slots
        # (no forward slot — they share a client-facing name on return only)
        self._reverse["deepseek-v4-flash"] = "claude-fable-5[1m]"
        # Reverse: deepseek name → first Codex model name (prefer [1m] versions)
        self._reverse_responses: dict[str, str] = {}
        for codex_name, ds_name in self._responses_map.items():
            if ds_name not in self._reverse_responses:
                self._reverse_responses[ds_name] = codex_name
            elif "[1m]" in codex_name:
                self._reverse_responses[ds_name] = codex_name  # prefer [1m]

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
            display_name = name
            data.append({
                "id": display_name,
                "object": "model",
                "created": created_at + len(data),
                "owned_by": "anthropic",
                "context_window": 1050000,
                "max_output_tokens": 393216,
            })
        # Codex model names from responses_map (prefer [1m] versions)
        models_by_base: dict[str, str] = {}
        for name in self._responses_map.keys():
            base = _strip_suffix(name)
            if base not in models_by_base or "[1m]" in name:
                models_by_base[base] = name
        for base, name in models_by_base.items():
            if base in seen:
                continue
            seen.add(base)
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
        """claude-fable-5[1m] → deepseek-v4-pro[1m]. Exact match first, then stripped base."""
        # Exact match (primary: client sends "claude-fable-5[1m]")
        if client_model in self._slot_map:
            return self._slot_map[client_model]
        # Stripped base match (backward compat: client sends bare "claude-fable-5")
        base = _strip_suffix(client_model)
        for key, value in self._slot_map.items():
            if _strip_suffix(key) == base:
                return value
        # Fallback to first slot
        return self._slot_map.get(self.slot_names[0], "deepseek-v4-pro")

    def reverse_anthropic(self, upstream_model: str) -> str:
        """deepseek-v4-pro → claude-fable-5[1m]."""
        base = self._reverse.get(_strip_suffix(upstream_model), self.slot_names[0])
        return base

    # ── Responses API (Codex) ──

    def resolve_responses(self, client_model: str) -> str:
        """gpt-5.6-sol[1m] → deepseek-v4-pro. Falls back to first slot if unknown.
        Strips context suffix from resolved model for DeepSeek Chat Completions API."""
        result = self._responses_map.get(client_model) or self._slot_map.get(self.slot_names[0], "deepseek-v4-pro")
        return _strip_suffix(result)

    def reverse_responses(self, upstream_model: str) -> str:
        """deepseek-v4-pro → gpt-5.6-sol[1m]. Returns matching Codex model name."""
        # Exact match (primary: upstream returns "deepseek-v4-pro[1m]")
        if upstream_model in self._reverse_responses:
            return self._reverse_responses[upstream_model]
        # Stripped base match (backward compat: upstream returns bare "deepseek-v4-pro")
        base = _strip_suffix(upstream_model)
        for key, value in self._reverse_responses.items():
            if _strip_suffix(key) == base:
                return value
        # Fallback
        return "gpt-5.6-sol[1m]"


# Singleton
_mapper: ModelMapper | None = None


def get_mapper() -> ModelMapper:
    global _mapper
    if _mapper is None:
        _mapper = ModelMapper()
    return _mapper
