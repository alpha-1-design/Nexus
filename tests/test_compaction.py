"""Tests for context compaction (REPL + orchestrator) and preference auto-capture."""

import asyncio
import json
from datetime import datetime

from nexus.agent.orchestrator import (
    COMPACT_KEEP_RECENT,
    AgentConfig,
    AgentOrchestrator,
    estimate_context_tokens,
    kept_suffix,
    summarize_messages_deterministic,
)
from nexus.cli.repl import REPL
from nexus.memory import Memory, Session
from nexus.providers.base import Message, Response
from nexus.thinking import get_thinking_engine


class FakeManager:
    """Provider-manager stand-in: returns a canned summary, or raises."""

    def __init__(self, summary: str = "User asked for a refactor; files edited.", fail: bool = False):
        self.summary = summary
        self.fail = fail
        self.calls = []

    async def complete(self, messages, tools=None, provider_name=None):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("provider down")
        return Response(content=self.summary, model="fake")


def make_repl(tmp_path, manager=None, n_messages=0):
    """Bare REPL instance with only the compaction seams wired."""
    repl = REPL.__new__(REPL)
    repl.config = {}
    contents = [f"turn {i} " + "x" * 40 for i in range(n_messages)]
    repl.messages = [Message(role="user" if i % 2 == 0 else "assistant", content=c) for i, c in enumerate(contents)]
    repl.session = Session(
        id="testses",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        messages=[{"role": "user" if i % 2 == 0 else "assistant", "content": c} for i, c in enumerate(contents)],
    )
    repl.memory = Memory(memory_dir=tmp_path / "memory")
    repl.manager = manager or FakeManager()
    return repl


def test_kept_suffix_anchored_at_user_turn():
    msgs = [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1", tool_calls=[{"id": "1"}]),
        Message(role="tool", content="r1"),
        Message(role="user", content="u2"),
    ]
    kept = kept_suffix(msgs, 2)
    assert kept[0].role == "user"


def test_kept_suffix_trims_dangling_tools():
    msgs = [Message(role="assistant", content="a", tool_calls=[{"id": "1"}])] + [
        Message(role="tool", content="r") for _ in range(10)
    ]
    kept = kept_suffix(msgs, 2)
    assert all(m.role != "tool" for m in kept[:1]) or kept[0].role == "assistant"
    assert len(kept) <= 10


def test_estimate_tokens_chars4():
    assert estimate_context_tokens([Message(role="user", content="a" * 400)]) == 100


def test_deterministic_summarizer_lists_turns_and_tools():
    msgs = [
        Message(role="user", content="fix the bug\nsecond line"),
        Message(role="assistant", content="running tool", tool_calls=[{"id": "1"}]),
        Message(role="tool", content="patch applied", name="edit"),
    ]
    out = summarize_messages_deterministic(msgs)
    assert "Deterministic summary" in out
    assert "fix the bug" in out
    assert "tool edit: patch applied" in out


def test_repl_compaction_llm_summary(tmp_path):
    mgr = FakeManager(summary="User asked for a refactor; key files edited.")
    repl = make_repl(tmp_path, manager=mgr, n_messages=30)
    info = asyncio.run(repl.compact_history())
    assert info["compacted"] >= 20
    assert len(repl.messages) <= COMPACT_KEEP_RECENT + 3
    assert repl.messages[0].role == "system"
    assert repl.messages[0].content.startswith("[context compacted]")
    assert "refactor" in repl.messages[0].content
    # Recent turns kept verbatim.
    assert repl.messages[-1].content.startswith("turn 29")
    # Strict summarization prompt reached the provider.
    assert mgr.calls and "conversation compactor" in mgr.calls[0][0].content


def test_repl_compaction_deterministic_fallback(tmp_path):
    repl = make_repl(tmp_path, manager=FakeManager(fail=True), n_messages=30)
    info = asyncio.run(repl.compact_history())
    assert repl.messages[0].content.startswith("[context compacted] Deterministic summary")
    assert "turn 0" in repl.messages[0].content


def test_session_stays_valid_and_reloadable(tmp_path):
    repl = make_repl(tmp_path, n_messages=30)
    asyncio.run(repl.compact_history())
    data = json.loads((repl.memory.sessions_dir / "testses.json").read_text())
    assert data["messages"][0]["role"] == "system"
    assert data["messages"][0]["content"].startswith("[context compacted]")
    loaded = repl.memory.load_session("testses")
    assert loaded is not None
    roles = [m["role"] for m in loaded.messages]
    assert roles[0] == "system"
    assert "tool" not in roles[:2]


