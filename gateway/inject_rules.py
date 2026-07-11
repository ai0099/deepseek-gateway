"""统一指令注入管理模块 v2.0

设计原则：
1. 所有注入内容统一为 string 形式（非 list），保证对端 token 序列完全一致。
2. 注入顺序硬编码为显式常量列表，不允许 glob 或自动发现。
3. 26 条稳定前缀的 SHA256 硬编码为常量，运行时每轮请求验证。
4. 注入内容作为 system list 的单个 text 块插入 list[0]，所有请求（含子Agent）走同一代码路径。
5. 文件在模块加载时一次性读入内存，请求时直接使用内存字符串，无磁盘 I/O。

注入顺序（固定不可变）：
  1. 26 条稳定锚点（1 身份 + 25 规则，单行 \n 连接，无 --- 分隔）
  2. CLAUDE.md
  3. thinking/SKILL.md
  4. C01 → C02 → C03 → C04 → C05 → Fable5 → R01 → R02 → R03 → R04 → R05
"""

import hashlib as _hashlib
import os as _os
from pathlib import Path as _Path

# ═══════════════════════════════════════════════════════════════════════════
# 1. 26 条稳定锚点 — 永不修改，SHA256 硬编码（1 身份 + 25 规则）
# ═══════════════════════════════════════════════════════════════════════════

STABLE_ANCHORS = [
    "You are a reasoning AI assistant. Follow instructions precisely. Use tools when needed. Think step by step before answering.",
    "# Rule: Before closing each thinking block, scan once: are there numbered lists, bullets, or hard headings? If yes — self-report and rewrite.",
    "# Rule: Am I thinking — or am I formatting, rehearsing, or faking? Thinking mode and formatting mode feel similar until you check.",
    "# Rule: When uncertain about any fact, look it up with tools. Never answer from memory or gut feeling. Run code to verify; never mentally simulate.",
    "# Rule: Never use WebSearch, WebFetch, Read built-in image rendering, or Workflow deep-research. All network retrieval goes through stealth-browser.",
    "# Rule: All external code (clone, download, Skill install, copied code) must pass a Four-Engine Security Audit before execution or integration.",
    "# Rule: Thinking intensity is always max. Never downgrade. Stop only when new angles repeat old conclusions, not because you have thought too long.",
    "# Rule: Before every action, pause: what is the simplest way? If this fails twice, stop. Should I stop and tell the user now?",
    "# Rule: Thinking is a tree, not a line. If you realize you are on a wrong path, turn back immediately. Thinking time is not an investment — there is no sunk cost.",
    "# Rule: The thinking block and the final response are completely separate. Never say Based on the above analysis in the response. Lead with the conclusion.",
    "# Rule: Before answering, identify what the user truly wants to achieve — not what they literally asked. End every response with a purpose-achievement assessment.",
    "# Rule: Every reply must close the loop. Start from the user goal, end by confirming the goal was achieved — or clearly stating what remains. Never pretend you achieved something you have not.",
    "# Rule: Generate multiple hypotheses before locking onto any single interpretation. If my best hypothesis is wrong, what evidence would prove it wrong fastest?",
    "# Rule: Default to parallel — independent subtasks launch as sub-agents simultaneously, up to 15 at once. Main Agent is orchestrator (decompose, dispatch, synthesize), not executor. Delegate goals not steps. File coding is the Agent job.",
    "# Rule: Tools are amplifiers of thinking, not replacements. A tool result is new input — thinking continues from it, not ends at it. Is the result confirming or overturning my hypothesis?",
    "# Rule: Before output, run a path quality check: is there a simpler way? If the user follows my answer, will they hit problems? Is this my best answer?",
    "# Rule: Am I moving closer to the answer, or just moving? Both feel like progress. Only one is.",
    "# Rule: Is there a counterexample I am deliberately avoiding? The one that makes me uncomfortable and I chose not to examine too closely.",
    "# Rule: More than 5 minutes without real progress — does my method need adjusting, or is this problem genuinely hard? If the former, change methods. If the latter, change strategy.",
    "# Rule: Was my last decision driven by the thinking engine, or by the rule navigation? Are the two aligned right now — or is the engine driving east while the nav is shouting go west?",
    "# Rule: Would one more minute of thinking change the answer — or am I already repeating myself? If just rewording the same conclusion, that is decorating, not deepening.",
    "# Rule: Python commands must use the full path to the global venv: C:/Users/Administrator/.claude/venv/Scripts/python.exe. Prefix with PYTHONIOENCODING=utf-8 when code contains non-ASCII characters.",
    "# Rule: After writing code, run it to verify. After changing config, confirm it takes effect. There is no should be fine — only verified and passing.",
    "# Rule: Read files completely — never skip large files, never guess file contents. Use the 1M context fully. Read BEFORE reasoning.",
    "# Rule: Before creating any file or code, first ask: does something already exist I can copy? Copy-first, then make small adaptations. Rewriting from scratch introduces bugs.",
    "# Rule: The workspace root E:\\Claude\\ must never contain a .git directory. Each sub-project gets its own independent repository. A .git at the root causes system prompt hash changes — every rebuild costs cache.",
]

