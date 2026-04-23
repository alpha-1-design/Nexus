"""Agent orchestrator - the core loop connecting AI, tools, and memory."""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..providers import ProviderManager, Message, ToolCall
from ..tools import ToolRegistry, ToolResult
from ..memory import Memory
from ..thinking import ThinkingEngine, ThinkingState, get_thinking_engine
from ..agents import MultiAgentTeam, AgentRole, init_team, get_team
from ..plugins import get_plugin_manager


def estimate_tokens(text: str) -> int:
    """Estimate token count using a simple approximation (4 chars per token)."""
    if not text:
        return 0
    return len(re.findall(r'\w+', text)) + (len(text) // 4)


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
        self._turn_count = 0
        self._tool_call_count = 0
        self._messages: list[Message] = []
        self._history: list[Turn] = []
        self._thinking = get_thinking_engine()
        self._thinking_callback = None
        self._total_tokens = 0
        self._tool_stats: dict[str, dict] = {}
        self._team: MultiAgentTeam | None = None

    def init_team(self, lead_name: str = "nexus") -> MultiAgentTeam:
        """Initialize the multi-agent team."""
        if self._team is None:
            self._team = init_team(lead_name=lead_name, pm=self.pm)
        return self._team

    @property
    def team(self) -> MultiAgentTeam | None:
        return self._team

    def spawn_agent(self, role: AgentRole, name: str | None = None, 
                    task: str | None = None, model: str | None = None):
        """Spawn an agent with the given role."""
        if self._team is None:
            self._team = self.init_team()
        return self._team.spawn(role, name=name, task=task, model=model)

    def get_team(self) -> MultiAgentTeam | None:
        """Get the current team."""
        return self._team

    def set_system_prompt(self, prompt: str) -> None:
        """Set or update the system prompt."""
        self._messages = [Message(role="system", content=prompt)]
        if self.config.system_prompt:
            self._messages[0] = Message(
                role="system",
                content=self.config.system_prompt + "\n\n" + prompt
            )

    async def run(self, user_input: str, stream_callback=None, thinking_callback=None) -> Turn:
        """Run a single interaction turn."""
        start = time.monotonic()
        turn = Turn(user_message=user_input)
        self._turn_count += 1

        if thinking_callback:
            self._thinking_callback = thinking_callback
            self._thinking.on_update(thinking_callback)

        self._thinking.clear()
        analyze_step = self._thinking.start_step(
            ThinkingState.ANALYZING,
            "Analyzing task...",
            detail=f"Input: {user_input[:100]}..." if len(user_input) > 100 else f"Input: {user_input}"
        )

        self._messages.append(Message(role="user", content=user_input))

        self._thinking.update_step(analyze_step, confidence=0.85)
        self._thinking.finish_step(analyze_step, result="Analyzed user input")

        planning_step = self._thinking.start_step(
            ThinkingState.PLANNING,
            "Planning tool sequence...",
            detail=f"Available tools: {len(self.tools.list_all())}"
        )
        self._thinking.update_step(planning_step, confidence=0.75)
        self._thinking.finish_step(planning_step, result="Tool sequence planned")

        try:
            result = await self._run_loop(stream_callback)
            turn.assistant_message = result["message"]
            turn.tool_calls = result.get("tool_calls", [])
            if result.get("pending_approval"):
                turn.pending_approval = result["pending_approval"]

            review_step = self._thinking.start_step(
                ThinkingState.REVIEWING,
                "Generating response...",
                detail=f"Response length: {len(result['message'])} chars"
            )
            self._thinking.update_step(review_step, confidence=0.90)
            self._thinking.finish_step(review_step, result=result["message"][:200])

            complete_step = self._thinking.start_step(
                ThinkingState.COMPLETE,
                "Task complete",
                detail=f"Completed in {len(result.get('tool_calls', []))} tool calls"
            )
            self._thinking.finish_step(complete_step, result=result["message"][:200])
            turn.tokens_used = self._total_tokens
        except Exception as e:
            turn.error = str(e)
            turn.assistant_message = f"Error: {e}"

        turn.duration_ms = int((time.monotonic() - start) * 1000)
        self._history.append(turn)
        return turn

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

            if self.config.stream and stream_callback:
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
                        "pending_approval": {
                            "tool_name": tc.name,
                            "args": tc.arguments,
                            "result": tool_result
                        }
                    }

                tool_msg = f"Tool result for {tc.name}: {tool_result.content}"
                if tool_result.error:
                    tool_msg += f"\nError: {tool_result.error}"
                self._messages.append(Message(role="user", content=tool_msg))

            if self.config.verbose:
                print(f"\n[nexus] Turn {self._turn_count}, tool calls: {len(tool_calls)}")

        raise RuntimeError(f"Max turns ({self.config.max_turns}) reached without final response")

    async def _execute_tool(self, tool_call, turn: Turn, depth: int = 0) -> ToolResult:
        """Execute a tool call with reflection on failure."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        exec_step = self._thinking.start_step(
            ThinkingState.EXECUTING,
            f"Calling tool: {name}",
            tool_name=name,
            tool_args=args
        )

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

            if result.success or depth > 0:
                self._thinking.finish_step(exec_step, result=result.content[:200] if result.content else "")
                return result

            if self.config.verbose:
                print(f"\n[nexus] Tool '{name}' failed (attempt {attempt+1}): {result.error}")

            if attempt < self.config.reflection_max_retries - 1 and self.config.reflection_enabled:
                reflection = await self._reflect_on_failure(name, args, result.error or "")
                if reflection.get("adjusted_args"):
                    args = reflection["adjusted_args"]
                    if self.config.verbose:
                        print(f"[nexus] Adjusting args: {args}")

        self._thinking.finish_step(exec_step, error=result.error or "Tool failed")
        return result

    async def _reflect_on_failure(
        self, tool_name: str, args: dict, error: str
    ) -> dict[str, Any]:
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

        summary = Message(
            role="system",
            content=f"[Previous conversation summarized — {len(non_system)} messages condensed]"
        )
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
