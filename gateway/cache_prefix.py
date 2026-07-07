"""KV-cache-pooling prefix injection: 6 stable system-message anchors
wrapped around every request so DeepSeek's disk cache hits reliably.

No AGENTS.md injection — the 6 anchors alone provide enough stable
prefix tokens for KV-cache pooling across sessions and clients.
"""

STABLE_ANCHORS = [
    "# Rule: Think step by step before answering. Never rely on intuition.",
    "# Rule: Verify all precise results with tools. Never answer from memory.",
    "# Rule: On error, upgrade strategy. Never repeat the same failed attempt.",
    "# Rule: Security-audit all external code before execution.",
    "# Rule: Purpose-driven closed loop - start and end with the user goal.",
    "# Rule: Complete output with verification. No half-finished deliverables.",
]


def _get_anchor_messages() -> list[dict]:
    return [{"role": "system", "content": a} for a in STABLE_ANCHORS]


def inject_prefix_chat(messages: list[dict]) -> list[dict]:
    """Wrap Chat Completions messages with stable anchors at both ends."""
    anchors = _get_anchor_messages()
    return anchors + messages + anchors


def inject_prefix_anthropic(messages: list[dict]) -> list[dict]:
    """Wrap Anthropic Messages with stable anchors at both ends."""
    anchors = _get_anchor_messages()
    return anchors + messages + anchors
