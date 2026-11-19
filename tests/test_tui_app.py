"""Tests for the NexusTUI app wiring — singleton orchestrator, tool
recording, panel layout at both desktop and Termux sizes.

These boot the real app in Textual's headless pilot. The ~/.nexus runtime
state is shared with other agent processes and occasionally ends up with a
0-byte facts.json that crashes Memory on boot; heal it before importing.
"""

import asyncio
import json
import os

import pytest


def _heal_runtime_state() -> None:
    path = os.path.expanduser("~/.nexus/memory/facts.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass


_heal_runtime_state()

from nexus.tui.app import NexusTUI, ToolCallRecorder, RecordedTool  # noqa: E402
from nexus.tui.state import AgentStatus, get_state_manager  # noqa: E402
from nexus.tui.widgets import WelcomeCard  # noqa: E402
from nexus.agent import get_orchestrator  # noqa: E402
from nexus.tui.task_wrapper import OrchestrationTask  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_tui_state():
    get_state_manager().reset()
    yield
    get_state_manager().reset()


class _FakeRegistry:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, name):
        self.calls.append(("get", name))
        if name == "edit":
            return _FakeTool()
        return None

    def list_all(self):
        return ["edit"]

    def to_openai_format(self):
        return [{"type": "function", "function": {"name": "edit"}}]


class _FakeTool:
    @property
    def definition(self):
        class D:
            name = "edit"

        return D()

    async def execute(self, **kwargs):
        from nexus.tools.base import ToolResult

        return ToolResult(success=True, content="ok", metadata={"diff": "+x"})


class TestToolCallRecorder:
    """The registry proxy that feeds the Tools panel and transcript cards."""

    def _recorder(self):
        events = {"start": [], "finish": []}
        recorder = ToolCallRecorder(
            _FakeRegistry(),
            on_start=lambda name, args, card_id: events["start"].append((name, args, card_id)),
            on_finish=lambda *a: events["finish"].append(a),
        )
        return recorder, events

    def test_get_wraps_tool_and_delegates(self):
        recorder, _ = self._recorder()
        tool = recorder.get("edit")
        assert isinstance(tool, RecordedTool)
        assert tool.definition.name == "edit"
        assert recorder.get("missing") is None
        # passthrough attributes
        assert recorder.list_all() == ["edit"]
        assert recorder.to_openai_format()[0]["function"]["name"] == "edit"

    @pytest.mark.asyncio
    async def test_execute_fires_start_and_finish(self):
        recorder, events = self._recorder()
        tool = recorder.get("edit")
        result = await tool.execute(path="a.py")
        assert result.success
        assert len(events["start"]) == 1
        name, args, card_id = events["start"][0]
        assert name == "edit" and args == {"path": "a.py"} and card_id
        assert len(events["finish"]) == 1
        finish = events["finish"][0]
        assert finish[1] == "edit" and finish[2] is True and finish[3] == "ok"
        assert finish[5] == "+x"  # diff extracted from metadata
        assert finish[6] >= 0  # duration_ms

    @pytest.mark.asyncio
    async def test_execute_error_path_notifies_and_reraises(self):
        from nexus.tools.base import ToolResult

        events = {"start": [], "finish": []}

        class BoomTool(_FakeTool):
            async def execute(self, **kwargs):
                raise RuntimeError("boom")

        class Reg(_FakeRegistry):
            def get(self, name):
                return BoomTool() if name == "edit" else None

        recorder = ToolCallRecorder(
            Reg(),
            on_start=lambda *a: events["start"].append(a),
            on_finish=lambda *a: events["finish"].append(a),
        )
        tool = recorder.get("edit")
        with pytest.raises(RuntimeError):
            await tool.execute()
        assert len(events["start"]) == 1
        assert len(events["finish"]) == 1
        assert events["finish"][0][2] is False
        assert "boom" in events["finish"][0][4]


class TestNexusTUISingletonWiring:
    """Recon M2-1: the singleton orchestrator must be used everywhere."""

    @pytest.mark.asyncio
    async def test_app_uses_singleton_orchestrator(self):
        app = NexusTUI()
        assert app.orchestrator is get_orchestrator()

    @pytest.mark.asyncio
    async def test_ui_callback_wired_to_app(self):
        app = NexusTUI()
        assert app.orchestrator._ui_callback == app._on_orchestrator_event

    @pytest.mark.asyncio
    async def test_registry_is_wrapped_for_live_events(self):
        app = NexusTUI()
        assert isinstance(app.orchestrator.tools, ToolCallRecorder)

    @pytest.mark.asyncio
    async def test_agent_status_event_coerces_string_status(self):
        app = NexusTUI()
        sm = app.state_manager
        sm.add_agent("nexus-core", "orchestrator")
        app._on_orchestrator_event("agent_status", {"name": "nexus-core", "status": "THINKING", "task": "t"})
        agent = sm.state.active_agents[0]
        assert agent.status == AgentStatus.THINKING
        app._on_orchestrator_event("agent_status", {"name": "nexus-core", "status": "garbage"})
        assert agent.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_thinking_event_creates_and_finishes_steps(self):
        app = NexusTUI()

        class FakeEngineStep:
            step_id = "step_7"
            title = "Calling tool: edit"
            detail = "path=a.py"
            duration_ms = 123.4

            class state:
                value = "complete"

        app._on_thinking_event(("start", FakeEngineStep()))
        steps = app.state_manager.state.thinking_steps
        assert len(steps) == 1 and steps[0].step_id == "step_7"
        app._on_thinking_event(("start", FakeEngineStep()))  # dedupe
        assert len(steps) == 1
        app._on_thinking_event(("finish", FakeEngineStep()))
        assert steps[0].status == "done"
        assert steps[0].duration_ms == 123


