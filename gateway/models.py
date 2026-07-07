"""Pydantic models for request/response schemas — kept as reference.

Currently the gateway uses raw dicts (request.json()) instead of
Pydantic validation for maximum throughput. Models here document
the expected shapes of the three supported API protocols.
"""

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════
# Anthropic Messages API (reference)
# ═══════════════════════════════════════════

class AnthropicRequest(BaseModel):
    model: str
    messages: list[dict]
    system: str | list[dict] | None = None
    max_tokens: int = 8192
    tools: list[dict] | None = None
    tool_choice: dict | str | None = None
    stream: bool = False
    stop_sequences: list[str] | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    thinking: dict | None = None
    metadata: dict | None = None


# ═══════════════════════════════════════════
# OpenAI Chat Completions API (reference)
# ═══════════════════════════════════════════

class ChatCompletionsRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    reasoning_effort: str | None = None


# ═══════════════════════════════════════════
# OpenAI Responses API (reference)
# ═══════════════════════════════════════════

class ResponsesRequest(BaseModel):
    model: str | None = None
    input: str | list[dict]
    instructions: str | None = None
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    previous_response_id: str | None = None
    store: bool = True
    stream: bool = False
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    reasoning: dict | None = None
    parallel_tool_calls: bool | None = None
