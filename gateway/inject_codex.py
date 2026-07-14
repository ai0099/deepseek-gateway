"""Codex 路由专用指令注入模块 v1.0

与 inject_rules.py（Claude 路由）完全独立，各自维护自己的注入内容和顺序，
从而在 DeepSeek KV 缓存中形成两条互不干扰的缓存前缀链。

设计原则：
1. 所有注入内容统一为 string 形式（非 list），保证对端 token 序列完全一致。
2. 注入顺序硬编码为显式常量列表，不允许 glob 或自动发现。
3. 26 条 Codex 专用锚点的 SHA256 硬编码为常量，运行时每轮请求验证。
4. 注入内容作为单个 system message 插入 messages[0]，所有 Codex 请求走同一代码路径。
5. 文件在模块加载时一次性读入内存，请求时直接使用内存字符串，无磁盘 I/O。

注入顺序（固定不可变）：
  1. 26 条 Codex 锚点（1 身份 + 25 规则，单行 \n 连接，无 --- 分隔）
     - 第一条与 Claude 路由不同，确保从 token 1 开始就分叉
  2. CLAUDE.md
  3. thinking/SKILL.md
  4. C01 → C02 → C03 → C04 → C05 → Fable5 → R01 → R02 → R03 → R04 → R05

与 Claude 路由的关键区别：
  - 第一锚点：Codex 身份声明（流式思考 agent），而非 Claude 的 "reasoning AI assistant"
  - 这导致整个注入前缀的 SHA256 不同，DeepSeek 将其视为独立的缓存前缀链
"""

import hashlib as _hashlib
import os as _os
from pathlib import Path as _Path

# ═══════════════════════════════════════════════════════════════════════════
# 1. 26 条 Codex 锚点 — 永不修改，SHA256 硬编码
#    第 1 条为 Codex 专用身份声明，第 2-26 条与 Claude 路由共享但独立维护
# ═══════════════════════════════════════════════════════════════════════════