class TestNexusTUILayout:
    """Chat is the hero at 120x38 and 90x30; panels never crush it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("size", [(120, 38), (90, 30)])
    async def test_boot_layout(self, size):
        app = NexusTUI()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            chat = app.query_one("#chat-panel")
            assert chat.region.height >= 8, "chat must keep a visible floor"
            assert app.query_one(WelcomeCard) is not None
            # All three side panels hidden when empty and not pinned
            for pid in ("#thinking-panel", "#tool-panel", "#agents-panel"):
                assert app.query_one(pid).has_class("hidden")
            status_text = str(app.query_one("#status-bar").render())
            assert "nexus" in status_text
            assert "no provider" in status_text  # graceful empty state

    @pytest.mark.asyncio
    async def test_panels_share_space_without_crushing_chat(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            sm = app.state_manager
            sm.start_tool("edit")
            sm.finish_tool("edit", result="ok")
            sm.add_agent("nexus-core", "orchestrator")
            sm.add_thinking_step(1, "Analyzing…", "d", step_id="s1")
            await pilot.pause()
            chat_h = app.query_one("#chat-panel").region.height
            assert chat_h >= 8
            total = sum(
                app.query_one(pid).region.height
                for pid in ("#thinking-panel", "#tool-panel", "#agents-panel")
            )
            # panels + chat + chrome must fit the screen without overflow
            assert chat_h + total <= 30 - 9 + 1  # fixed chrome is 9 rows

    @pytest.mark.asyncio
    async def test_welcome_card_mounted_at_boot(self):
        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            assert app.query_one(WelcomeCard) is not None


class TestOrchestrationTaskErrorPath:
    """Recon M2-4: the error handler must pass a ChatMessage, not a str."""

    @pytest.mark.asyncio
    async def test_failed_task_posts_chat_message(self):
        import asyncio as aio

        class FakeStatusBar:
            def __init__(self):
                self.message = ""

            def update(self, message=""):
                self.message = message

        class FakeChatPanel:
            def __init__(self):
                self.added = []

            def add_message(self, msg):
                self.added.append(msg)

        class FakeTUI:
            def __init__(self):
                self.status = FakeStatusBar()
                self.chat = FakeChatPanel()

            def query_one(self, selector):
                return self.status if selector == "StatusBar" else self.chat

        async def boom():
            raise ValueError("kaput")

        tui = FakeTUI()
        task = OrchestrationTask(tui, boom())
        await task.run()  # must NOT raise
        assert len(tui.chat.added) == 1
        from nexus.tui.state import ChatMessage, MessageRole

        msg = tui.chat.added[0]
        assert isinstance(msg, ChatMessage)
        assert msg.role == MessageRole.SYSTEM
        assert "kaput" in msg.content


class TestStateManagerHelpers:
    """New state helpers used by the live panels and status bar."""

    def test_record_message_does_not_notify(self):
        sm = get_state_manager()
        calls = []
        sm.subscribe(lambda state: calls.append(1))
        from nexus.tui.state import ChatMessage, MessageRole

        before = len(calls)
        sm.record_message(ChatMessage(role=MessageRole.USER, content="x"))
        assert len(calls) == before  # history mirror only

    def test_set_turn_stats_accumulates(self):
        sm = get_state_manager()
        sm.set_turn_stats(tokens_used=100, duration_ms=50, max_context_tokens=1000)
        sm.set_turn_stats(tokens_used=50, duration_ms=50)
        assert sm.state.total_tokens == 150
        assert sm.state.context_tokens == 50
        assert sm.state.max_context_tokens == 1000

    def test_add_agent_is_idempotent(self):
        sm = get_state_manager()
        sm.add_agent("nexus-core", "orchestrator", model="m1")
        sm.add_agent("nexus-core", "orchestrator", model="m2")
        assert len(sm.state.active_agents) == 1
        assert sm.state.active_agents[0].model == "m2"

    def test_update_thinking_step_matches_by_id(self):
        sm = get_state_manager()
        sm.add_thinking_step(1, "step", "d", step_id="s9")
        sm.update_thinking_step("s9", status="done", details="final", duration_ms=10)
        step = sm.state.thinking_steps[0]
        assert step.status == "done" and step.details == "final" and step.duration_ms == 10

    def test_refresh_renotifies(self):
        sm = get_state_manager()
        calls = []
        sm.subscribe(lambda state: calls.append(state))
        n = len(calls)
        sm.refresh()
        assert len(calls) == n + 1
