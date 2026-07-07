"""KV-cache-pooling prefix injection: 6 stable anchor lines injected
at the very start of every request (system field + messages array) so
DeepSeek's disk cache hits reliably across main-agent and sub-agent
requests — even when their system prompts differ (CLAUDE.md vs Agent
tool definitions).

The anchors are the FIRST tokens in the effective prompt sequence,
ensuring that all requests — main agent, sub-agent 1, sub-agent 2, ...
— share an identical prefix regardless of what follows.
"""

STABLE_ANCHORS = [
    # ── Role preamble (always first — model identity anchor) ──
    "You are the Claude Fable 5 AI assistant. Follow instructions precisely. Use tools when needed. Think step by step before answering.",
    # ── Core behavioral rules (stable KV-cache anchoring) ──
    "# Rule: Think step by step before answering. Never rely on intuition.",
    "# Rule: Verify all precise results with tools. Never answer from memory.",
    "# Rule: On error, upgrade strategy. Never repeat the same failed attempt.",
    "# Rule: Security-audit all external code before execution.",
    "# Rule: Purpose-driven closed loop - start and end with the user goal.",
    "# Rule: Complete output with verification. No half-finished deliverables.",
    "# Rule: Complete file reads — never skip large files, never guess file contents.",
    "# Rule: Parallel-first execution — independent tasks via sub-agents, not serial edits.",
    "# Rule: Self-verify — run code after writing, confirm config after changing.",
    "# Rule: Delegate goals not steps — describe desired outcome, let model find optimal path.",
    "# Rule: Use 1M context fully — don't compress early, don't truncate files prematurely.",
    "# Rule: Every reply must close the loop — start and end with the user's goal.",
]

# Single newline-joined anchor block for system-field injection.
# Used when system is a plain string; for list-form system we use
# _get_anchor_messages().
_ANCHOR_BLOCK = "\n".join(STABLE_ANCHORS)


def _get_anchor_messages() -> list[dict]:
    return [{"role": "system", "content": a} for a in STABLE_ANCHORS]


def inject_system_prefix(system):
    """Prepend the stable anchor block to the system field.

    Ensures the effective prompt prefix is identical across ALL
    requests — main agent (CLAUDE.md system prompt) and sub-agents
    (tool-definition system prompt) both start with the same tokens.
    """
    if system is None:
        return _ANCHOR_BLOCK
    if isinstance(system, str):
        return _ANCHOR_BLOCK + "\n\n" + system
    if isinstance(system, list):
        anchors = [{"type": "text", "text": a} for a in STABLE_ANCHORS]
        separator = [{"type": "text", "text": "\n\n"}]
        return anchors + separator + system
    return system


def inject_prefix_chat(messages: list[dict]) -> list[dict]:
    """Wrap Chat Completions messages with stable anchors at the front only.
    Suffix anchors are omitted — DeepSeek's KV-cache is prefix-based,
    so trailing anchors never produce cache hits."""
    anchors = _get_anchor_messages()
    return anchors + messages


def inject_prefix_anthropic(messages: list[dict]) -> list[dict]:
    """Wrap Anthropic Messages with stable anchors at the front only.
    Suffix anchors are omitted — DeepSeek's KV-cache is prefix-based,
    so trailing anchors never produce cache hits."""
    anchors = _get_anchor_messages()
    return anchors + messages
