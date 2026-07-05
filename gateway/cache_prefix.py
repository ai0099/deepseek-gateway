import os
from pathlib import Path

_CACHE = None
_PREFIX_PATH = None

def _find_claude_md():
    candidates = [
        Path(os.path.expandvars(r"%USERPROFILE%\.codex\AGENTS.md")),
        Path(r"C:\Users\Administrator\.codex\AGENTS.md"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

STABLE_ANCHORS = [
    "# Rule: Think step by step before answering. Never rely on intuition.",
    "# Rule: Verify all precise results with tools. Never answer from memory.",
    "# Rule: On error, upgrade strategy. Never repeat the same failed attempt.",
    "# Rule: Security-audit all external code before execution.",
    "# Rule: Purpose-driven closed loop - start and end with the user goal.",
    "# Rule: Complete output with verification. No half-finished deliverables.",
]

def _get_anchor_messages():
    return [{"role": "system", "content": a} for a in STABLE_ANCHORS]

def inject_prefix_with_anchors(messages, main_prefix):
    anchors = _get_anchor_messages()
    main_msg = {"role": "system", "content": main_prefix}
    return anchors + [main_msg] + messages

def _build_stable_prefix(claude_md_path):
    content = claude_md_path.read_text(encoding="utf-8")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    return content.strip()

def get_stable_prefix():
    global _CACHE, _PREFIX_PATH
    if _CACHE is not None:
        return _CACHE
    claude_md = _find_claude_md()
    if claude_md is None:
        _CACHE = "# Global Rules\nCore: C01-C04 | R02-R18\n"
        return _CACHE
    _PREFIX_PATH = claude_md
    _CACHE = _build_stable_prefix(claude_md)
    return _CACHE

def inject_prefix_chat(messages):
    prefix = get_stable_prefix()
    return inject_prefix_with_anchors(messages, prefix)

def inject_prefix_anthropic(messages):
    prefix = get_stable_prefix()
    return inject_prefix_with_anchors(messages, prefix)

# Quick test
if __name__ == "__main__":
    msgs = [{"role": "user", "content": "hello"}]
    result = inject_prefix_chat(msgs)
    print(f"Messages: {len(result)}")
    for i, m in enumerate(result):
        content = str(m.get("content", ""))[:80]
        print(f"  [{i}] role={m['role']} content={content}...")
    print("OK - multi-anchor injection works")
