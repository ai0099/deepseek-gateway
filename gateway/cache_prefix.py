"""统一 facade — 同时暴露 Claude 和 Codex 两条注入路线。

架构：
  Claude/Anthropic 路由 → inject_rules.py  → routes_anthropic.py
  Codex/Chat Completions 路由 → inject_codex.py → translator.py

两条路线各自维护锚点、SHA256、注入内容和验证逻辑，
从 token 1 开始就分叉，形成独立的 DeepSeek KV 缓存前缀链。
"""

from .inject_rules import (
    STABLE_ANCHORS,
    get_anchor_messages,
    inject_system_prefix,
    inject_prefix_chat,          # Claude 路由版本 — Anthropic 端点用
    verify_injection_order,
    _ANCHOR_SHA256 as ANCHOR_SHA256,
)

from .inject_codex import (
    inject_prefix_chat as inject_prefix_chat_codex,   # Codex 路由版本 — Chat Completions 端点用
    verify_injection_order as verify_injection_order_codex,
)

__all__ = [
    "STABLE_ANCHORS",
    "get_anchor_messages",
    "inject_system_prefix",
    "inject_prefix_chat",           # Claude
    "inject_prefix_chat_codex",     # Codex
    "verify_injection_order",
    "verify_injection_order_codex",
    "ANCHOR_SHA256",
]
