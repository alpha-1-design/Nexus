"""Agent orchestrator - the core loop connecting AI, tools, and memory."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent.reflection import ReflectionEngine
from ..agents import AgentRole, MultiAgentTeam
from ..errors import NexusError
from ..memory import Memory
from ..plugins import get_plugin_manager
from ..providers import Message, ProviderManager, ToolCall
from ..safety import get_safety_engine
from ..skills.matcher import format_relevant_skills, get_skill_matcher
from ..thinking import ThinkingState, get_thinking_engine
from ..tools import ToolRegistry, ToolResult
from ..utils import format_error
from ..utils.validation import get_tool_failure_hint, run_refiners_fire


def estimate_tokens(text: str) -> int:
    """Estimate token count using a simple approximation (4 chars per token)."""
    if not text:
        return 0
    return len(re.findall(r"\w+", text)) + (len(text) // 4)


def prune_messages(
    messages: list[Message],
    max_tokens: int,
    prune_ratio: float = 0.8,
) -> list[Message]:
    """Prune messages when approaching token limit.

    Keeps recent messages and summarizes old ones.
    """
    if not messages:
        return messages

    current_tokens = sum(estimate_tokens(m.content or "") for m in messages)
    if current_tokens <= max_tokens:
        return messages

    system_messages = [m for m in messages if m.role == "system"]
    other_messages = [m for m in messages if m.role != "system"]

    while estimate_tokens("".join(m.content or "" for m in messages)) > max_tokens * prune_ratio and len(other_messages) > 2:
        msg_to_summarize = other_messages[0]
        summary = f"[Earlier: {msg_to_summarize.role} said {msg_to_summarize.content[:100]}...]"

        summary_msg = Message(
            role="system",
            content=summary,
        )
        system_messages.append(summary_msg)

        other_messages = other_messages[1:]

    return system_messages + other_messages


# ── Context compaction (chars/4 estimate + LLM summarization) ────────────
DEFAULT_CONTEXT_WINDOW = 128_000
COMPACT_THRESHOLD_RATIO = 0.6   # compact when est. tokens >= 60% of window
COMPACT_MESSAGE_TRIGGER = 40    # ...or when history grows past this many msgs
COMPACT_KEEP_RECENT = 8         # recent messages always kept verbatim

COMPACT_SYSTEM_PROMPT = (
    "You are a conversation compactor. Summarize the transcript below for an "
    "AI assistant that must continue the work with no other context. Capture: "
    "the user's goals and stated preferences, key decisions, important facts "
    "and file paths, tool actions taken and their outcomes, and the current "
    "state / next steps. Be terse and factual; no preamble; maximum 300 words."
)


def _msg_role(m: Any) -> str:
    """Role of a Message or a session-store dict entry."""
    return m.get("role") if isinstance(m, dict) else m.role


def _msg_content(m: Any) -> str:
    """Content of a Message or a session-store dict entry."""
    return (m.get("content") if isinstance(m, dict) else m.content) or ""


def estimate_context_tokens(messages: list) -> int:
    """Estimate request-context size in tokens with the chars/4 heuristic."""
    return sum(len(_msg_content(m)) for m in messages) // 4


def kept_suffix(messages: list, keep: int = COMPACT_KEEP_RECENT) -> list:
    """Trailing slice kept verbatim, protocol-valid at its front edge.

    Keeps the last `keep` messages. If the slice starts with dangling
    role="tool" results whose tool_calls message was summarized away, they
    are trimmed so the compacted transcript stays protocol-valid.
    """
    if not messages:
        return []
    kept = list(messages[-keep:])
    while kept and _msg_role(kept[0]) == "tool":
        kept.pop(0)
    return kept


def summarize_messages_deterministic(messages: list, instructions: str | None = None) -> str:
    """Deterministic compaction summary used when no provider is available
    or the summarize call fails: first line of each turn plus tool actions."""
    lines = []
    for m in messages:
        role = _msg_role(m)
        first = _msg_content(m).strip().split("\n", 1)[0][:150]
        if role == "tool":
            name = m.get("name") if isinstance(m, dict) else m.name
            if first:
                lines.append(f"tool {name or ''}: {first}")
        elif first:
            lines.append(f"{role}: {first}")
    out = "Deterministic summary (LLM summarizer unavailable):\n" + "\n".join(lines[:80])
    if instructions:
        out += f"\nFocus requested: {instructions}"
    return out


@dataclass
class AgentConfig:
    """Configuration for the agent orchestrator."""

    model: str | None = None
    provider_name: str | None = None
    max_turns: int = 50
    max_tool_calls: int = 100
    stream: bool = True
    verbose: bool = False
    reflection_enabled: bool = True
    reflection_max_retries: int = 3
    system_prompt: str | None = None
    max_context_tokens: int = 128000
    context_prune_ratio: float = 0.8
    memory_recall_enabled: bool = True
    memory_recall_limit: int = 3
    memory_auto_persist: bool = True


@dataclass
class Turn:
    """Represents a single turn in the conversation."""

    user_message: str
    assistant_message: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reflection: str | None = None
    tokens_used: int = 0
    duration_ms: int = 0
    error: str | None = None
    pending_approval: dict[str, Any] | None = None
    # Names of bundled skills matched for this turn's task (skill
    # pre-loading), empty when nothing scored above the relevance threshold.
    skills_loaded: list[str] = field(default_factory=list)


class AgentOrchestrator:
    """
    The core agent loop: receives user input, calls the AI provider with tool
    definitions, executes tools, injects results, and repeats until the AI
    produces a final response.

    Combines patterns from:
    - Claude Code: tool-call → execute → inject → repeat
    - OpenCode: compact agent for context reduction
    - nexus_v2: reflection loop on errors with retry
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
        tool_registry: ToolRegistry,
        memory: Memory | None = None,
        config: AgentConfig | None = None,
    ):
        self.pm = provider_manager
        self.tools = tool_registry
        self.memory = memory
        self.config = config or AgentConfig()
        self.reflection_engine = ReflectionEngine(Path("./.nexus/sessions"))

        # ... rest of init ...
        self._tool_call_count = 0
        self._messages: list[Message] = []
        self._history: list[Turn] = []

        # Seed the transcript with generated project context (NEXUS.md) so
        # every turn knows the workspace even before any tool runs.
        _project_ctx = self._load_project_context()
        if _project_ctx:
            self._messages.append(
                Message(role="system", content="### PROJECT CONTEXT (NEXUS.md)\n" + _project_ctx)
            )
        self._thinking = get_thinking_engine()
        self._thinking_callback = None
        self._ui_callback: Callable[[str, Any], None] | None = None  # UI Bridge
        self._total_tokens = 0
        self._turn_count = 0
        self._tool_stats: dict[str, dict] = {}
        self._team: MultiAgentTeam | None = None
        # Skill pre-loading usage: name -> number of turns it was matched in.
        self._skill_usage: dict[str, int] = {}

        # ── memory layer ──────────────────────────────────────
        # `self.memory` (structured facts/sessions) was previously stored
        # but never actually consulted anywhere in the turn loop -- facts
        # were never recalled, no semantic memory was queried, and
        # conversations were never persisted to a session automatically.
        # This wires those pieces (Memory facts, VectorMemory semantic
        # recall, Session auto-persistence) into the actual request loop.
        self._vector_memory = None
        self._session = None
        if self.memory is not None:
            try:
                self._session = self.memory.create_session()
            except Exception:
                self._session = None

    def set_ui_callback(self, callback: Callable[[str, Any], None]):
        """Set a callback to notify the UI of state changes."""
        self._ui_callback = callback

    def _notify_ui(self, event_type: str, data: Any):
        """Notify the UI of a state change."""
        if self._ui_callback:
            self._ui_callback(event_type, data)

    @property
    def team(self) -> MultiAgentTeam | None:
        return self._team

    def spawn_agent(self, role: AgentRole, name: str | None = None, task: str | None = None, model: str | None = None):
        """Spawn an agent with the given role."""
        if self._team is None:
            self._team = self.init_team()
        return self._team.spawn(role, name=name, task=task, model=model)

    def get_team(self) -> MultiAgentTeam | None:
        """Get the current team."""
        return self._team

    def _load_project_context(self) -> str | None:
        """Read NEXUS.md from the workspace (capped) for system-prompt context."""
        try:
            from ..project import load_nexus_md

            return load_nexus_md(Path.cwd())
        except Exception:
            return None

    def set_system_prompt(self, prompt: str) -> None:
        """Set or update the system prompt."""
        project_ctx = self._load_project_context()
        if project_ctx:
            prompt = prompt + "\n\n### PROJECT CONTEXT (NEXUS.md)\n" + project_ctx
        self._messages = [Message(role="system", content=prompt)]
        if self.config.system_prompt:
            self._messages[0] = Message(role="system", content=self.config.system_prompt + "\n\n" + prompt)

    # ── context compaction ──────────────────────────────────────────────
    def context_window_tokens(self) -> int:
        """Provider context window: config override where available, else 128k."""
        return int(getattr(self.config, "max_context_tokens", 0) or 0) or DEFAULT_CONTEXT_WINDOW

    def context_usage(self) -> dict[str, Any]:
        """Estimated tokens / window / message count for /context-style surfaces."""
        window = self.context_window_tokens()
        est = estimate_context_tokens(self._messages)
        return {
            "tokens": est,
            "window": window,
            "ratio": (est / window) if window else 0.0,
            "messages": len(self._messages),
        }

    def should_compact(self) -> bool:
        """True when history is over the compaction threshold (>= 60% of the
        estimated context window, or more than COMPACT_MESSAGE_TRIGGER messages)."""
        usage = self.context_usage()
        return usage["messages"] > COMPACT_MESSAGE_TRIGGER or (
            bool(usage["window"]) and usage["ratio"] >= COMPACT_THRESHOLD_RATIO
        )

    async def compact_context(self, instructions: str | None = None, force: bool = False) -> dict[str, Any] | None:
        """Compact conversation history into one system summary message.

        Keeps the last ~COMPACT_KEEP_RECENT messages verbatim (anchored at a
        user turn so the transcript stays protocol-valid), summarizes the
        older messages via the provider manager with a strict summarization
        prompt, and replaces them with a single `[context compacted]` system
        message. Falls back to a deterministic summary when the provider is
        unavailable or the summarize call fails. The persisted session is
        rewritten atomically to mirror the compaction.

        Only invoked between rounds / at turn start — never mid-tool-round.
        Returns an info dict, or None when nothing needed compacting.
        """
        if not force and not self.should_compact():
            return None

        kept = kept_suffix(self._messages, COMPACT_KEEP_RECENT)
        head = self._messages[: len(self._messages) - len(kept)]
        older = [m for m in head if m.role != "system"]
        if not older:
            return None

        summary = await self._summarize_messages(older, instructions)
        compacted = Message(role="system", content=f"[context compacted] {summary}")
        self._messages = [m for m in head if m.role == "system"] + [compacted] + kept

        # Keep the persisted session valid: mirror the compaction and write
        # atomically (Memory.save_session uses temp + os.replace).
        if self.memory is not None and self._session is not None:
            try:
                kept_session = kept_suffix(self._session.messages, COMPACT_KEEP_RECENT)
                self._session.messages = [{"role": "system", "content": compacted.content}] + kept_session
                self.memory.save_session(self._session)
            except Exception:
                pass

        info = {
            "compacted": len(older),
            "remaining": len(self._messages),
            "auto": not force,
            "summary": summary[:200],
        }
        line = f"◈ context compacted: {info['compacted']} → {info['remaining']} messages"
        step = self._thinking.start_step(
            ThinkingState.ANALYZING,
            line,
            detail="Context threshold reached; older history summarized into one system message.",
        )
        self._thinking.finish_step(step, result=summary[:200])
        # UI bridge: surfaces as a system message in TUI state.
        self._notify_ui("system_message", {"content": line})
        return info

    async def _summarize_messages(self, messages: list, instructions: str | None = None) -> str:
        """Summarize older turns via the provider, with a deterministic fallback."""
        transcript = "\n".join(f"{_msg_role(m)}: {_msg_content(m).strip()[:400]}" for m in messages)
        prompt = COMPACT_SYSTEM_PROMPT
        if instructions:
            prompt += f"\nExtra focus from the user: {instructions}"
        try:
            response = await self.pm.complete(
                messages=[Message(role="system", content=prompt), Message(role="user", content=transcript)],
                provider_name=self.config.provider_name,
            )
            text = (response.content or "").strip()
            if text:
                return text[:1200]
        except Exception:
            pass
        return summarize_messages_deterministic(messages, instructions)

    async def run(self, user_input: str, stream_callback=None, thinking_callback=None) -> Turn:
        """Run a single interaction turn."""
        start = time.monotonic()
        turn = Turn(user_message=user_input)
        self._turn_count += 1

        # TUI convenience: app.py routes unhandled slash commands to the
        # engine, so answer /skills lookups here without burning an LLM
        # call. (The interactive REPL handles /skills itself first.)
        if user_input.strip().lower().startswith("/skills"):
            return await self._run_skills_lookup(user_input)

        if thinking_callback:
            self._thinking_callback = thinking_callback
            self._thinking.on_update(thinking_callback)

        self._thinking.clear()
        analyze_step = self._thinking.start_step(
            ThinkingState.ANALYZING, "Analyzing task...", detail=f"Input: {user_input[:100]}..." if len(user_input) > 100 else f"Input: {user_input}"
        )

        # Context compaction at turn start (never mid-tool-round): when the
        # estimated context is over threshold, summarize older history into
        # one system message before this turn is appended.
        try:
            await self.compact_context()
        except Exception:
            pass

        self._messages.append(Message(role="user", content=user_input))

        # Skill pre-loading: match bundled skills for this task before the
        # first LLM call and inject a compact section, the same way memory
        # recall is inserted (as a system note before this turn's user
        # message, subject to normal pruning — nothing lingers into later
        # turns that no longer applies).
        skill_matches = self.match_skills(user_input)
        if skill_matches:
            turn.skills_loaded = [m.name for m in skill_matches]
            for name in turn.skills_loaded:
                self._skill_usage[name] = self._skill_usage.get(name, 0) + 1
            skill_note = format_relevant_skills(skill_matches)
            if skill_note:
                self._messages.insert(
                    len(self._messages) - 1,
                    Message(role="system", content=skill_note),
                )
            # Visible in the TUI: the ThinkingEngine feeds the ThinkingPanel,
            # so a matched turn shows one dim "⚡ N skills loaded" step.
            skill_step = self._thinking.start_step(
                ThinkingState.ANALYZING,
                f"⚡ {len(skill_matches)} skills loaded: {', '.join(turn.skills_loaded)}",
                detail="Matched from the bundled skill library before the first LLM call.",
            )
            self._thinking.finish_step(skill_step, result="activate with /skill <name>")
        self._notify_ui("skills_loaded", {"skills": turn.skills_loaded})

        memory_note = await self._build_memory_context(user_input)
        if memory_note:
            # Inserted as a system-role note right before this turn's user
            # message. It stays in history like any other message and is
            # subject to the same context pruning as everything else, so it
            # doesn't need special-case cleanup.
            self._messages.insert(len(self._messages) - 1, Message(role="system", content=memory_note))

        self._thinking.update_step(analyze_step, confidence=0.85)
        self._thinking.finish_step(analyze_step, result="Analyzed user input")

        planning_step = self._thinking.start_step(ThinkingState.PLANNING, "Planning tool sequence...", detail=f"Available tools: {len(self.tools.list_all())}")
        self._thinking.update_step(planning_step, confidence=0.75)
        self._thinking.finish_step(planning_step, result="Tool sequence planned")

        try:
            result = await self._run_loop(stream_callback)
            turn.assistant_message = result["message"]
            turn.tool_calls = result.get("tool_calls", [])
            if result.get("pending_approval"):
                turn.pending_approval = result["pending_approval"]

            review_step = self._thinking.start_step(
                ThinkingState.REVIEWING, "Generating response...", detail=f"Response length: {len(result['message'])} chars"
            )
            self._thinking.update_step(review_step, confidence=0.90)
            self._thinking.finish_step(review_step, result=result["message"][:200])

            complete_step = self._thinking.start_step(
                ThinkingState.COMPLETE, "Task complete", detail=f"Completed in {len(result.get('tool_calls', []))} tool calls"
            )
            self._thinking.finish_step(complete_step, result=result["message"][:200])
            turn.tokens_used = self._total_tokens
        except Exception as e:
            turn.error = str(e)
            turn.assistant_message = f"Error: {e}"

        turn.duration_ms = int((time.monotonic() - start) * 1000)
        self._history.append(turn)
        await self._persist_turn(turn)
        return turn

    def _get_vector_memory(self):
        """Lazily create the semantic memory store, scoped under the same
        memory directory as structured facts/sessions."""
        if self._vector_memory is None and self.memory is not None:
            try:
                from ..memory.vectors import VectorMemory

                db_path = str(self.memory.memory_dir / "vector_memory.db")
                self._vector_memory = VectorMemory(db_path=db_path)
            except Exception:
                self._vector_memory = False  # sentinel: creation failed, don't retry every turn
        return self._vector_memory or None

    def match_skills(self, task_text: str, top_n: int | None = None) -> list:
        """Match bundled skills for a task via the shared keyword matcher.

        Never raises: a broken skills install must not break a turn, so
        failures degrade to "no skills matched".
        """
        try:
            return get_skill_matcher().match(task_text, top_n=top_n)
        except Exception:
            return []

    def skill_note_for(self, task_text: str) -> str | None:
        """The `### RELEVANT SKILLS` prompt section for a task, or None when
        nothing scores above the relevance threshold (injection is opt-in
        on real matches only — no context noise on unrelated tasks)."""
        note = format_relevant_skills(self.match_skills(task_text))
        return note or None

    def get_skill_usage(self) -> dict[str, int]:
        """Skill-name -> turns-matched count, for /stats-style surfaces."""
        return dict(self._skill_usage)

    async def _run_skills_lookup(self, raw_input: str) -> Turn:
        """Answer a /skills slash command that reached the engine (the TUI
        passes unknown slash commands to run()). Lists matched skills for
        the given task — or a catalog summary without args — without an
        LLM call."""
        turn = Turn(user_message=raw_input)
        parts = raw_input.strip().split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        if args:
            matches = self.match_skills(args)
            if matches:
                lines = [f"⚡ {len(matches)} skills matched for “{args}”:"]
                for m in matches:
                    desc = " ".join(m.description.split())[:90]
                    lines.append(f"  • {m.name} — {desc}")
                lines.append("Load one with /skill <name>.")
            else:
                lines = [f"No bundled skills matched “{args}”."]
        else:
            try:
                matcher = get_skill_matcher()
                count = matcher.skill_count or matcher.build_index()
                cats = ", ".join(matcher.categories()[:12])
                lines = [
                    f"{count} bundled skills available.",
                    f"Categories: {cats}…",
                    "Match skills for a task with /skills <task>.",
                ]
            except Exception:
                lines = ["Skill catalog unavailable."]
        turn.assistant_message = "\n".join(lines)
        self._history.append(turn)
        return turn

    async def _build_memory_context(self, user_input: str) -> str | None:
        """Assemble a compact memory-recall note for this turn: relevant
        stored facts plus the top semantically-related past exchanges.
        Returns None if there's nothing useful to add (no memory configured,
        recall disabled, or nothing relevant found) so we don't pollute the
        context with an empty note.
        """
        if self.memory is None or not self.config.memory_recall_enabled:
            return None

        parts: list[str] = []

        try:
            facts = self.memory.get_all_facts()
            if facts:
                fact_lines = [f"- {k}: {v}" for k, v in list(facts.items())[:10]]
                parts.append("Known facts about the user/project:\n" + "\n".join(fact_lines))
        except Exception:
            pass

        vm = self._get_vector_memory()
        if vm is not None:
            try:
                entries = await vm.recall(user_input, limit=self.config.memory_recall_limit)
                if entries:
                    mem_lines = [f"- {e.content[:200]}" for e in entries]
                    parts.append("Relevant past context:\n" + "\n".join(mem_lines))
            except Exception:
                pass

        if not parts:
            return None
        return "[Memory recall -- for context only, not user-visible]\n" + "\n\n".join(parts)

    async def _persist_turn(self, turn: Turn) -> None:
        """Auto-persist the turn to the current session and index it in
        semantic memory, so future turns (and future sessions) can recall
        it. Best-effort: memory persistence should never break a
        conversation turn that otherwise succeeded.
        """
        if self.memory is None or not self.config.memory_auto_persist:
            return

        try:
            if self._session is not None:
                self._session.messages.append({"role": "user", "content": turn.user_message})
                self._session.messages.append({"role": "assistant", "content": turn.assistant_message})
                self._session.tools_used = list({tc.get("name", "") for tc in turn.tool_calls} | set(self._session.tools_used))
                self.memory.save_session(self._session)
        except Exception:
            pass

        vm = self._get_vector_memory()
        if vm is not None and turn.assistant_message and not turn.error:
            try:
                await vm.store(
                    f"User: {turn.user_message}\nAssistant: {turn.assistant_message[:500]}",
                    metadata={"session_id": self._session.id if self._session else None},
                )
            except Exception:
                pass

    async def _run_loop(self, stream_callback=None) -> dict[str, Any]:
        """The main agent loop."""
        tool_defs = self.tools.to_openai_format()
        provider_name = self.config.provider_name
        accumulated = ""
        tool_calls_accumulated: list[ToolCall] = []

        while self._turn_count <= self.config.max_turns:
            if self._tool_call_count >= self.config.max_tool_calls:
                raise RuntimeError(f"Max tool calls ({self.config.max_tool_calls}) reached")

            if self.config.max_context_tokens > 0:
                self._messages = prune_messages(
                    self._messages,
                    self.config.max_context_tokens,
                    self.config.context_prune_ratio,
                )

            if self.config.stream and stream_callback and not tool_defs:
                tool_calls_accumulated = []
                accumulated = ""

                async for chunk in self.pm.stream(
                    messages=self._messages,
                    tools=tool_defs,
                    provider_name=provider_name,
                ):
                    if chunk.tool_call:
                        tool_calls_accumulated.append(chunk.tool_call)
                    elif chunk.content:
                        accumulated += chunk.content
                        stream_callback(chunk.content)
                    if chunk.done:
                        break

                response_content = accumulated
                tool_calls = tool_calls_accumulated
            else:
                response = await self.pm.complete(
                    messages=self._messages,
                    tools=tool_defs,
                    provider_name=provider_name,
                )
                response_content = response.content or ""
                tool_calls = response.tool_calls

            if not tool_calls:
                self._messages.append(Message(role="assistant", content=response_content))
                return {"message": response_content, "tool_calls": []}

            for tc in tool_calls:
                self._tool_call_count += 1
                tool_result = await self._execute_tool(tc, turn=Turn(user_message=""))

                # --- INTERACTIVE APPROVAL FLOW ---
                # If the tool returns a result requiring human approval (like a diff)
                if isinstance(tool_result, ToolResult) and tool_result.metadata and tool_result.metadata.get("action") == "require_approval":
                    # This is where we pause the loop and wait for user input.
                    # In the TUI, this will trigger a modal/prompt.
                    # For the orchestrator, we return a special signal to the TUI.
                    return {
                        "message": f"I have a proposed change for {tool_result.metadata['path']}. Please review the diff.",
                        "tool_calls": [],
                        "pending_approval": {"tool_name": tc.name, "args": tc.arguments, "result": tool_result},
                    }

                tool_msg = f"Tool result for {tc.name}: {tool_result.content}"
                if tool_result.error:
                    tool_msg += f"\nError: {tool_result.error}"
                self._messages.append(Message(role="user", content=tool_msg))

            if self.config.verbose:
                print(f"\n[nexus] Turn {self._turn_count}, tool calls: {len(tool_calls)}")

        raise RuntimeError(f"Max turns ({self.config.max_turns}) reached without final response")

    async def _execute_tool(self, tool_call, turn: Turn, depth: int = 0) -> ToolResult:
        """Execute a tool call with safety checks and reflection on failure."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        exec_step = self._thinking.start_step(ThinkingState.EXECUTING, f"Calling tool: {name}", tool_name=name, tool_args=args)

        # 1. Safety Check
        safety = get_safety_engine()
        context = {"tool": name, "args": args}
        for path_key in ("path", "filePath", "file_path"):
            if path_key in args:
                context["path"] = args[path_key]
                break
        if "command" in args:
            context["command"] = args["command"]

        violations = safety.check(context)
        proceed, reason = safety.should_proceed(violations)
        if not proceed:
            self._thinking.finish_step(exec_step, error=f"Safety Block: {reason}")
            return ToolResult(success=False, content=f"Blocked by Safety: {reason}", error=reason)

        if name == "Read":
            for path_key in ("path", "filePath", "file_path"):
                if path_key in args:
                    safety.mark_file_read(args[path_key])
                    break

        plugin_manager = get_plugin_manager()
        ctx = {"orchestrator": self, "turn": turn}

        for attempt in range(self.config.reflection_max_retries):
            tool = self.tools.get(name)
            if not tool:
                self._thinking.finish_step(exec_step, error=f"Unknown tool: {name}")
                return ToolResult(success=False, content="", error=f"Unknown tool: {name}")

            args = plugin_manager.call_tool_hooks(name, args, ctx)

            result = await tool.execute(**args)
            result = plugin_manager.call_result_hooks(name, result, ctx)

            # 2. The Refiner's Fire (Integrity Validation)
            if result.success and name in ("write", "edit"):
                validation_passed, validation_error = await self._run_refiners_fire(args.get("path"))
                if not validation_passed:
                    result.success = False
                    result.content = (
                        "[REJECTED BY FIRE] The work was performed, but it "
                        "failed the integrity check:\n"
                        f"{validation_error}\n\n[RECOVERY] I have detected an "
                        "impurity in the logic. You must fix this error "
                        "before proceeding."
                    )

            if result.success or depth > 0:
                self._thinking.finish_step(exec_step, result=result.content[:200] if result.content else "")
                return result

            # 3. Deterministic Recovery Hints
            if not result.success:
                result.content = await self._handle_tool_failure(name, args, result.error or result.content)

            if self.config.verbose:
                print(f"\\n[nexus] Tool '{name}' failed (attempt {attempt + 1}): {result.error}")

            if attempt < self.config.reflection_max_retries - 1 and self.config.reflection_enabled:
                reflection = await self._reflect_on_failure(name, args, result.error or "")
                if reflection.get("adjusted_args"):
                    args = reflection["adjusted_args"]
                    if self.config.verbose:
                        print(f"[nexus] Adjusting args: {args}")

        self._thinking.finish_step(exec_step, error=result.error or "Tool failed")
        return result

    async def _run_refiners_fire(self, path: str | None) -> tuple[bool, str | None]:
        """Perform a mandatory integrity check on modified code."""
        return await run_refiners_fire(path)

    async def _handle_tool_failure(self, tool_name: str, args: dict, error: str) -> str:
        """Provide deterministic recovery hints for tool failures."""
        hint = get_tool_failure_hint(tool_name, args, error)
        user_friendly_error = format_error(error)
        return f"Tool '{tool_name}' failed: {user_friendly_error}\n{hint}"

    async def _reflect_on_failure(self, tool_name: str, args: dict, error: str) -> dict[str, Any]:
        """Ask the AI to suggest fixes for a failed tool call."""
        prompt = (
            f"Tool '{tool_name}' failed with error: {error}\n"
            f"Original args: {json.dumps(args)}\n"
            "Suggest adjusted args (JSON only, or {{}} if no fix possible)."
        )

        try:
            response = await self.pm.complete(
                messages=[Message(role="user", content=prompt)],
                provider_name=self.config.provider_name,
            )
            content = response.content or "{}"
            try:
                adjusted = json.loads(content)
                return {"adjusted_args": adjusted} if isinstance(adjusted, dict) else {}
            except json.JSONDecodeError:
                return {}
        except Exception:
            return {}

    def get_history(self) -> list[Turn]:
        """Return conversation history."""
        return self._history

    def compact_history(self, target_turns: int = 10) -> None:
        """
        Compact conversation history to reduce context size.
        Keeps first and last N turns, summarizes middle turns.
        """
        if len(self._messages) <= target_turns * 2:
            return

        system = [m for m in self._messages if m.role == "system"]
        non_system = [m for m in self._messages if m.role != "system"]
        keep = non_system[:2] + non_system[-target_turns:]

        summary = Message(role="system", content=f"[Previous conversation summarized — {len(non_system)} messages condensed]")
        self._messages = system + [summary] + keep

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def tool_call_count(self) -> int:
        return self._tool_call_count

    def reset(self) -> None:
        """Reset conversation state but keep config."""
        system = [m for m in self._messages if m.role == "system"]
        self._messages = system
        self._history = []
        self._turn_count = 0
        self._tool_call_count = 0
