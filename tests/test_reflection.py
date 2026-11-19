"""Tests for the run-reflection engine (nexus/agent/reflection.py).

`perform_reflection` was a print-only stub until the 2026-11-19 M6 round:
it is now a real JSON-lines journal appended once per completed
orchestrator run.
"""

import json

import pytest

from nexus.agent.reflection import ReflectionEngine


class _Turn:
    """Duck-typed stand-in for orchestrator.Turn."""

    def __init__(self, user_message="task", error=None, duration_ms=1500, tool_calls=None, tokens_used=320):
        self.user_message = user_message
        self.error = error
        self.duration_ms = duration_ms
        self.tool_calls = tool_calls or []
        self.tokens_used = tokens_used


def test_perform_reflection_appends_json_line(tmp_path):
    engine = ReflectionEngine(tmp_path)
    entry = engine.perform_reflection(_Turn())

    assert entry is not None
    assert entry["outcome"] == "ok"
    assert entry["task"] == "task"
    assert entry["duration_ms"] == 1500
    assert entry["tool_calls"] == 0
    assert entry["tokens_used"] == 320
    assert "timestamp" in entry

    lines = (tmp_path / "reflections.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry


def test_perform_reflection_records_error_outcome(tmp_path):
    engine = ReflectionEngine(tmp_path)
    entry = engine.perform_reflection(_Turn(user_message="fix the bug", error="provider down"))

    assert entry["outcome"] == "error"
    assert entry["error"] == "provider down"
    assert entry["task"] == "fix the bug"


def test_perform_reflection_counts_tool_calls(tmp_path):
    engine = ReflectionEngine(tmp_path)
    entry = engine.perform_reflection(_Turn(tool_calls=[{"name": "bash"}, {"name": "edit"}]))
    assert entry["tool_calls"] == 2


def test_perform_reflection_truncates_long_task(tmp_path):
    engine = ReflectionEngine(tmp_path)
    entry = engine.perform_reflection(_Turn(user_message="x" * 500))
    assert len(entry["task"]) == 120


def test_load_reflections_reads_back_last_entries(tmp_path):
    engine = ReflectionEngine(tmp_path)
    for i in range(5):
        engine.perform_reflection(_Turn(user_message=f"task {i}"))

    entries = engine.load_reflections(limit=3)
    assert [e["task"] for e in entries] == ["task 2", "task 3", "task 4"]
    assert len(engine.load_reflections(limit=100)) == 5


def test_load_reflections_tolerates_corrupt_lines(tmp_path):
    engine = ReflectionEngine(tmp_path)
    engine.perform_reflection(_Turn(user_message="good"))
    with open(engine.reflections_file, "a", encoding="utf-8") as f:
        f.write("{torn line\n")
    engine.perform_reflection(_Turn(user_message="also good"))

    entries = engine.load_reflections()
    assert [e["task"] for e in entries] == ["good", "also good"]


def test_load_reflections_missing_file_returns_empty(tmp_path):
    assert ReflectionEngine(tmp_path).load_reflections() == []


# ---------------------------------------------------------------------------
# Orchestrator wiring: run() appends one entry per completed run
# ---------------------------------------------------------------------------


class FakeProviderManager:
    async def complete(self, messages, tools=None, provider_name=None, **kwargs):
        from nexus.providers.base import Response

        return Response(content="done", model="fake-model", tool_calls=[])


@pytest.mark.asyncio
async def test_run_appends_reflection_entry(tmp_path):
    from nexus.agent.orchestrator import AgentConfig, AgentOrchestrator
    from nexus.memory import Memory
    from nexus.tools import ToolRegistry

    memory = Memory(memory_dir=tmp_path / "nexus-memory")
    orch = AgentOrchestrator(
        provider_manager=FakeProviderManager(),
        tool_registry=ToolRegistry(),
        memory=memory,
        config=AgentConfig(stream=False, reflection_enabled=False),
    )
    turn = await orch.run("Reflect on me")

    # The engine points at the structured sessions dir, next to session JSONs.
    assert orch.reflection_engine.session_dir == memory.sessions_dir
    entries = orch.reflection_engine.load_reflections()
    assert len(entries) == 1
    assert entries[0]["task"] == "Reflect on me"
    assert entries[0]["outcome"] == "ok"
    assert entries[0]["duration_ms"] == turn.duration_ms


@pytest.mark.asyncio
async def test_run_reflection_error_outcome(tmp_path):
    from nexus.agent.orchestrator import AgentConfig, AgentOrchestrator
    from nexus.memory import Memory
    from nexus.tools import ToolRegistry

    class BoomProviderManager:
        async def complete(self, messages, tools=None, provider_name=None, **kwargs):
            raise RuntimeError("provider exploded")

    memory = Memory(memory_dir=tmp_path / "nexus-memory")
    orch = AgentOrchestrator(
        provider_manager=BoomProviderManager(),
        tool_registry=ToolRegistry(),
        memory=memory,
        config=AgentConfig(stream=False, reflection_enabled=False),
    )
    await orch.run("This will fail")

    entries = orch.reflection_engine.load_reflections()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "error"
    assert "provider exploded" in entries[0]["error"]
