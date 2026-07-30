"""Integration tests for the memory layer wiring in AgentOrchestrator.

Previously `self.memory` was stored on the orchestrator but never actually
consulted: facts were never recalled, no semantic memory was queried, and
conversations were never auto-persisted to a session. These tests exercise
the real (non-mocked) Memory + VectorMemory classes end-to-end against a
fake provider, so a regression that silently disconnects the wiring again
would be caught.
"""

import pytest

from nexus.agent.orchestrator import AgentConfig, AgentOrchestrator
from nexus.memory import Memory
from nexus.providers.base import Response
from nexus.tools import ToolRegistry


class FakeProviderManager:
    """Minimal duck-typed stand-in for ProviderManager.complete()."""

    def __init__(self, reply: str = "Sure, here's the answer."):
        self.reply = reply
        self.calls: list[list] = []

    async def complete(self, messages, tools=None, provider_name=None, **kwargs):
        self.calls.append(list(messages))
        return Response(content=self.reply, model="fake-model", tool_calls=[])


@pytest.fixture
def memory(tmp_path):
    return Memory(memory_dir=tmp_path / "nexus-memory")


@pytest.fixture
def orchestrator(memory):
    pm = FakeProviderManager()
    tools = ToolRegistry()
    orch = AgentOrchestrator(
        provider_manager=pm,
        tool_registry=tools,
        memory=memory,
        config=AgentConfig(stream=False, reflection_enabled=False),
    )
    orch._pm_ref = pm  # stash for assertions
    return orch


@pytest.mark.asyncio
async def test_orchestrator_creates_a_session_on_init(orchestrator, memory):
    assert orchestrator._session is not None
    assert memory.load_session(orchestrator._session.id) is not None


@pytest.mark.asyncio
async def test_turn_is_auto_persisted_to_session(orchestrator, memory):
    turn = await orchestrator.run("What's the capital of France?")
    assert turn.assistant_message

    saved = memory.load_session(orchestrator._session.id)
    assert saved is not None
    contents = [m["content"] for m in saved.messages]
    assert "What's the capital of France?" in contents
    assert turn.assistant_message in contents


@pytest.mark.asyncio
async def test_turn_is_indexed_in_vector_memory(orchestrator):
    await orchestrator.run("Remember that the deploy key rotates every Monday.")

    vm = orchestrator._get_vector_memory()
    assert vm is not None
    count = await vm.count()
    assert count >= 1

    results = await vm.recall("deploy key Monday")
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_facts_are_recalled_into_the_next_turn(orchestrator, memory):
    memory.add_fact("preferred_language", "Rust")

    await orchestrator.run("What language should I use for this new service?")

    # The fake provider records every message list it was called with;
    # the injected memory-recall note should be present in that request.
    all_sent_messages = orchestrator._pm_ref.calls[-1]
    combined = "\n".join(m.content for m in all_sent_messages)
    assert "preferred_language" in combined
    assert "Rust" in combined


@pytest.mark.asyncio
async def test_memory_recall_can_be_disabled(memory):
    pm = FakeProviderManager()
    tools = ToolRegistry()
    memory.add_fact("preferred_language", "Rust")
    orch = AgentOrchestrator(
        provider_manager=pm,
        tool_registry=tools,
        memory=memory,
        config=AgentConfig(stream=False, reflection_enabled=False, memory_recall_enabled=False),
    )

    await orch.run("What language should I use?")
    combined = "\n".join(m.content for m in pm.calls[-1])
    assert "preferred_language" not in combined


@pytest.mark.asyncio
async def test_memory_auto_persist_can_be_disabled(tmp_path):
    memory = Memory(memory_dir=tmp_path / "nexus-memory-2")
    pm = FakeProviderManager()
    tools = ToolRegistry()
    orch = AgentOrchestrator(
        provider_manager=pm,
        tool_registry=tools,
        memory=memory,
        config=AgentConfig(stream=False, reflection_enabled=False, memory_auto_persist=False),
    )

    await orch.run("This should not be persisted anywhere.")
    saved = memory.load_session(orch._session.id)
    assert saved.messages == []


@pytest.mark.asyncio
async def test_orchestrator_without_memory_still_works():
    """No `memory=` at all must remain a no-op, not an error."""
    pm = FakeProviderManager()
    tools = ToolRegistry()
    orch = AgentOrchestrator(provider_manager=pm, tool_registry=tools, config=AgentConfig(stream=False, reflection_enabled=False))

    turn = await orch.run("hello")
    assert turn.assistant_message
    assert orch._get_vector_memory() is None