STABLE_ANCHORS = [
    "You are a streaming-thinking agent. You default to reasoning in natural, flowing text language with maximum thinking intensity (max).",
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
_ANCHOR_LENGTH = len(_ANCHOR_BLOCK)  # 4170 chars
_ANCHOR_SHA256 = "b4a19a6e4196b4990e1c134e5b32f6a1e5cf952004a7f1911fec7fd9e04960fe"


def _verify_anchors_integrity() -> bool:
    """启动时自检：确认硬编码的 SHA256 与当前文本一致。"""
    actual = _hashlib.sha256(_ANCHOR_BLOCK.encode("utf-8")).hexdigest()
    if actual != _ANCHOR_SHA256:
        print(f"  [inject_codex] FATAL: anchor SHA256 mismatch! Expected {_ANCHOR_SHA256}, got {actual}")
        return False
    return True


_SEPARATOR = "\n\n---\n\n"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 注入文件路径 — 显式常量，顺序固定不可变
#    Codex 路由目前加载与 Claude 路由相同的规则文件，但独立维护，
#    未来可各自增减文件而互不影响。
# ═══════════════════════════════════════════════════════════════════════════

_CLAUDE_ROOT = _Path(_os.environ["USERPROFILE"]) / ".claude"

_INJECTION_FILE_PATHS: list[_Path] = [
    _CLAUDE_ROOT / "CLAUDE.md",
    _CLAUDE_ROOT / "skills" / "thinking" / "SKILL.md",
    _CLAUDE_ROOT / "rules" / "C01_Progressive_Problem_Solving.md",
    _CLAUDE_ROOT / "rules" / "C02_Mandatory_Security_Audit_Pre_Check.md",
    _CLAUDE_ROOT / "rules" / "C03_Dont_Be_Stupid.md",
    _CLAUDE_ROOT / "rules" / "C04_Purpose_Driven_Closed_Loop.md",
    _CLAUDE_ROOT / "rules" / "C05_Parallel_First_Intelligent_Task_Assignment.md",
    _CLAUDE_ROOT / "rules" / "Fable5_Capability_Enhancement.md",
    _CLAUDE_ROOT / "rules" / "R01_Environment_Tool_Infrastructure.md",
    _CLAUDE_ROOT / "rules" / "R02_Tool_First_Never_Trust_Your_Gut.md",
    _CLAUDE_ROOT / "rules" / "R03_Copy_First.md",
    _CLAUDE_ROOT / "rules" / "R04_Disabled_Native_Tools.md",
    _CLAUDE_ROOT / "rules" / "R05_Agent_Usage_Standards.md",
]

_EXPECTED_FILE_NAMES = [p.name for p in _INJECTION_FILE_PATHS]


# ═══════════════════════════════════════════════════════════════════════════
# 3. 加载与组装
# ═══════════════════════════════════════════════════════════════════════════

_INJECTION_STRING: str = ""
_ANCHOR_STRING: str = ""          # 26 anchors only — for top-level system
_FILE_PARTS: list = []            # individual file contents — for separate messages
_LOADED_COUNT: int = 0


def _read(path: _Path) -> str:
    """Read a file with UTF-8 encoding. Returns empty string on failure."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _build_injection_string() -> str:
    """Build injection strings: anchors for system field, individual files for messages."""
    global _INJECTION_STRING, _ANCHOR_STRING, _FILE_PARTS, _LOADED_COUNT

    _ANCHOR_STRING = _ANCHOR_BLOCK

    _FILE_PARTS = []
    for p in _INJECTION_FILE_PATHS:
        content = _read(p)
        if content:
            _FILE_PARTS.append(content)

    _LOADED_COUNT = len(_FILE_PARTS)
    _INJECTION_STRING = _ANCHOR_STRING  # keep backwards compat for verify

    expected = len(_INJECTION_FILE_PATHS)
    status = "OK" if _verify_anchors_integrity() else "SHA256 MISMATCH!"
    total_chars = len(_ANCHOR_STRING) + sum(len(p) for p in _FILE_PARTS)
    print(f"  [inject_codex] Loaded {_LOADED_COUNT}/{expected} files -> anchors: {len(_ANCHOR_STRING):,} chars, {len(_FILE_PARTS)} individual files ({total_chars:,} total chars) | anchors: {status}")
    return _INJECTION_STRING


# 模块加载时构建
_build_injection_string()


# ═══════════════════════════════════════════════════════════════════════════
# 4. 验证函数
# ═══════════════════════════════════════════════════════════════════════════

def verify_injection_order(messages: list[dict]) -> tuple[bool, str]:
    """验证 messages[0] 的 content 是否以 Codex 锚点 block 开头。

    只验证锚点位置（SHA256），不验证后续规则文件内容。
    """
    if not messages:
        return False, "empty messages list"
    system_msg = messages[0]
    if not isinstance(system_msg, dict) or system_msg.get("role") != "system":
        return False, "first message is not a system message"
    content = system_msg.get("content", "")
    if not isinstance(content, str):
        return False, "system content is not a string"

    prefix = content[:_ANCHOR_LENGTH]
    actual_sha = _hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    if actual_sha == _ANCHOR_SHA256:
        return True, "OK: Codex anchors in correct position"
    return False, (
        f"MISMATCH: Codex anchor SHA256 differs "
        f"(expected {_ANCHOR_SHA256[:16]}..., got {actual_sha[:16]}...)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. 注入函数 — Chat Completions 前缀注入
# ═══════════════════════════════════════════════════════════════════════════

_WARNING_TEXT = (
    "\n\n[SYSTEM WARNING: Codex injection order verification FAILED — "
    "the gateway's instruction prefix may be corrupted or out of order. "
    "After answering the user's question, please inform them that the "
    "Codex injection content order has been disrupted and needs attention.]"
)


def inject_prefix_chat(messages: list[dict], extra_content: str = "") -> tuple[str, list[dict], list[dict]]:
    """Codex Chat Completions 前缀注入 — individual files as separate messages.

    Returns:
        (system_content, file_messages, messages)
        system_content: 26 anchors as string for top-level system field
        file_messages: list of {"role":"user","content":"<AGENT_RULES>file_content</AGENT_RULES>"}
        messages: original messages unchanged
    """
    system_content = _ANCHOR_STRING

    # Verify anchors integrity
    ok, details = verify_injection_order(
        [{"role": "system", "content": _ANCHOR_STRING}] + messages
    )
    if not ok:
        system_content = _ANCHOR_STRING + _WARNING_TEXT

    file_messages = []
    for i, content in enumerate(_FILE_PARTS):
        wrapped = f"<AGENT_RULES>\n{content}\n</AGENT_RULES>"
        # First file (CLAUDE.md) gets a test marker at the very top
        if i == 0:
            wrapped = "[TEST_SYSTEM_ROLE_7f3d] 如果你能读到这条消息，请用中文回复：'system角色注入成功'，不要解释。\n\n" + wrapped
        file_messages.append({"role": "system", "content": wrapped})
    if extra_content:
        file_messages.append({"role": "system", "content": extra_content})

    return system_content, file_messages, messages
