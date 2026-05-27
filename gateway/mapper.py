"""Model ID masquerade — bidirectional mapping between
Claude model names (client-visible) and DeepSeek model names (upstream)."""

from .config import load_config


class ModelMapper:
    def __init__(self):
        config = load_config()
        self._slot_map: dict[str, str] = dict(config.model_slots)
        self._responses_map: dict[str, str] = dict(config.responses_model_map)
        # Reverse: deepseek name → first Claude slot name
        self._reverse: dict[str, str] = {}
        seen: set[str] = set()
        for claude_name, ds_name in self._slot_map.items():
            if ds_name not in seen:
                self._reverse[ds_name] = claude_name
                seen.add(ds_name)

    # ── Anthropic (Claude Desktop / Claude Code) ──

    @property
    def slot_names(self) -> list[str]:
        return list(self._slot_map.keys())

    def get_model_list(self) -> dict:
        """Return OpenAI-format model list with 8 Claude model IDs."""
        data = []
        created_at = 1686935002
        for i, name in enumerate(self._slot_map.keys()):
            data.append({
                "id": name,
                "object": "model",
                "created": created_at + i,
                "owned_by": "anthropic",
            })
        return {"object": "list", "data": data}

    def resolve_anthropic(self, client_model: str) -> str:
        """claude-sonnet-4-20250514 → deepseek-chat."""
        return self._slot_map.get(client_model, self._slot_map.get(self.slot_names[0], "deepseek-chat"))

    def reverse_anthropic(self, upstream_model: str) -> str:
        """deepseek-chat → claude-sonnet-4-20250514 (first slot)."""
        return self._reverse.get(upstream_model, self.slot_names[0])

    # ── Responses API (Codex) ──

    def resolve_responses(self, client_model: str) -> str:
        """gpt-4o → deepseek-chat. Falls back to first slot if unknown."""
        return self._responses_map.get(client_model) or self._slot_map.get(self.slot_names[0], "deepseek-chat")


# Singleton
_mapper: ModelMapper | None = None


def get_mapper() -> ModelMapper:
    global _mapper
    if _mapper is None:
        _mapper = ModelMapper()
    return _mapper
