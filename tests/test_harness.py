"""Sanity Fire - Core integrity tests for the Nexus Harness."""

import json

import httpx
import pytest

from nexus.cli.repl import REPL
from nexus.config import ProviderConfig
from nexus.plan import PlanMode, StepPriority
from nexus.providers import Message, Response, StreamChunk, ToolCall
from nexus.providers.base import OpenAIProvider
from nexus.providers.manager import ProviderManager
from nexus.safety import SafetyMode, get_safety_engine
from nexus.tools import ToolResult


def test_safety_engine_initialization():
    """Verify that the Safety Engine boots in USER_REVIEW mode."""
    safety = get_safety_engine()
    assert safety.get_mode() == SafetyMode.USER_REVIEW


def test_safety_mode_switching():
    """Verify that switching safety modes updates the Harness state."""
    safety = get_safety_engine()
    safety.set_mode(SafetyMode.READ_ONLY)
    assert safety.get_mode() == SafetyMode.READ_ONLY

    # Switch back
    safety.set_mode(SafetyMode.USER_REVIEW)
    assert safety.get_mode() == SafetyMode.USER_REVIEW


def test_read_before_edit_rule():
    """Verify that the Harness enforces the 'Read Before Edit' rule."""
    safety = get_safety_engine()
    # Mock a context where a file is being edited without being read
    context = {"tool": "edit", "path": "non_existent_file.py"}
    violations = safety.check(context)

    # It should have at least one violation for reading before editing
    assert any(v.rule.id == "read-before-edit" for v in violations)


@pytest.mark.asyncio
async def test_refiners_fire_syntax_check(tmp_path):
    """Verify that the Refiner's Fire catches syntax impurities."""
    repl = REPL()

    # Create a file with invalid syntax
    bad_file = tmp_path / "impure.py"
    bad_file.write_text("def unclosed_function(:", encoding="utf-8")

    passed, error = await repl._run_refiners_fire(str(bad_file))
    assert passed is False
    assert "Syntax Error" in error


@pytest.mark.asyncio
async def test_refiners_fire_solid_work(tmp_path):
    """Verify that solid work stands the fire."""
    repl = REPL()

    # Create a file with valid syntax
    good_file = tmp_path / "solid.py"
    good_file.write_text("def solid_work():\n    return True", encoding="utf-8")

    passed, error = await repl._run_refiners_fire(str(good_file))
    assert passed is True
    assert error is None


# ---------------------------------------------------------------------------
# Streaming tool-call accumulation (providers/base.py)
# ---------------------------------------------------------------------------


def _sse(payload: dict) -> str:
    """Format one SSE data line like an OpenAI-compatible server sends."""
    return "data: " + json.dumps(payload)


