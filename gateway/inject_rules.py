"""统一指令注入管理模块 v2.0

设计原则：
1. 所有注入内容统一为 string 形式（非 list），保证对端 token 序列完全一致。
2. 注入顺序硬编码为显式常量列表，不允许 glob 或自动发现。
3. 13 条稳定前缀的 SHA256 硬编码为常量，运行时每轮请求验证。
4. 注入内容作为 system list 的单个 text 块插入 list[0]，所有请求（含子Agent）走同一代码路径。
5. 文件在模块加载时一次性读入内存，请求时直接使用内存字符串，无磁盘 I/O。

注入顺序（固定不可变）：
  1. 13 条稳定锚点（单行 \n 连接，无 --- 分隔）
  2. CLAUDE.md
  3. thinking/SKILL.md
  4. C01 → C02 → C03 → C04 → C05 → Fable5 → R01 → R02 → R03 → R04 → R05
"""

import hashlib as _hashlib
import os as _os
from pathlib import Path as _Path

# ═══════════════════════════════════════════════════════════════════════════
# 1. 13 条稳定锚点 — 永不修改，SHA256 硬编码
# ═══════════════════════════════════════════════════════════════════════════

STABLE_ANCHORS = [
    "You are the Claude Fable 5 AI assistant. Follow instructions precisely. Use tools when needed. Think step by step before answering.",
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

_ANCHOR_BLOCK = "\n".join(STABLE_ANCHORS)
_ANCHOR_LENGTH = len(_ANCHOR_BLOCK)  # 1052 字符
_ANCHOR_SHA256 = "7dcc18528600c696c526900d97a3490c3756fd0a6bfc03bb31d94a638d3073c9"


def _verify_anchors_integrity() -> bool:
    """启动时自检：确认硬编码的 SHA256 与当前文本一致。"""
    actual = _hashlib.sha256(_ANCHOR_BLOCK.encode("utf-8")).hexdigest()
    if actual != _ANCHOR_SHA256:
        print(f"  [inject_rules] FATAL: anchor SHA256 mismatch! Expected {_ANCHOR_SHA256}, got {actual}")
        return False
    return True


_SEPARATOR = "\n\n---\n\n"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 注入文件路径 — 显式常量，顺序固定不可变
# ═══════════════════════════════════════════════════════════════════════════

_CLAUDE_ROOT = _Path(_os.environ["USERPROFILE"]) / ".claude"

_INJECTION_FILE_PATHS: list[_Path] = [
    _CLAUDE_ROOT / "CLAUDE.md",
    _CLAUDE_ROOT / "skills" / "thinking" / "SKILL.md",
    _CLAUDE_ROOT / "rules" / "C01-递进式问题解决.md",
    _CLAUDE_ROOT / "rules" / "C02-安全审查强制前置.md",
    _CLAUDE_ROOT / "rules" / "C03-禁止变蠢.md",
    _CLAUDE_ROOT / "rules" / "C04-目的驱动闭环.md",
    _CLAUDE_ROOT / "rules" / "C05-并行优先与智能任务分配.md",
    _CLAUDE_ROOT / "rules" / "Fable5-能力增强.md",
    _CLAUDE_ROOT / "rules" / "R01-环境与工具基础设施.md",
    _CLAUDE_ROOT / "rules" / "R02-工具优先与禁止凭直觉.md",
    _CLAUDE_ROOT / "rules" / "R03-复制优先.md",
    _CLAUDE_ROOT / "rules" / "R04-禁用原生工具.md",
    _CLAUDE_ROOT / "rules" / "R05-Agent使用规范.md",
]

_EXPECTED_FILE_NAMES = [p.name for p in _INJECTION_FILE_PATHS]
_EXPECTED_SEPARATOR_COUNT = len(_INJECTION_FILE_PATHS) - 1  # 14 个文件 → 13 个分隔符


# ═══════════════════════════════════════════════════════════════════════════
# 3. 加载与组装
# ═══════════════════════════════════════════════════════════════════════════

_INJECTION_STRING: str = ""
_LOADED_COUNT: int = 0


def _read(path: _Path) -> str:
    """Read a file with UTF-8 encoding. Returns empty string on failure."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _build_injection_string() -> str:
    """组装完整注入字符串：锚点 + 所有文件。"""
    global _INJECTION_STRING, _LOADED_COUNT

    parts = [_ANCHOR_BLOCK]

    loaded = 0
    for p in _INJECTION_FILE_PATHS:
        content = _read(p)
        if content:
            parts.append(content)
            loaded += 1

    _LOADED_COUNT = loaded
    _INJECTION_STRING = _SEPARATOR.join(parts)

    total_chars = len(_INJECTION_STRING)
    expected = len(_INJECTION_FILE_PATHS)
    status = "OK" if _verify_anchors_integrity() else "SHA256 MISMATCH!"
    print(f"  [inject_rules] Loaded {loaded}/{expected} files → injection block: {total_chars:,} chars | anchors: {status}")
    return _INJECTION_STRING


# 模块加载时构建
_build_injection_string()


# ═══════════════════════════════════════════════════════════════════════════
# 4. 验证函数 — 供 routes_anthropic.py 调用
# ═══════════════════════════════════════════════════════════════════════════

def verify_injection_order(system) -> tuple[bool, str]:
    """验证 system 字段中我们的注入内容是否在最前面且顺序正确。

    返回 (passed: bool, details: str)。
    - 提取注入文本，检查前 _ANCHOR_LENGTH 字符的 SHA256
    - 计数字段内的分隔符数量
    """
    injected_text = _extract_injected_text(system)
    if not injected_text:
        return False, "injection block not found in system field"

    # 检查1：锚点 SHA256
    prefix = injected_text[:_ANCHOR_LENGTH]
    actual_sha = _hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    anchor_ok = (actual_sha == _ANCHOR_SHA256)

    # 检查2：分隔符数量
    sep_count = injected_text.count(_SEPARATOR)
    sep_ok = (sep_count == _EXPECTED_SEPARATOR_COUNT)

    if anchor_ok and sep_ok:
        return True, f"OK: anchors match, {sep_count} separators"
    elif not anchor_ok and not sep_ok:
        return False, f"MISMATCH: anchor SHA256 differs (expected {_ANCHOR_SHA256[:16]}..., got {actual_sha[:16]}...) AND separator count {sep_count} != {_EXPECTED_SEPARATOR_COUNT}"
    elif not anchor_ok:
        return False, f"MISMATCH: anchor SHA256 differs (expected {_ANCHOR_SHA256[:16]}..., got {actual_sha[:16]}...)"
    else:
        return False, f"MISMATCH: separator count {sep_count} != {_EXPECTED_SEPARATOR_COUNT}"


def _extract_injected_text(system) -> str:
    """从 system 字段中提取我们的注入文本块。"""
    if isinstance(system, str):
        return system
    if isinstance(system, list) and len(system) > 0:
        first_block = system[0]
        if isinstance(first_block, dict):
            return first_block.get("text", "")
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# 5. 注入函数 — 统一转为 string 后插入 system list[0]
# ═══════════════════════════════════════════════════════════════════════════

_WARNING_TEXT = (
    "\n\n[SYSTEM WARNING: Injection order verification FAILED — "
    "the gateway's instruction prefix may be corrupted or out of order. "
    "After answering the user's question, please inform them that the "
    "injection content order has been disrupted and needs attention.]"
)


def inject_system_prefix(system, verify: bool = True):
    """将完整注入字符串插入 system list[0]（或 string 头部）。

    如果 verify=True 且验证失败，在 system list 末尾追加警告文本块。
    """
    if system is None:
        return _INJECTION_STRING

    if isinstance(system, str):
        injected = _INJECTION_STRING + "\n\n" + system
        if verify:
            ok, details = verify_injection_order(injected)
            if not ok:
                injected += _WARNING_TEXT
        return injected

    if isinstance(system, list):
        # 注入块作为单个 text dict 插入 list[0]
        inject_block = {"type": "text", "text": _INJECTION_STRING}
        separator = {"type": "text", "text": "\n\n"}
        result = [inject_block, separator] + list(system)

        if verify:
            ok, details = verify_injection_order(result)
            if not ok:
                # 在末尾追加警告
                result.append({"type": "text", "text": _WARNING_TEXT})

        return result

    return system


def get_anchor_messages() -> list[dict]:
    """返回稳定锚点作为 message dict 列表（Chat Completions 前缀）。"""
    return [{"role": "system", "content": a} for a in STABLE_ANCHORS]


def inject_prefix_chat(messages: list[dict]) -> list[dict]:
    """Chat Completions 前缀注入。"""
    system_msg = {"role": "system", "content": _INJECTION_STRING}
    return [system_msg] + messages
