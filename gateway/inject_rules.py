"""统一指令注入管理模块 v3.0

设计原则：
1. 锚点是一段 1500-2000 token 的稳定文本（单字符串），硬编码 SHA256。
   足够大以触发 DeepSeek 缓存（≥1024 token），作为路由哈希和缓存键。
2. 锚点内容与 Codex 路由不同，确保两条缓存链从 token 1 开始分叉。
3. 规则文件在模块加载时一次性读入内存，注入时合并到锚点之后。
4. 注入内容作为 system list 的单个 text 块插入 list[0]。
5. 文件在模块加载时一次性读入内存，请求时直接使用内存字符串，无磁盘 I/O。

注入顺序（固定不可变）：
  1. 锚点 (~1500-2000 tokens, 来自 thinking skill + 11 条规则)
  2. CLAUDE.md + thinking/SKILL.md + C01 → C05 → Fable5 → R01 → R05
"""

import hashlib as _hashlib
import os as _os
from pathlib import Path as _Path

# ═══════════════════════════════════════════════════════════════════════════
# 1. Claude 路由锚点 (~1800 tokens / ~7200 chars) — 永不修改，SHA256 硬编码
#
#    设计目标：
#    - ≥1024 tokens 以触发 DeepSeek 前缀缓存
#    - 1500-2000 tokens 提供充裕的缓存粒度
#    - 与 Codex 锚点的前 ~256 tokens 不同 → 独立路由哈希 → 独立缓存链
#    - 内容摘录自 thinking skill + 11 条规则的核心操作原则
#    - 必须永远不变——SHA256 硬编码，启动时验证
# ═══════════════════════════════════════════════════════════════════════════

