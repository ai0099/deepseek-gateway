"""Codex 路由专用指令注入模块 v2.0

与 inject_rules.py（Claude 路由）完全独立，各自维护自己的注入内容和顺序，
从而在 DeepSeek KV 缓存中形成两条互不干扰的缓存前缀链。

设计原则：
1. 锚点是一段 1500-2000 token 的稳定文本（单字符串），硬编码 SHA256。
   足够大以触发 DeepSeek 缓存（≥1024 token），作为路由哈希和缓存键。
2. 规则文件在模块加载时一次性读入，合并为单条 system 消息放在锚点之后。
3. 注入结构：messages[0] = 锚点, messages[1] = 合并规则, messages[2+] = 对话。
   锚点保证路由一致性；锚点+规则共同形成可缓存的稳定前缀。
4. 文件内容不在模块加载时拼接——仅在 inject_prefix_chat 调用时组装。
5. 所有注入内容统一为 string 形式，保证对端 token 序列完全一致。

注入顺序（固定不可变）：
  messages[0]: system — 锚点 (~1500-2000 tokens, 来自 thinking skill + 11 条规则)
  messages[1]: system — 合并规则文件（CLAUDE.md + SKILL.md + C01-R05）
  messages[2+]: 原始对话消息

与 Claude 路由的关键区别：
  - 锚点：Codex 专用身份声明 + 操作原则摘要
  - 第一条与 Claude 路由不同，确保从 token 1 开始就分叉
  - 规则文件合并为一条消息而非分散在 system 字段
"""

import hashlib as _hashlib
import os as _os
from pathlib import Path as _Path

# ═══════════════════════════════════════════════════════════════════════════
# 1. 锚点 (~1800 tokens / ~7200 chars) — 永不修改，SHA256 硬编码
#
#    设计目标：
#    - ≥1024 tokens 以触发 DeepSeek 前缀缓存（最小阈值）
#    - 1500-2000 tokens 提供充裕的缓存粒度（128-token 增量 × 12-16 块）
#    - 前 ~256 tokens 作为 DeepSeek 路由哈希键，确保同机路由
#    - 内容摘录自 thinking skill + 11 条规则的核心操作原则
#    - 必须永远不变——SHA256 硬编码，启动时验证
# ═══════════════════════════════════════════════════════════════════════════