def test_should_compact_thresholds(tmp_path):
    repl = make_repl(tmp_path, n_messages=10)
    assert repl._should_compact() is False
    # 70% of a 128k window in chars -> ~0.7 ratio via chars/4.
    repl.messages = [Message(role="user", content="a" * int(128_000 * 4 * 0.7))]
    assert repl._should_compact() is True
    # Configured window override.
    repl.config = {"max_context_tokens": 1000}
    repl.messages = [Message(role="user", content="a" * 4000)]
    assert repl._context_usage()["ratio"] >= 0.6
    assert repl._should_compact() is True


def test_orchestrator_compaction_rewrites_session(tmp_path):
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.config = AgentConfig()
    orch.pm = FakeManager(fail=True)
    orch.memory = Memory(memory_dir=tmp_path / "memory")
    orch._session = orch.memory.create_session()
    orch._session.messages = [{"role": "user", "content": f"turn {i}"} for i in range(30)]
    orch._messages = [Message(role="user", content=f"turn {i} " + "y" * 40) for i in range(30)]
    orch._thinking = get_thinking_engine()
    orch._ui_callback = None

    info = asyncio.run(orch.compact_context(force=True))
    assert info is not None and info["compacted"] == 30 - COMPACT_KEEP_RECENT
    assert orch._messages[0].role == "system"
    assert "[context compacted] Deterministic summary" in orch._messages[0].content
    # Persisted session mirrored and reloadable.
    loaded = orch.memory.load_session(orch._session.id)
    assert loaded.messages[0]["role"] == "system"
    assert loaded.messages[0]["content"].startswith("[context compacted]")


def test_orchestrator_no_compaction_below_threshold(tmp_path):
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.config = AgentConfig()
    orch.pm = FakeManager(fail=True)
    orch.memory = None
    orch._session = None
    orch._messages = [Message(role="user", content="hello") for _ in range(5)]
    orch._thinking = get_thinking_engine()
    orch._ui_callback = None
    assert orch.should_compact() is False
    assert asyncio.run(orch.compact_context()) is None


def test_compact_command_handler(tmp_path, capsys):
    mgr = FakeManager(summary="focused summary")
    repl = make_repl(tmp_path, manager=mgr, n_messages=30)
    assert asyncio.run(repl._handle_command("/compact focus on the refactor")) is True
    out = capsys.readouterr().out
    assert "context compacted" in out
    assert any("focus on the refactor" in m.content for call in mgr.calls for m in call)


def test_compact_command_below_threshold(tmp_path, capsys):
    repl = make_repl(tmp_path, n_messages=4)
    assert asyncio.run(repl._handle_command("/compact")) is True
    assert "Nothing to compact" in capsys.readouterr().out


def test_context_command_handler(tmp_path, capsys):
    repl = make_repl(tmp_path, n_messages=5)
    assert asyncio.run(repl._handle_command("/context")) is True
    out = capsys.readouterr().out
    assert "Context usage" in out
    assert "tokens" in out
    assert "▰" in out or "▱" in out


def test_preference_capture(tmp_path):
    repl = make_repl(tmp_path)
    repl._maybe_capture_preference("Always use tabs for indentation in Python files")
    facts = repl.memory.get_facts_by_category("preference")
    assert len(facts) == 1
    assert facts[0].source == "auto"
    assert facts[0].confidence == 0.8
    assert "use tabs" in facts[0].value


def test_preference_capture_dedup(tmp_path):
    repl = make_repl(tmp_path)
    repl._maybe_capture_preference("Never deploy on Fridays without approval")
    repl._maybe_capture_preference("Never deploy on Fridays without approval!")
    facts = repl.memory.get_facts_by_category("preference")
    assert len(facts) == 1


def test_preference_capture_variants(tmp_path):
    repl = make_repl(tmp_path)
    repl._maybe_capture_preference("I prefer concise answers with code examples")
    repl._maybe_capture_preference("From now on, summarize diffs before applying them")
    repl._maybe_capture_preference("Don't add comments to my code")
    cats = repl.memory.get_facts_by_category("preference")
    assert len(cats) == 3


def test_preference_not_captured_for_short_or_commands(tmp_path):
    repl = make_repl(tmp_path)
    repl._maybe_capture_preference("/compact")
    repl._maybe_capture_preference("hi")
    assert repl.memory.get_facts_by_category("preference") == []
