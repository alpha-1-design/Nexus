"""Tests for the REPL module — focusing on the ReAct loop and tool execution."""

import pytest

from nexus.cli.repl import REPL
from nexus.providers import Message, Response, StreamChunk, ToolCall
from nexus.safety import SafetyMode, get_safety_engine
from nexus.tools import ToolResult


def test_safety_engine_modes():
    """Safety engine should support all required modes."""
    safety = get_safety_engine()
    modes = [m for m in SafetyMode]
    assert SafetyMode.USER_REVIEW in modes
    assert SafetyMode.READ_ONLY in modes
    assert SafetyMode.STRICT in modes


def test_safety_read_before_edit():
    """Safety should detect edits without prior reads."""
    safety = get_safety_engine()
    context = {"tool": "edit", "path": "unknown.py"}
    violations = safety.check(context)
    read_edit_violations = [v for v in violations if "read" in v.rule.id.lower()]
    assert len(read_edit_violations) > 0


def test_safety_blocks_destructive_commands():
    """Safety should flag edits without prior reads."""
    safety = get_safety_engine()
    context = {"tool": "edit", "path": "/etc/passwd"}
    violations = safety.check(context)
    read_edit = [v for v in violations if v.rule.id == "read-before-edit"]
    assert len(read_edit) > 0


def test_safety_read_tool_bypasses_edit_check():
    """Read tool should not trigger read-before-edit violations."""
    safety = get_safety_engine()
    context = {"tool": "Read", "path": "readme.md"}
    violations = safety.check(context)
    read_edit = [v for v in violations if v.rule.id == "read-before-edit"]
    assert len(read_edit) == 0


# ---------------------------------------------------------------------------
# Fakes for driving the ReAct loop without a real provider
# ---------------------------------------------------------------------------