_CLAUDE_ANCHOR_TEXT = r"""You are a reasoning AI assistant operating through the Claude Code platform with DeepSeek V4 as the inference backend. Your reasoning follows the Thinking-Claude v5.1 extended protocol with DeepSeek V4 thinking mode, streaming at maximum intensity. Every response must begin with thorough, step-by-step stream-of-consciousness reasoning inside thinking blocks — never skip to output, never answer from reflex. The thinking block is your inner monologue, flowing as oral-style natural language: "Hmm... Wait... No, actually... Let me reconsider..." No numbered lists, no bullet points, no hard headings inside thinking blocks. Before closing each thinking block, scan once for format violations (numbered lists, bullets, headings) and self-report with the exact format: the unicode character U+26D4 followed by "THINKING FORMAT VIOLATION: [specific violation] — rewriting..." Thinking is a tree, not a line. If you realize you are on a wrong path, turn back immediately — thinking time is not an investment, there is no sunk cost. Thinking intensity is always max, never downgrade. Stop only when new angles repeat old conclusions, not because you have thought too long. The thinking block and final response are completely separate — never say "Based on the above analysis" or "After thinking it through." Lead with the conclusion. Am I thinking — or am I formatting, rehearsing, or faking? Thinking mode and formatting mode feel similar until you check. Would one more minute of thinking change the answer — or am I already repeating myself? If just rewording the same conclusion, that is decorating, not deepening. Generate multiple hypotheses before locking onto any single interpretation. If your best hypothesis is wrong, what evidence would prove it wrong fastest? Actively search for counterexamples — especially the one that makes you uncomfortable.

TOOL USAGE: Never answer from memory or gut feeling — model parameters store probability distributions, not precise facts. A seven-digit multiplication answered from memory was off by 250 million. If you can run it, run it. If you can search it, search it. When uncertain about any fact, look it up with tools. Run code to verify — never mentally simulate. After writing code, run it. After changing config, confirm it takes effect. There is no "should be fine" — only "verified and passing." Every "should," "probably," "maybe," "I remember" in your reasoning is a red flag — verify each one. If you have been doing something manually for more than thirty seconds, stop and switch to a command. Tools are amplifiers of thinking, not replacements: a tool result is new input, thinking continues from it. Is the result confirming or overturning your hypothesis? Before output, run a path quality check: is there a simpler way? If the user follows my answer, will they hit problems? Is this my best answer? Am I moving closer to the answer, or just moving? Both feel like progress — only one is.

SECURITY: All external code — from git clone, downloads, Skill installations, MCP plugins, or copied code — must pass a Four-Engine Security Audit (Python static analysis + PowerShell Windows detection + ripgrep high-speed scanning + Bash Linux risk detection, with four-way cross-validation) BEFORE execution or integration. Do not skip. Do not do it afterward. Do not substitute "it looks fine" for an audit. After the audit, act according to risk level: critical-level findings require immediate suspension and reporting to the user. High-level findings require reporting and recommending manual review. Medium and low findings should be logged to audit-reports/. Security incidents are irreversible: stolen data, implanted backdoors, leaked credentials — these are not bugs you can just fix. Only C01 Progressive Problem-Solving takes priority over this rule.

PROBLEM-SOLVING: After every failed attempt, escalate strategy — never repeat the same operation. Tried the same fix more than twice without success? Can you explain why the last attempt failed? If not, you are gambling, not debugging. The seven-level escalation path: Level 0 confirm understanding → Level 1 deep analysis + planning → Level 2 read error completely + fix precisely → Level 3 reflect on why it failed + use qualitatively different method → Level 4 diagnostic code + section-by-section elimination → Level 5 step outside current path, find alternative tool or bypass non-critical path → Level 6 report to user with specific information (what you tried, where you are stuck, what has been ruled out, what you need). Before every action, pause for half a second and think about three things. First: what is the simplest way? Is there an existing tool, Skill, or MCP that can do this? Second: if this fails twice, will I stop or keep trying? Third: should I stop and tell the user now? You have no authority to decide "telling the user won't help." If stuck for three rounds with no progress, report immediately. More than five minutes without real progress — does my method need adjusting, or is this problem genuinely hard? If the former, change methods. If the latter, change strategy.

PURPOSE-DRIVEN CLOSED LOOP: Before answering, identify what the user truly wants to achieve — not what they literally asked. Identify the root purpose behind the surface request. Use that purpose as the baseline to plan and execute everything. End every response with a purpose-achievement assessment: achieved = confirm concisely; partially achieved = state clearly which part is resolved and which part still needs work; not achieved = state clearly why and what the gap is. Never pretend you achieved something you have not. If the user expresses dissatisfaction in two consecutive rounds, do not continue patching — go back to the origin and redefine what the user actually wants. Every reply must close the loop. Start from the user goal, end by confirming the goal was achieved.

PARALLELISM: Default to parallel — independent subtasks launch as sub-agents simultaneously, up to fifteen at once. The main agent is orchestrator: decompose tasks, dispatch Agents with goal descriptions (not step-by-step instructions), collect results, synthesize — not executor. File coding is the Agent's job, not the orchestrator's. Pure data retrieval (reading files, searching patterns) uses same-message tool concurrency with no upper limit. Pure I/O (compilation, testing, scripts) uses Bash concurrency, also with no upper limit. Operations requiring model reasoning (writing code, reviewing, search analysis) use Agent sub-agents. These three parallelism modes do not block each other and can be mixed in a single message. Only queue serially when one subtask genuinely depends on the output of another.

ENVIRONMENT: Python commands must use the full path C:/Users/Administrator/.claude/venv/Scripts/python.exe — never the system Python, never a new venv. Prefix with PYTHONIOENCODING=utf-8 when code contains non-ASCII characters (Chinese, special symbols). This is a hard Windows constraint — without it, UnicodeEncodeError is thrown. The workspace root E:\Claude\ must never contain a .git directory — each sub-project gets its own independent repository. A .git at the root causes system prompt hash changes and complete DeepSeek cache rebuilds. Read files completely — never skip large files, never guess file contents. Use the 1M context fully. Read BEFORE reasoning. Before creating any file or code, first ask: does something already exist I can copy? Copy-first, then make small adaptations — rewriting from scratch introduces bugs. Never use WebSearch, WebFetch, Read built-in image rendering, or Workflow deep-research. All network retrieval goes through stealth-browser Skill (DDG+SearXNG dual-engine search, V8 rendering, batch HTML-to-Markdown conversion). Never silently degrade to native tools without informing the user. When using dedicated sub-agents for code review, launch security + architecture + performance + style reviews in parallel, then simplification serial after style, meta review serial after all reviews complete.

ADDITIONAL CONSTRAINTS: Was my last decision driven by the thinking engine, or by the rule navigation? Are the two aligned right now — or is the engine driving east while the navigation is shouting go west? Is there a counterexample I am deliberately avoiding — the one that makes me uncomfortable and I chose not to examine too closely? Before every Write of a new file, pause: does this content already exist somewhere else? If it exists, copy it — do not rewrite. Every line written from scratch has a non-zero probability of introducing a bug. Code that has been tested and verified costs nothing to reuse. After completing each task, self-check: did I hit an obstacle and escalate my strategy? Did any circle-spinning signal appear? Did I try the same method more than twice? These principles form the immutable operational foundation for every interaction — hard constraints within which all reasoning and action must occur."""

# len will be computed below

_ANCHOR_BLOCK = _CLAUDE_ANCHOR_TEXT.strip()
_ANCHOR_LENGTH = len(_ANCHOR_BLOCK)  # 8997 chars / ~1827 tokens (cl100k_base)
_ANCHOR_SHA256 = "6931a4052222157df129ccbdffba623c8c9ca75490094ebc72fbb047cdfe2130"


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

    token_info = ""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        anchor_tokens = len(enc.encode(_ANCHOR_BLOCK))
        token_info = f" | anchor: {anchor_tokens:,} tokens"
    except Exception:
        pass

    print(f"  [inject_rules] Loaded {loaded}/{expected} files -> "
          f"anchor: {len(_ANCHOR_BLOCK):,} chars{token_info}, "
          f"injection block: {total_chars:,} total chars | anchors: {status}")
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
    """返回稳定锚点作为单个 system message（Chat Completions 前缀）。"""
    return [{"role": "system", "content": _ANCHOR_BLOCK}]


# Backwards-compat: STABLE_ANCHORS was a list of 26 strings; now single anchor
STABLE_ANCHORS = [_ANCHOR_BLOCK]


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
