"""Tests for skill pre-loading injection (Feature B) in the engine seams.

Covers:
- orchestrator: system-prompt injection only above threshold, turn metadata,
  usage counters, and the /skills lookup short-circuit.
- REPL: the dim `⚡ skills:` line before the stream, the RELEVANT SKILLS
  section in the system prompt of the first LLM call, and /skills <task>.
"""

import pytest

from nexus.agent import get_orchestrator
from nexus.cli.repl import REPL
from nexus.providers import StreamChunk
from nexus.skills.matcher import get_skill_matcher

TOPIC_TASK = "help me design a postgres database migration"
GARBAGE_TASK = "quantum banana strudel wobble xylophone"


# ---------------------------------------------------------------------------
# Orchestrator seam
# ---------------------------------------------------------------------------


@pytest.fixture()
def orchestrator():
    orch = get_orchestrator()
    orch.config.memory_recall_enabled = False
    orch.config.memory_auto_persist = False
    orch.reset()
    # reset() keeps system messages — drop notes left by earlier tests so
    # assertions about injected notes start from a clean transcript.
    orch._messages = [
        m
        for m in orch._messages
        if "### RELEVANT SKILLS" not in (m.content or "")
    ]
    return orch


class _FakeOrchPM:
    """Provider-manager stub: one final answer, records calls."""

    def __init__(self):
        self.complete_calls: list = []

    async def complete(self, messages, tools=None, **kwargs):
        self.complete_calls.append(list(messages))

        class _R:
            content = "Done."
            tool_calls = []

        return _R()

    async def stream(self, messages, tools=None, **kwargs):
        yield StreamChunk(content="Done.", done=True)
        yield StreamChunk(done=True)


class TestOrchestratorInjection:
    def test_match_skills_relevant_and_garbage(self, orchestrator):
        assert orchestrator.match_skills(TOPIC_TASK)
        assert orchestrator.match_skills(GARBAGE_TASK) == []

    def test_skill_note_above_threshold(self, orchestrator):
        note = orchestrator.skill_note_for(TOPIC_TASK)
        assert note is not None
        assert note.startswith("### RELEVANT SKILLS")
        assert "postgres" in note.lower()
        assert "/skill" in note

    def test_no_note_below_threshold(self, orchestrator):
        assert orchestrator.skill_note_for(GARBAGE_TASK) is None

    @pytest.mark.asyncio
    async def test_run_injects_note_and_records_turn_skills(self, orchestrator):
        matcher = get_skill_matcher()
        expected = [m.name for m in matcher.match(TOPIC_TASK)]
        assert expected  # pre-condition: catalog matches this topic

        orchestrator.reset()
        fake_pm = _FakeOrchPM()
        original_pm = orchestrator.pm
        orchestrator.pm = fake_pm
        try:
            turn = await orchestrator.run(TOPIC_TASK)
        finally:
            orchestrator.pm = original_pm

        assert turn.skills_loaded == expected
        assert turn.error is None
        # The note is inserted as a system message right before the turn's
        # user message (same seam as memory recall).
        skills_notes = [
            m
            for m in orchestrator._messages
            if m.role == "system" and "### RELEVANT SKILLS" in (m.content or "")
        ]
        assert len(skills_notes) == 1
        note_idx = orchestrator._messages.index(skills_notes[0])
        assert orchestrator._messages[note_idx + 1].role == "user"
        assert orchestrator._messages[note_idx + 1].content == TOPIC_TASK
        # First (and only) LLM call saw the section in its system prompt.
        assert fake_pm.complete_calls, "expected one LLM call"
        system_msg = fake_pm.complete_calls[0][0]
        assert system_msg.role == "system"
        assert "### RELEVANT SKILLS" in system_msg.content
        # Usage counter recorded for /stats-style surfaces.
        usage = orchestrator.get_skill_usage()
        for name in expected:
            assert usage.get(name, 0) >= 1

    @pytest.mark.asyncio
    async def test_run_skips_injection_below_threshold(self, orchestrator):
        orchestrator.reset()
        fake_pm = _FakeOrchPM()
        original_pm = orchestrator.pm
        orchestrator.pm = fake_pm
        try:
            turn = await orchestrator.run(GARBAGE_TASK)
        finally:
            orchestrator.pm = original_pm

        assert turn.skills_loaded == []
        for m in orchestrator._messages:
            if m.role == "system" and m.content:
                assert "### RELEVANT SKILLS" not in m.content

    @pytest.mark.asyncio
    async def test_run_answers_skills_lookup_without_llm(self, orchestrator):
        fake_pm = _FakeOrchPM()
        original_pm = orchestrator.pm
        orchestrator.pm = fake_pm
        try:
            turn = await orchestrator.run("/skills postgres migration tuning")
        finally:
            orchestrator.pm = original_pm

        assert fake_pm.complete_calls == []  # no LLM call for a lookup
        assert "skills matched" in turn.assistant_message
        assert "postgres-pro" in turn.assistant_message
        assert "/skill" in turn.assistant_message

    @pytest.mark.asyncio
    async def test_skills_lookup_without_args_summarizes_catalog(self, orchestrator):
        turn = await orchestrator.run("/skills")
        assert "bundled skills" in turn.assistant_message
        assert "/skills <task>" in turn.assistant_message