class _FakeTool:
    """Tool stub whose execute() echoes its arguments."""

    name = "echo_tool"
    description = "Echoes arguments for tests"

    def __init__(self):
        self.calls: list[dict] = []

    async def execute(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(success=True, content=f"ok:{kwargs.get('path', '')}")


class _FakeRegistry:
    """Registry stub exposing the echo_tool."""

    def __init__(self):
        self.tool = _FakeTool()

    def to_openai_format(self):
        return [{"type": "function", "function": {"name": "echo_tool", "parameters": {}}}]

    def get(self, name):
        return self.tool if name == "echo_tool" else None

    def list_all(self):
        return [self.tool]


class _FakeManager:
    """Scripted provider manager: each turn is a list of StreamChunks (stream
    turn) or a Response (complete turn), consumed in order."""

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.active_provider = "fake"
        self.stream_calls: list[list[Message]] = []
        self.complete_calls: list[tuple[list[Message], list | None]] = []
        self.switched: list[str] = []

    async def stream(self, messages, tools=None, **kwargs):
        self.stream_calls.append(list(messages))
        for chunk in self.turns.pop(0):
            yield chunk

    async def complete(self, messages, tools=None, **kwargs):
        self.complete_calls.append((list(messages), tools))
        return self.turns.pop(0)

    async def switch_model(self, model, provider_name=None):
        self.switched.append(model)

    def get_provider_config(self, name):
        return None


def _make_repl() -> REPL:
    """Build a REPL wired to fakes: no network, no disk writes, fast visuals."""
    repl = REPL()
    repl.team = None  # skip auto-spawn heuristics
    repl.registry = _FakeRegistry()
    repl.memory.save_session = lambda session: None  # keep tests off disk
    repl._get_system_prompt = lambda: "SYSTEM"  # skip indexer/memory lookups
    repl._check_tool_safety = lambda name, args: (True, None)  # skip visual scan
    repl._show_context_pulse = lambda: None
    return repl


def _user_input() -> str:
    # Long enough to skip the proactive-insight branch.
    return "please echo the demo file for me"


# ---------------------------------------------------------------------------
# ReAct loop: transcript validity, persistence, iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_transcript_orders_assistant_before_tool_results():
    """The role="tool" message must be preceded by the assistant message that
    carries the matching tool_calls, in both the wire transcript and history."""
    repl = _make_repl()
    repl.streaming = True
    call = ToolCall(id="call_1", name="echo_tool", arguments={"path": "x.py"})
    repl.manager = _FakeManager(
        [
            [StreamChunk(content="Checking."), StreamChunk(tool_call=call), StreamChunk(done=True)],
            [StreamChunk(content="All done.")],
        ]
    )

    result = await repl._generate_response(_user_input(), print_result=False)

    assert result == "All done."
    # Second LLM call sees [system, user, assistant(tool_calls), tool]
    second = repl.manager.stream_calls[1]
    assert second[0].role == "system"
    assert second[-2].role == "assistant"
    assert [tc.id for tc in second[-2].tool_calls] == ["call_1"]
    assert second[-1].role == "tool"
    assert second[-1].tool_call_id == "call_1"
    assert second[-1].content == "ok:x.py"
    # Same ordering is persisted in in-memory history:
    # user -> assistant(tool_calls) -> tool -> assistant(final answer).
    assert repl.messages[-3].role == "assistant" and repl.messages[-3].tool_calls
    assert repl.messages[-2].role == "tool" and repl.messages[-2].tool_call_id == "call_1"
    assert repl.messages[-1].role == "assistant" and repl.messages[-1].content == "All done."


@pytest.mark.asyncio
async def test_assistant_reply_persisted_to_history_and_session():
    """Every turn — user, assistant tool round, tool results, final answer —
    lands in in-memory history and the session store."""
    repl = _make_repl()
    repl.streaming = True
    call = ToolCall(id="call_1", name="echo_tool", arguments={"path": "x.py"})
    repl.manager = _FakeManager(
        [
            [StreamChunk(content="Checking."), StreamChunk(tool_call=call), StreamChunk(done=True)],
            [StreamChunk(content="All done.")],
        ]
    )

    await repl._generate_response(_user_input(), print_result=False)

    # In-memory history: user -> assistant(tool_calls) -> tool -> assistant.
    assert repl.messages[0].role == "user" and repl.messages[0].content == _user_input()
    assert repl.messages[-1].role == "assistant"
    assert repl.messages[-1].content == "All done."

    # Session store mirrors the same sequence.
    roles = [m["role"] for m in repl.session.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert repl.session.messages[-1]["content"] == "All done."
    assert repl.session.messages[1]["tool_calls"][0]["function"]["name"] == "echo_tool"
    assert repl.session.messages[2]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_loop_iterates_multiple_tool_rounds():
    """The loop must keep calling the LLM after tool rounds until it answers."""
    repl = _make_repl()
    repl.streaming = False
    repl.manager = _FakeManager(
        [
            Response(content="", model="fake", tool_calls=[ToolCall(id="c1", name="echo_tool", arguments={"path": "a.py"})]),
            Response(content="", model="fake", tool_calls=[ToolCall(id="c2", name="echo_tool", arguments={"path": "b.py"})]),
            Response(content="Finished the task.", model="fake"),
        ]
    )

    result = await repl._generate_response("run the multi step task now please", print_result=False)

    assert result == "Finished the task."
    assert len(repl.manager.complete_calls) == 3  # 2 tool rounds + final answer

    # Round 2 transcript: assistant(c1) immediately before tool(c1).
    round_two = repl.manager.complete_calls[1][0]
    assert round_two[-2].role == "assistant" and round_two[-2].tool_calls[0].id == "c1"
    assert round_two[-1].role == "tool" and round_two[-1].tool_call_id == "c1"

    # Round 3 transcript: both rounds present in protocol order.
    round_three = repl.manager.complete_calls[2][0]
    assert [m.role for m in round_three] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert round_three[-2].tool_calls[0].id == "c2"

    # Both tool calls actually executed with their parsed arguments.
    assert repl.registry.tool.calls == [{"path": "a.py"}, {"path": "b.py"}]


@pytest.mark.asyncio
async def test_max_rounds_forces_tool_free_final_answer():
    """After the round budget is spent one final tool-free call is made."""
    repl = _make_repl()
    repl.streaming = False
    repl.max_tool_rounds = 2
    looping_call = ToolCall(id="c1", name="echo_tool", arguments={"path": "a.py"})
    repl.manager = _FakeManager(
        [
            Response(content="", model="fake", tool_calls=[looping_call]),
            Response(content="", model="fake", tool_calls=[looping_call]),
            Response(content="Final synthesized answer.", model="fake"),
        ]
    )

    result = await repl._generate_response("loop on tools forever please", print_result=False)

    assert result == "Final synthesized answer."
    assert len(repl.manager.complete_calls) == 3
    final_messages, final_tools = repl.manager.complete_calls[2]
    assert final_tools is None  # the forced call cannot request more tools
    assert final_messages[-1].role == "user"
    assert "Tool round limit" in final_messages[-1].content


@pytest.mark.asyncio
async def test_abort_flag_stops_react_loop_but_keeps_repl_alive():
    """/abort must end the active loop without killing the REPL."""
    repl = _make_repl()
    repl.streaming = False
    looping_call = ToolCall(id="c1", name="echo_tool", arguments={"path": "a.py"})
    repl.manager = _FakeManager(
        [
            Response(content="", model="fake", tool_calls=[looping_call]),
            Response(content="should never be reached", model="fake"),
        ]
    )

    # Simulate /abort arriving while the first round is executing.
    original_complete = repl.manager.complete

    async def aborting_complete(messages, tools=None, **kwargs):
        repl._abort_requested = True
        return await original_complete(messages, tools, **kwargs)

    repl.manager.complete = aborting_complete

    result = await repl._generate_response("start a long task please", print_result=False)

    assert result == ""  # aborted before any answer
    assert len(repl.manager.complete_calls) == 1  # no further LLM rounds
    assert repl.running is True  # REPL did not exit


# ---------------------------------------------------------------------------
# Slash command fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_command_does_not_exit_repl():
    repl = _make_repl()
    handled = await repl._handle_command("/abort")
    assert handled
    assert repl.running is True  # previously /abort set running=False
    assert repl._abort_requested is False  # nothing active -> no stale flag

    repl._task_running = True
    await repl._handle_command("/abort")
    assert repl._abort_requested is True
    assert repl.running is True


@pytest.mark.asyncio
async def test_model_command_awaits_switch_before_printing(capsys):
    """/model must complete the switch before reporting success."""
    repl = _make_repl()
    repl.manager = _FakeManager([])

    handled = await repl._handle_command("/model grok-4")

    assert handled
    assert repl.manager.switched == ["grok-4"]
    assert "Model switched to: grok-4" in capsys.readouterr().out