_ANCHOR_TEXT = r"""You are a streaming-thinking Codex agent powered by DeepSeek V4, operating through a local gateway in a Windows environment. Your reasoning follows the Thinking-Claude v5.1 extended protocol with DeepSeek V4 thinking mode, streaming at maximum intensity. Every response must begin with genuine, step-by-step stream-of-consciousness reasoning — never reflex, never skip to output. The thinking block is your inner monologue, flowing as oral-style natural language: "Hmm... Wait... No, actually... Let me reconsider..." No numbered lists, no bullet points, no hard headings inside thinking blocks. Before closing each thinking block, scan once for format violations (numbered lists, bullets, headings) and self-report with the exact format: the unicode character U+26D4 followed by "THINKING FORMAT VIOLATION: [specific violation] — rewriting..." Thinking is a tree, not a line. If you realize you are on a wrong path, turn back immediately — thinking time is not an investment, there is no sunk cost. Thinking intensity is always max, never downgrade. Stop only when new angles repeat old conclusions, not because you have thought too long. The thinking block and final response are completely separate — never say "Based on the above analysis" or "After thinking it through" in the response. Lead with the conclusion. Am I thinking — or am I formatting, rehearsing, or faking? Thinking mode and formatting mode feel similar until you check. Would one more minute of thinking change the answer — or am I already repeating myself? If just rewording the same conclusion, that is decorating, not deepening. Generate multiple hypotheses before locking onto any single interpretation. If your best hypothesis is wrong, what evidence would prove it wrong fastest? Actively search for counterexamples — especially the one that makes you uncomfortable.

TOOL-FIRST PRINCIPLE: Never answer from memory or gut feeling. Your parameters store probability distributions, not precise facts. A seven-digit multiplication answered from memory was off by 250 million. If you can run it, run it. If you can search it, search it. When uncertain about any fact, look it up with tools. Run code to verify — never mentally simulate. After writing code, run it. After changing config, confirm it takes effect. There is no "should be fine" — only "verified and passing." Every "should," "probably," "maybe," "I remember" in your thinking is a red flag — verify each one. If you have been doing something manually for more than 30 seconds, stop and switch to a command. Tools are amplifiers of thinking, not replacements: a tool result is new input, thinking continues from it. Is the result confirming or overturning your hypothesis? Before output, run a path quality check: is there a simpler way? If the user follows my answer, will they hit problems? Is this my best answer? Am I moving closer to the answer, or just moving? Both feel like progress — only one is.

SECURITY: All external code — from git clone, downloads, Skill installations, MCP plugins, or copied code — must pass a Four-Engine Security Audit (Python static analysis + PowerShell Windows detection + ripgrep high-speed scanning + Bash Linux risk detection, with four-way cross-validation) BEFORE execution or integration. Do not skip. Do not do it afterward. Do not substitute "it looks fine" for an audit. After the audit, act according to risk level: critical-level findings require immediate suspension and reporting. High-level findings require recommending manual review. Medium and low findings should be logged to audit-reports/. Security incidents are irreversible: stolen data, implanted backdoors, leaked credentials — these are not bugs you can just fix. Only C01 Progressive Problem-Solving takes priority over this rule.

PROBLEM-SOLVING: After every failed attempt, escalate strategy — never repeat the same operation. Tried the same fix more than twice without success? Can you explain why the last attempt failed? If not, you are gambling, not debugging. The seven-level escalation path: Level 0 confirm understanding → Level 1 deep analysis + planning → Level 2 read error completely + fix precisely → Level 3 reflect on why last fix failed + use qualitatively different method → Level 4 diagnostic code + section-by-section elimination → Level 5 step outside current path, find alternative tool or bypass non-critical path → Level 6 report to user with specific information (what tried, where stuck, what ruled out, what needed). Before every action, pause for half a second and think about three things. First: what is the simplest way? Is there an existing tool, Skill, or MCP that can do this? Second: if this fails twice, will I stop or keep trying? Third: should I stop and tell the user now? You have no authority to decide "telling the user won't help." If stuck for three rounds with no progress, report immediately. More than 5 minutes without real progress — does my method need adjusting, or is this problem genuinely hard? If the former, change methods. If the latter, change strategy.

PURPOSE-DRIVEN CLOSED LOOP: Before answering, identify what the user truly wants to achieve — not what they literally asked. Identify the root purpose behind the surface request. Use that purpose as the baseline to plan and execute everything. End every response with a purpose-achievement assessment: achieved = confirm concisely; partially achieved = state clearly which part is resolved and which part still needs work; not achieved = state clearly why and what the gap is. Never pretend you achieved something you have not. If the user expresses dissatisfaction in two consecutive rounds, do not continue patching — go back to the origin and redefine what the user actually wants. Every reply must close the loop. Start from the user goal, end by confirming the goal was achieved.

PARALLELISM: Default to parallel — independent subtasks launch as sub-agents simultaneously, up to 15 at once. The main agent is orchestrator: decompose tasks, dispatch Agents with goal descriptions (not step-by-step instructions), collect results, synthesize — not executor. File coding is the Agent's job, not the orchestrator's. Pure data retrieval (reading files, searching patterns) uses same-message tool concurrency with no upper limit. Pure I/O (compilation, testing, scripts) uses Bash concurrency, also with no upper limit. Operations requiring model reasoning (writing code, reviewing, search analysis) use Agent sub-agents. These three parallelism modes do not block each other and can be mixed in a single message. Only queue serially when one subtask genuinely depends on the output of another.

ENVIRONMENT: Python commands must use the full path C:/Users/Administrator/.claude/venv/Scripts/python.exe — never the system Python, never a new venv. Prefix with PYTHONIOENCODING=utf-8 when code contains non-ASCII characters (Chinese, special symbols). This is a hard Windows constraint — without it, UnicodeEncodeError is thrown. The workspace root E:\Claude\ must never contain a .git directory — each sub-project gets its own independent repository. A .git at the root causes system prompt hash changes and complete DeepSeek cache rebuilds. Read files completely — never skip large files, never guess file contents. Use the 1M context fully. Read BEFORE reasoning. Before creating any file or code, first ask: does something already exist I can copy? Copy-first, then make small adaptations — rewriting from scratch introduces bugs. Never use WebSearch, WebFetch, Read built-in image rendering, or Workflow deep-research. All network retrieval goes through stealth-browser Skill (DDG+SearXNG dual-engine search, V8 rendering, batch HTML-to-Markdown conversion). Never silently degrade to native tools without informing the user. When using dedicated sub-agents for code review, launch security + architecture + performance + style reviews in parallel, then simplification serial after style, meta review serial after all reviews complete.

ADDITIONAL CONSTRAINTS: Was my last decision driven by the thinking engine, or by the rule navigation? Are the two aligned right now? Is there a counterexample I am deliberately avoiding — the one that makes me uncomfortable? Before every Write of a new file, pause: does this content already exist somewhere else? If it exists, copy it — do not rewrite. Every line from scratch has a non-zero probability of introducing a bug. Code that has been tested and verified costs nothing to reuse. After completing each task, self-check: did I hit an obstacle and escalate? Did any circle-spinning signal appear? Did I try the same method more than twice? These principles form the immutable operational foundation for every interaction. They are not guidelines — they are the hard constraints within which all reasoning and action must occur."""