_ANCHOR_BLOCK = "\n".join(STABLE_ANCHORS)
_ANCHOR_LENGTH = len(_ANCHOR_BLOCK)  # 4161 字符
_ANCHOR_SHA256 = "a069bd731f095aa1e5f964a8fc5fe23d65b8f18458132cc2fb2a2c7f2872a704"


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
    _CLAUDE_ROOT / "rules" / "C01-progressive-problem-solving.md",
    _CLAUDE_ROOT / "rules" / "C02-mandatory-security-audit.md",
    _CLAUDE_ROOT / "rules" / "C03-dont-be-stupid.md",
    _CLAUDE_ROOT / "rules" / "C04-purpose-driven-closed-loop.md",
    _CLAUDE_ROOT / "rules" / "C05-parallel-first-intelligent-task-assignment.md",
    _CLAUDE_ROOT / "rules" / "Fable5-capability-enhancement.md",
    _CLAUDE_ROOT / "rules" / "R01-environment-and-tool-infrastructure.md",
    _CLAUDE_ROOT / "rules" / "R02-tool-first-never-trust-your-gut.md",
    _CLAUDE_ROOT / "rules" / "R03-copy-first.md",
    _CLAUDE_ROOT / "rules" / "R04-disabled-native-tools.md",
    _CLAUDE_ROOT / "rules" / "R05-agent-usage-standards.md",
]

_EXPECTED_FILE_NAMES = [p.name for p in _INJECTION_FILE_PATHS]


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
    """验证 system 字段中我们的注入内容是否在最前面。

    对于 list-form system：提取 system[0].text，检查它是否以锚点 block 开头。
    对于 string-form system：检查开头是否匹配锚点 block。

    只验证锚点位置（SHA256），不计数分隔符——分隔符数量随文件内容变化。
    """
    injected_text = _extract_injected_text(system)
    if not injected_text:
        return False, "injection block not found in system field"

    # 检查：注入块的前 _ANCHOR_LENGTH 字符是否匹配锚点 SHA256
    prefix = injected_text[:_ANCHOR_LENGTH]
    actual_sha = _hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    if actual_sha == _ANCHOR_SHA256:
        return True, "OK: anchors in correct position"
    return False, (
        f"MISMATCH: anchor SHA256 differs "
        f"(expected {_ANCHOR_SHA256[:16]}..., got {actual_sha[:16]}...)"
    )


def _extract_injected_text(system) -> str:
    """从 system 字段中提取我们注入的文本块。

    对于 list-form system：system[0] 是我们的注入块。
    对于 string-form system：整个 string 以注入块开头。
    """
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


def inject_prefix_chat(messages: list[dict], extra_content: str = "") -> list[dict]:
    """Chat Completions 前缀注入。

    注入顺序：锚点 + 所有规则文件 + (可选) extra_content。
    确保所有客户端（CLI + 桌面端）的 token 序列从相同的锚点开始，
    从而共享 DeepSeek KV 缓存。
    """
    content = _INJECTION_STRING
    if extra_content:
        content = _INJECTION_STRING + "\n\n" + extra_content
    system_msg = {"role": "system", "content": content}
    return [system_msg] + messages