# ---------------------------------------------------------------------------
# REPL seam
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def to_openai_format(self):
        return []

    def get(self, name):
        return None

    def list_all(self):
        return []


class _FakeStreamManager:
    """One streamed no-tool answer; records the messages of each call."""

    def __init__(self):
        self.stream_calls: list[list] = []
        self.active_provider = "fake"

    async def stream(self, messages, tools=None, **kwargs):
        self.stream_calls.append(list(messages))
        yield StreamChunk(content="Done.", done=True)


def _make_repl() -> REPL:
    """REPL wired to fakes: no network, no disk writes, fast visuals."""
    repl = REPL()
    repl.team = None
    repl.registry = _FakeRegistry()
    repl.memory.save_session = lambda session: None
    repl._get_system_prompt = lambda: "BASE PROMPT"  # skip indexer/memory lookups
    repl._check_tool_safety = lambda name, args: (True, None)
    repl._show_context_pulse = lambda: None
    repl.manager = _FakeStreamManager()
    return repl


class TestREPLInjection:
    @pytest.mark.asyncio
    async def test_prints_dim_skills_line_and_injects_section(self, capsys):
        repl = _make_repl()
        result = await repl._generate_response(TOPIC_TASK, print_result=False)

        assert result == "Done."
        out = capsys.readouterr().out
        # The visible hint line comes before the stream...
        assert "⚡ skills:" in out
        matched = repl._match_skills(TOPIC_TASK)
        for m in matched:
            assert m.name in out
        # ...and the first LLM call's system prompt carries the section.
        first_call = repl.manager.stream_calls[0]
        system_msg = first_call[0]
        assert system_msg.role == "system"
        assert system_msg.content.startswith("BASE PROMPT")
        assert "### RELEVANT SKILLS" in system_msg.content
        # Session metadata for later /stats surfaces.
        assert set(m.name for m in matched) <= set(repl._skills_used)

    @pytest.mark.asyncio
    async def test_no_line_and_no_section_below_threshold(self, capsys):
        repl = _make_repl()
        await repl._generate_response(GARBAGE_TASK, print_result=False)

        out = capsys.readouterr().out
        assert "⚡ skills:" not in out
        system_msg = repl.manager.stream_calls[0][0]
        assert system_msg.content == "BASE PROMPT"  # untouched
        assert repl._skills_used == []

    @pytest.mark.asyncio
    async def test_skills_command_matches_task(self, capsys):
        repl = _make_repl()
        handled = await repl._handle_command("/skills postgres migration tuning")
        out = capsys.readouterr().out
        assert handled
        assert "skills matched" in out
        assert "postgres-pro" in out
        assert "/skill <name>" in out

    @pytest.mark.asyncio
    async def test_skills_command_without_args_still_lists_all(self, capsys):
        repl = _make_repl()
        handled = await repl._handle_command("/skills")
        out = capsys.readouterr().out
        assert handled
        assert "Available skills:" in out

    @pytest.mark.asyncio
    async def test_skills_command_reports_no_match(self, capsys):
        repl = _make_repl()
        handled = await repl._handle_command(f"/skills {GARBAGE_TASK}")
        out = capsys.readouterr().out
        assert handled
        assert "No bundled skills matched" in out

    @pytest.mark.asyncio
    async def test_help_mentions_skills_matcher(self, capsys):
        repl = _make_repl()
        handled = await repl._handle_command("/help")
        out = capsys.readouterr().out
        assert handled
        assert "/skills [task]" in out

    @pytest.mark.asyncio
    async def test_stats_shows_skills_used(self, capsys):
        repl = _make_repl()
        await repl._generate_response(TOPIC_TASK, print_result=False)
        await repl._handle_command("/stats")
        out = capsys.readouterr().out
        assert "Skills used:" in out

    def test_provider_badge_absent_without_configs(self):
        """No configured providers -> no badge line (neutral art)."""
        repl = _make_repl()
        # _FakeStreamManager has no `configs` attribute at all.
        assert repl._get_provider_badge_line() is None

    def test_provider_badge_renders_brand_color(self):
        repl = _make_repl()

        class _Cfg:
            model = "llama-3.3-70b"

        class _Mgr(_FakeStreamManager):
            configs = {"groq": object()}

            def __init__(self):
                super().__init__()
                # Instance attr: the inherited __init__ would otherwise
                # shadow the class-level value.
                self.active_provider = "groq"

            def get_provider_config(self, name):
                return _Cfg()

        repl.manager = _Mgr()
        line = repl._get_provider_badge_line()
        assert line is not None
        assert "⬢ groq" in line
        assert "llama-3.3-70b" in line
        assert "\033[38;2;245;80;54m" in line  # groq brand #f55036 as ANSI