class _FakeStreamResponse:
    """Duck-typed httpx.Response replaying pre-recorded SSE lines."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeHttpClient:
    """Duck-typed httpx.AsyncClient returning the scripted response."""

    def __init__(self, lines: list[str]):
        self._response = _FakeStreamResponse(lines)

    async def post(self, url, json=None):
        return self._response


def _openai_stream_lines() -> list[str]:
    """OpenAI-style stream: arguments arrive as incremental string deltas."""
    return [
        _sse({"choices": [{"delta": {"role": "assistant", "content": "Let me "}}]}),
        _sse({"choices": [{"delta": {"content": "look."}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"pa"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "th\": \"/tmp/x.py\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


@pytest.mark.asyncio
async def test_stream_tool_call_arguments_accumulate_across_chunks():
    """Fragments streamed across chunks must reassemble into parseable args."""
    provider = OpenAIProvider({"api_key": "k", "base_url": "http://localhost:1", "model": "m"})
    provider._client = _FakeHttpClient(_openai_stream_lines())

    chunks = [
        chunk
        async for chunk in provider.stream(
            [Message(role="user", content="hi")],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        )
    ]

    # Content deltas pass through untouched.
    assert [c.content for c in chunks if c.content] == ["Let me ", "look."]

    # Exactly one complete tool call, emitted after the stream finished.
    tool_chunks = [c for c in chunks if c.tool_call]
    assert len(tool_chunks) == 1
    tool_call = tool_chunks[0].tool_call
    assert tool_call.id == "call_1"
    assert tool_call.name == "read_file"
    assert tool_call.arguments == {"path": "/tmp/x.py"}

    # No partial tool_call fragments leak before the finish (consumers would
    # double-count them); the done chunk closes the stream.
    assert all(c.tool_call is None for c in chunks[:-2])
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_stream_content_in_finish_chunk_is_not_dropped():
    """Some providers send the last token together with finish_reason."""
    lines = [
        _sse({"choices": [{"delta": {"content": "Hello"}}]}),
        _sse({"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    provider = OpenAIProvider({"api_key": "k", "base_url": "http://localhost:1", "model": "m"})
    provider._client = _FakeHttpClient(lines)

    chunks = [c async for c in provider.stream([Message(role="user", content="hi")])]

    assert [c.content for c in chunks if c.content] == ["Hello", " world"]
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_stream_buffer_parses_parallel_tool_calls():
    """Interleaved fragments for two tool calls land in index order."""
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "read_file", "arguments": "{\"path\""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "call_b", "function": {"name": "bash", "arguments": "{\"command\""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": \"a.py\"}"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": ": \"ls\"}"}}]}}]}),
        "data: [DONE]",
    ]
    provider = OpenAIProvider({"api_key": "k", "base_url": "http://localhost:1", "model": "m"})
    provider._client = _FakeHttpClient(lines)

    chunks = [c async for c in provider.stream([Message(role="user", content="hi")])]

    calls = [c.tool_call for c in chunks if c.tool_call]
    assert [c.id for c in calls] == ["call_a", "call_b"]
    assert calls[0].arguments == {"path": "a.py"}
    assert calls[1].arguments == {"command": "ls"}


def test_format_message_serializes_tool_calls():
    """Assistant tool_calls must reach the wire in OpenAI format."""
    provider = OpenAIProvider({"api_key": "k", "base_url": "http://localhost:1", "model": "m"})
    msg = Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "x.py"})],
    )
    formatted = provider._format_message(msg)
    assert formatted["tool_calls"] == [
        {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "x.py"})}}
    ]
    # Plain messages are unaffected.
    assert "tool_calls" not in provider._format_message(Message(role="user", content="hi"))


# ---------------------------------------------------------------------------
# ProviderManager.stream fallback semantics (providers/manager.py)
# ---------------------------------------------------------------------------


class _ScriptedProvider(OpenAIProvider):
    """Provider that replays scripted outcomes instead of hitting the network."""

    def __init__(self, outcome: list):
        super().__init__({"api_key": "", "base_url": "http://localhost:1", "model": "m"})
        self._outcome = outcome
        self.seen_tools = None

    async def stream(self, messages, tools=None, **kwargs):
        self.seen_tools = tools
        for item in self._outcome:
            if isinstance(item, Exception):
                raise item
            yield item


def _manager_with_scripts(**providers_by_name: list) -> ProviderManager:
    mgr = ProviderManager()
    for name, outcome in providers_by_name.items():
        mgr.configs[name] = ProviderConfig(name=name, provider_type="openai", base_url="http://localhost:1", model="m")
        mgr.providers[name] = _ScriptedProvider(outcome)
    mgr.active_provider = next(iter(providers_by_name))
    return mgr


@pytest.mark.asyncio
async def test_manager_stream_supports_tools():
    """manager.stream must pass tools through instead of raising RuntimeError."""
    mgr = _manager_with_scripts(
        primary=[StreamChunk(tool_call=ToolCall(id="c1", name="read_file", arguments={"path": "x"})), StreamChunk(done=True)],
    )
    messages = [Message(role="user", content="hi")]

    chunks = [c async for c in mgr.stream(messages, tools=[{"type": "function"}])]

    assert mgr.providers["primary"].seen_tools == [{"type": "function"}]
    assert chunks[0].tool_call.arguments == {"path": "x"}
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_manager_stream_falls_back_before_first_chunk():
    """A provider failing before any chunk is emitted falls to the next one."""
    mgr = _manager_with_scripts(
        primary=[httpx.ConnectError("connection refused")],
        backup=[StreamChunk(content="hello"), StreamChunk(done=True)],
    )
    messages = [Message(role="user", content="hi")]

    chunks = [c async for c in mgr.stream(messages)]

    assert [c.content for c in chunks if c.content] == ["hello"]
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_manager_stream_does_not_restart_after_emitting():
    """After output reached the consumer a mid-stream failure must raise, not
    restart on the next provider (which would duplicate the output)."""
    mgr = _manager_with_scripts(
        primary=[StreamChunk(content="partial"), httpx.ConnectError("dropped")],
        backup=[StreamChunk(content="DUPLICATED")],
    )
    messages = [Message(role="user", content="hi")]

    collected = []
    with pytest.raises(httpx.ConnectError):
        async for chunk in mgr.stream(messages):
            collected.append(chunk)

    assert [c.content for c in collected] == ["partial"]


@pytest.mark.asyncio
async def test_manager_stream_all_fail_raises_runtime_error():
    mgr = _manager_with_scripts(
        primary=[httpx.ConnectError("down")],
        backup=[httpx.ConnectError("also down")],
    )
    with pytest.raises(RuntimeError, match="All providers failed to stream"):
        [c async for c in mgr.stream([Message(role="user", content="hi")])]


# ---------------------------------------------------------------------------
# Plan Mode priority normalization (nexus/plan.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_priority_normalizes_llm_output():
    """LLMs emit lowercase or invalid priorities; they must not crash plan mode."""
    plan_json = json.dumps(
        {
            "task_summary": "demo task",
            "confidence": 0.9,
            "estimated_minutes": 5,
            "steps": [
                {"description": "step a", "priority": "high"},
                {"description": "step b", "priority": "medium"},
                {"description": "step c", "priority": "bogus"},
            ],
        }
    )

    class _PlanProviderManager:
        async def complete(self, messages=None, **kwargs):
            return Response(content=plan_json, model="fake")

    plan = await PlanMode(task="demo task", orchestrator=None).generate_plan(_PlanProviderManager(), [])

    assert plan.steps[0].priority == StepPriority.HIGH
    assert plan.steps[1].priority == StepPriority.MED
    assert plan.steps[2].priority == StepPriority.MED
