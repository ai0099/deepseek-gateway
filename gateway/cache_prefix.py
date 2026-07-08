"""Backward compatibility wrapper — delegates to inject_rules.py (v2.0).

All KV-cache anchoring and instruction injection is now managed by
gateway/inject_rules.py.
"""

from .inject_rules import (
    STABLE_ANCHORS,
    get_anchor_messages,
    inject_system_prefix,
    inject_prefix_chat,
    verify_injection_order,
    _ANCHOR_SHA256 as ANCHOR_SHA256,
)

__all__ = [
    "STABLE_ANCHORS",
    "get_anchor_messages",
    "inject_system_prefix",
    "inject_prefix_chat",
    "verify_injection_order",
    "ANCHOR_SHA256",
]