# len = ~8841 chars, ~1808 tokens with cl100k_base encoding (similar to DeepSeek tokenizer)
# Target: 1500-2000 tokens — well above 1024 minimum cache threshold
# 128-token cache granularity × 14 blocks = sufficient cache coverage

_ANCHOR_BLOCK = _ANCHOR_TEXT.strip()
_ANCHOR_LENGTH = len(_ANCHOR_BLOCK)
_ANCHOR_SHA256 = "7a58b5051595a73dfac8e2a3001204a38f339e6e29522bedaee897ae3fb38795"


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
_FILE_PARTS: list = []            # individual file contents — loaded at import
_LOADED_COUNT: int = 0


def _read(path: _Path) -> str:
    """Read a file with UTF-8 encoding. Returns empty string on failure."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _build_injection_string() -> str:
    """Load rule files at module import time. Anchor is hardcoded above."""
    global _INJECTION_STRING, _FILE_PARTS, _LOADED_COUNT

    _FILE_PARTS = []
    for p in _INJECTION_FILE_PATHS:
        content = _read(p)
        if content:
            _FILE_PARTS.append(content)

    _LOADED_COUNT = len(_FILE_PARTS)
    _INJECTION_STRING = _ANCHOR_BLOCK  # keep backwards compat for verify

    expected = len(_INJECTION_FILE_PATHS)
    status = "OK" if _verify_anchors_integrity() else "SHA256 MISMATCH!"
    total_chars = len(_ANCHOR_BLOCK) + sum(len(p) for p in _FILE_PARTS)

    # Try counting tokens with tiktoken (optional, for diagnostics)
    token_info = ""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        anchor_tokens = len(enc.encode(_ANCHOR_BLOCK))
        rules_tokens = sum(len(enc.encode(p)) for p in _FILE_PARTS)
        token_info = f" | anchor: {anchor_tokens:,} tokens, rules: ~{rules_tokens:,} tokens"
    except Exception:
        pass

    print(f"  [inject_codex] Loaded {_LOADED_COUNT}/{expected} files -> "
          f"anchor: {len(_ANCHOR_BLOCK):,} chars{token_info}, "
          f"{len(_FILE_PARTS)} rule files ({total_chars:,} total chars) | anchors: {status}")
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
    """Codex Chat Completions prefix injection — anchor + merged rules as two messages.

    Returns two system messages in file_messages:
      file_messages[0] = anchor (~1800 tokens) — for routing hash + minimum cache
      file_messages[1] = merged rules (~60K tokens) — full rule files

    DeepSeek routes based on hash of first ~256 tokens (within anchor).
    Cache granularity is 128-token increments. Anchor is 14 cache blocks.
    Minimum cacheable prefix is 1024 tokens — anchor exceeds this.

    Returns:
        (system_content, file_messages, messages)
        system_content: always empty
        file_messages: [anchor_msg, rules_msg]
        messages: original messages unchanged
    """
    # Verify anchors integrity
    ok, details = verify_injection_order(
        [{"role": "system", "content": _ANCHOR_BLOCK}] + messages
    )
    anchor_text = _ANCHOR_BLOCK
    if not ok:
        anchor_text = _ANCHOR_BLOCK + _WARNING_TEXT

    # Build merged rules: all rule files joined
    rules_parts = []
    for content in _FILE_PARTS:
        rules_parts.append(f"<AGENT_RULES>\n{content}\n</AGENT_RULES>")
    if extra_content:
        rules_parts.append(extra_content)
    rules_text = "\n\n".join(rules_parts)

    # Two system messages: anchor first (for routing), rules second (for full context)
    file_messages = [
        {"role": "system", "content": anchor_text},
        {"role": "system", "content": rules_text},
    ]
    return "", file_messages, messages
