"""Tests for the AgentOrchestrator — focusing on tool execution and reflection."""

from nexus.agent import get_orchestrator


def test_orchestrator_singleton():
    """get_orchestrator should return the same instance."""
    orch1 = get_orchestrator()
    orch2 = get_orchestrator()
    assert orch1 is orch2


def test_orchestrator_initialized():
    """Orchestrator should have required subsystems."""
    orch = get_orchestrator()
    assert orch.pm is not None
    assert orch.tools is not None
    assert orch.memory is not None


def test_orchestrator_tool_count():
    """Orchestrator should have tools registered."""
    orch = get_orchestrator()
    tools = orch.tools.list_all()
    assert len(tools) > 0


def test_orchestrator_has_reflection():
    """Orchestrator should have reflection engine."""
    orch = get_orchestrator()
    assert orch.reflection_engine is not None
