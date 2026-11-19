"""Paste chips + message queue — the Claude-Code-class input UX round.

Covers the paste-chip pipeline (NexusInput paste interception → in-memory
store → resolved agent payload → transcript chip rendering → ctrl+o toggle)
and the busy type-ahead queue (enter=queue → FIFO flush at turn end →
ctrl+u send now → /queue list/clear), plus the REPL chip echo.

Pilot tests boot the real app in Textual's headless mode; the orchestrator
singleton's run() is monkeypatched per test so no provider/network is
touched. The ~/.nexus runtime state is shared with other agent processes
and occasionally ends up with a 0-byte facts.json that crashes Memory on
boot; heal it before importing.
"""

import asyncio
import json
import os
import re
import types

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

from textual import events  # noqa: E402

from nexus.agent import get_orchestrator  # noqa: E402
from nexus.tui.app import NexusTUI  # noqa: E402
from nexus.tui.paste import (  # noqa: E402
    collapse_paste,
    expand_segments,
    get_paste_store,
    make_chip,
    reset_paste_store,
    resolve_chips,
    should_collapse,
    summarize_for_queue,
)
from nexus.tui.state import ChatMessage, MessageRole, get_state_manager  # noqa: E402
from nexus.tui.widgets import (  # noqa: E402
    ChatMessageWidget,
    NexusInput,
    QueueBar,
    SlashCommandDropdown,
)


def _block_of(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(n))


def _turn(assistant="ok"):
    """Minimal stand-in for orchestrator.Turn."""
    return types.SimpleNamespace(
        assistant_message=assistant,
        tokens_used=10,
        duration_ms=5,
        pending_approval=None,
        error=None,
    )


def _threshold(value: int) -> None:
    from nexus.tui.settings import get_settings

    get_settings().paste_collapse_lines = value


async def _settle(app, pilot, predicate, limit=30):
    """Pump the pilot until a condition holds (or the limit runs out)."""
    for _ in range(limit):
        await pilot.pause()
        if predicate():
            return True
    return predicate()


@pytest.fixture(autouse=True)
def _isolated_store_settings_state(tmp_path, monkeypatch):
    """Fresh paste store, per-test settings file, clean TUI state."""
    monkeypatch.setenv("NEXUS_TUI_SETTINGS_PATH", str(tmp_path / "settings.json"))
    from nexus.tui.settings import reset_settings_instance

    reset_settings_instance()
    reset_paste_store()
    get_state_manager().reset()
    yield
    get_state_manager().reset()
    reset_paste_store()
    reset_settings_instance()


@pytest.fixture()
def fake_run(monkeypatch):
    """Replace the singleton orchestrator's run() with an instant recorder."""
    orchestrator = get_orchestrator()
    calls: list[str] = []

    async def _run(user_input, stream_callback=None, **kwargs):
        calls.append(user_input)
        return _turn()

    monkeypatch.setattr(orchestrator, "run", _run)
    return calls


def _user_messages(app) -> list[ChatMessage]:
    return [m for m in app.query_one("#chat-panel")._messages
            if m.role == MessageRole.USER]


def _sys_messages(app) -> list[str]:
    return [m.content for m in app.query_one("#chat-panel")._messages
            if m.role == MessageRole.SYSTEM]


# ── paste store / chip primitives ────────────────────────────────────────────


class TestPasteChips:
    def test_chip_format(self):
        assert make_chip(2, 42) == "[Pasted text #2 +42 lines]"
        assert make_chip(1, 1) == "[Pasted text #1 +1 lines]"

    def test_store_numbering_is_session_monotonic(self):
        store = get_paste_store()
        assert store.add(_block_of(3)) == 1
        assert store.add(_block_of(4)) == 2
        store.clear()
        assert len(store) == 0
        assert store.add("x") == 3  # numbering never resets or reuses

    def test_collapse_at_under_and_over_threshold(self):
        assert should_collapse(_block_of(15), 15) is False  # exactly at → raw
        assert should_collapse(_block_of(16), 15) is True  # over → chip
        assert collapse_paste(_block_of(15), 15) is None
        chip = collapse_paste(_block_of(16), 15)
        assert chip == "[Pasted text #1 +16 lines]"
        assert get_paste_store().get(1) == _block_of(16)

    def test_single_long_line_never_collapses(self):
        assert should_collapse("x" * 5000, 15) is False

    def test_resolve_mixed_text_and_chips(self):
        store = get_paste_store()
        store.add(_block_of(4))  # #1
        assert store.add(_block_of(3)) == 2  # #2
        mixed = f"added this:\n{make_chip(2, 3)}\nplease fix"
        resolved = resolve_chips(mixed, store)
        assert resolved == f"added this:\n{_block_of(3)}\nplease fix"
        # unknown chip numbers pass through untouched
        assert resolve_chips("[Pasted text #99 +1 lines]", store) == "[Pasted text #99 +1 lines]"

    def test_expand_segments_runs(self):
        store = get_paste_store()
        store.add(_block_of(3))
        text = f"a\n{make_chip(1, 3)}\nb"
        runs = expand_segments(text, store)
        assert [is_paste for _, is_paste in runs] == [False, True, False]
        assert _block_of(3) in runs[1][0]
        assert runs[0][0] == "a\n" and runs[2][0] == "\nb"

    def test_summarize_for_queue_flattens_and_truncates(self):
        assert summarize_for_queue("one\ntwo\nthree", 60) == "one two three"
        long = "x" * 200
        assert len(summarize_for_queue(long, 60)) == 60
        assert summarize_for_queue(long, 60).endswith("…")

    def test_singleton_and_reset(self):
        get_paste_store().add("keep me")
        reset_paste_store()
        assert len(get_paste_store()) == 0


# ── settings field ───────────────────────────────────────────────────────────


class TestPasteCollapseSetting:
    def test_default_value(self):
        from nexus.tui.settings import TUISettings

        assert TUISettings().paste_collapse_lines == 15

    def test_save_load_roundtrip(self, tmp_path):
        from nexus.tui.settings import TUISettings, load_settings, save_settings

        path = str(tmp_path / "settings.json")
        settings = TUISettings(paste_collapse_lines=23)
        save_settings(settings, path)
        loaded, notes = load_settings(path)
        assert loaded.paste_collapse_lines == 23
        assert notes == []

    def test_wrong_type_resets_with_note(self, tmp_path):
        from nexus.tui.settings import load_settings

        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"paste_collapse_lines": "many"}), encoding="utf-8")
        loaded, notes = load_settings(str(path))
        assert loaded.paste_collapse_lines == 15
        assert any("wrong type" in n for n in notes)

    def test_below_one_resets_with_note(self, tmp_path):
        from nexus.tui.settings import load_settings

        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"paste_collapse_lines": 0}), encoding="utf-8")
        loaded, notes = load_settings(str(path))
        assert loaded.paste_collapse_lines == 15
        assert any(">= 1" in n for n in notes)

    def test_row_in_behavior_group_and_cycles(self, tmp_path):
        from nexus.tui.settings import (
            SettingsModel,
            get_settings,
            load_settings,
            save_settings_now,
        )

        model = SettingsModel()
        rows = [r for r in model.rows if r.label == "paste collapse lines"]
        assert len(rows) == 1
        row = rows[0]
        assert row.group == "Behavior"
        row.cycle(1)  # 15 → 20
        assert get_settings().paste_collapse_lines == 20
        save_settings_now()
        # persisted to the per-test settings file
        loaded, _ = load_settings(str(tmp_path / "settings.json"))
        assert loaded.paste_collapse_lines == 20

    def test_row_format_fits_label_width(self):
        from nexus.tui.settings import SettingsModel

        model = SettingsModel()
        row = next(r for r in model.rows if r.label == "paste collapse lines")
        line = model.format_row(row)
        assert line.startswith("paste collapse lines")
        assert line.strip().endswith("15")


# ── TUI input paste handling (pilot) ─────────────────────────────────────────


class TestTUIPasteInput:
    @pytest.mark.asyncio
    async def test_long_paste_becomes_chip_in_input(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            _threshold(3)
            inp = app.query_one("#command-input", NexusInput)
            assert isinstance(inp, NexusInput)
            inp._on_paste(events.Paste(text=_block_of(6)))
            assert inp.value == "[Pasted text #1 +6 lines]"
            assert get_paste_store().get(1) == _block_of(6)

    @pytest.mark.asyncio
    async def test_two_pastes_append_chips(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            _threshold(3)
            inp = app.query_one("#command-input", NexusInput)
            inp._on_paste(events.Paste(text=_block_of(4)))
            inp.value += " and "
            inp.cursor_position = len(inp.value)
            inp._on_paste(events.Paste(text=_block_of(5)))
            assert inp.value == "[Pasted text #1 +4 lines] and [Pasted text #2 +5 lines]"
            store = get_paste_store()
            assert store.get(1) == _block_of(4)
            assert store.get(2) == _block_of(5)

    @pytest.mark.asyncio
    async def test_short_multiline_paste_never_submits_partial_line(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            _threshold(15)
            inp = app.query_one("#command-input", NexusInput)
            inp._on_paste(events.Paste(text="alpha\nbeta\ngamma"))
            # flattened into the single-line input — never a bare first line
            assert inp.value == "alpha beta gamma"
            inp.focus()
            await pilot.press("enter")
            await _settle(app, pilot, lambda: bool(app._command_history))
            # the WHOLE paste was submitted, not just "alpha"
            assert app._command_history[-1] == "alpha beta gamma"

    @pytest.mark.asyncio
    async def test_at_threshold_paste_stays_raw(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            _threshold(3)
            inp = app.query_one("#command-input", NexusInput)
            inp._on_paste(events.Paste(text=_block_of(3)))  # exactly at → raw
            assert inp.value == _block_of(3).replace("\n", " ")
            assert len(get_paste_store()) == 0


# ── transcript rendering + ctrl+o ────────────────────────────────────────────


class TestTranscriptChipRender:
    def _seed_message(self, app) -> ChatMessage:
        store = get_paste_store()
        store.add(_block_of(4))
        msg = ChatMessage(
            role=MessageRole.USER,
            content=f"added this:\n{make_chip(1, 4)}\nplease fix",
        )
        app.query_one("#chat-panel").add_message(msg)
        return msg

    @pytest.mark.asyncio
    async def test_transcript_renders_chip_not_dump(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            self._seed_message(app)
            await pilot.pause()
            widget = list(app.query(ChatMessageWidget))[-1]
            plain = widget._content_text().plain
            assert "[Pasted text #1 +4 lines]" in plain
            assert "line 0" not in plain  # the dump never hits the transcript

    @pytest.mark.asyncio
    async def test_ctrl_o_toggles_expand_collapse(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            self._seed_message(app)
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            widget = list(app.query(ChatMessageWidget))[-1]
            plain = widget._content_text().plain
            assert "line 0" in plain and "line 3" in plain
            assert "[Pasted text #1 +4 lines]" not in plain
            await pilot.press("ctrl+o")
            await pilot.pause()
            widget = list(app.query(ChatMessageWidget))[-1]
            plain = widget._content_text().plain
            assert "[Pasted text #1 +4 lines]" in plain
            assert "line 0" not in plain

    @pytest.mark.asyncio
    async def test_system_messages_never_expand(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            get_paste_store().add(_block_of(4))
            app.query_one("#chat-panel").add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"note {make_chip(1, 4)}",
            ))
            await pilot.pause()
            get_paste_store().expanded = True
            widget = list(app.query(ChatMessageWidget))[-1]
            # only USER messages expand — sys notes keep the chip inline
            assert "[Pasted text #1 +4 lines]" in widget._content_text().plain


# ── payload resolution (agent gets the full text) ────────────────────────────


class TestPayloadResolution:
    @pytest.mark.asyncio
    async def test_submit_sends_resolved_payload_transcript_keeps_chips(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            _threshold(3)
            inp = app.query_one("#command-input", NexusInput)
            inp._on_paste(events.Paste(text=_block_of(4)))
            inp.value += " please fix"
            inp.focus()
            await pilot.press("enter")
            assert await _settle(app, pilot, lambda: len(fake_run) == 1)
            # agent payload: full pasted text + typed suffix, chips resolved
            assert fake_run == [f"{_block_of(4)} please fix"]
            # transcript: the chip-mixed text the user saw in the input
            assert _user_messages(app)[0].content == "[Pasted text #1 +4 lines] please fix"

    @pytest.mark.asyncio
    async def test_unknown_chip_token_passes_through(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#command-input", NexusInput)
            inp.value = "looks like [Pasted text #7 +3 lines] but is not"
            inp.focus()
            await pilot.press("enter")
            assert await _settle(app, pilot, lambda: len(fake_run) == 1)
            assert fake_run == ["looks like [Pasted text #7 +3 lines] but is not"]


# ── message queue ────────────────────────────────────────────────────────────


class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_enter_while_busy_queues_not_sends(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#command-input", NexusInput)
            inp.value = "first message"
            inp.focus()
            await pilot.press("enter")
            assert await _settle(app, pilot, lambda: len(fake_run) == 1)
            assert fake_run == ["first message"]
            # simulate the turn still running, then type ahead
            app._turn_active = True
            inp.value = "second message"
            await pilot.press("enter")
            assert await _settle(app, pilot, lambda: bool(app._queued_messages))
            assert app._queued_messages == ["second message"]
            assert fake_run == ["first message"]  # nothing new was sent
            assert len(_user_messages(app)) == 1  # queued input not in transcript

    @pytest.mark.asyncio
    async def test_empty_input_enter_while_busy_noop(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app.state_manager.set_busy(True)
            app._turn_active = True
            inp = app.query_one("#command-input", NexusInput)
            inp.value = "   "
            inp.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app._queued_messages == []

    def test_queue_bar_renders_rows_hint_and_hides_when_empty(self):
        bar = QueueBar()
        assert bar.render_queued([], True) == ""
        text = bar.render_queued(["fix the parser", "update tests"], True)
        assert "⎿ queued: fix the parser" in text
        assert "⎿ queued: update tests" in text
        assert QueueBar.BUSY_HINT in text
        idle = bar.render_queued(["one"], False)
        assert QueueBar.BUSY_HINT not in idle  # hint only while busy

    def test_queue_rows_fit_90_cols(self):
        bar = QueueBar()
        text = bar.render_queued(["x" * 200, "y" * 200, "z" * 200], True)
        plain = re.sub(r"\[[^\]]*\]", "", text)
        for line in plain.splitlines():
            assert len(line) <= 72  # 11-char prefix + 60-char capped summary

    @pytest.mark.asyncio
    async def test_queue_flush_fifo_after_turn_ends(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#command-input", NexusInput)
            inp.value = "first"
            inp.focus()
            await pilot.press("enter")
            assert await _settle(app, pilot, lambda: len(fake_run) == 1)
            # queue two more while the first turn is still "running"
            app._turn_active = True
            inp.value = "second"
            await pilot.press("enter")
            inp.value = "third"
            await pilot.press("enter")
            assert await _settle(app, pilot,
                                 lambda: app._queued_messages == ["second", "third"])
            # the turn ends → its finally flushes the queue in FIFO order
            app._turn_active = False
            app.state_manager.set_busy(False)
            app._flush_queue()
            assert await _settle(
                app, pilot,
                lambda: not app._queued_messages and len(fake_run) >= 3,
            )
            assert fake_run == ["first", "second", "third"]
            assert app._queued_messages == []

    @pytest.mark.asyncio
    async def test_ctrl_u_interrupts_running_turn_and_flushes(self, monkeypatch):
        calls: list[str] = []
        cancelled: list[str] = []

        async def blocking_run(user_input, stream_callback=None, **kwargs):
            calls.append(user_input)
            if user_input == "first":
                try:
                    await asyncio.Event().wait()  # blocked until ctrl+u
                except asyncio.CancelledError:
                    cancelled.append(user_input)
                    raise
            return _turn(f"done: {user_input}")

        monkeypatch.setattr(get_orchestrator(), "run", blocking_run)
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#command-input", NexusInput)
            inp.value = "first"
            inp.focus()
            await pilot.press("enter")
            assert await _settle(app, pilot,
                                 lambda: app.state_manager.state.is_busy)
            # queue a second message while the first blocks
            inp.value = "second"
            await pilot.press("enter")
            assert await _settle(app, pilot, lambda: bool(app._queued_messages))
            # ctrl+u: interrupt the running turn, then the queue flushes
            await pilot.press("ctrl+u")
            assert await _settle(app, pilot, lambda: len(calls) >= 2)
            assert cancelled == ["first"]
            assert calls == ["first", "second"]
            assert "⏹ interrupted" in _sys_messages(app)

    @pytest.mark.asyncio
    async def test_ctrl_u_while_idle_is_noop(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+u")
            await pilot.pause()
            assert "⏹ interrupted" not in _sys_messages(app)
            assert fake_run == []
            assert app._turn_task is None

    @pytest.mark.asyncio
    async def test_ctrl_u_double_press_does_not_double_interrupt(self, monkeypatch):
        calls: list[str] = []

        async def blocking_run(user_input, stream_callback=None, **kwargs):
            calls.append(user_input)
            if user_input == "first":
                await asyncio.Event().wait()  # cancelled by ctrl+u
            return _turn()

        monkeypatch.setattr(get_orchestrator(), "run", blocking_run)
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#command-input", NexusInput)
            inp.value = "first"
            inp.focus()
            await pilot.press("enter")
            assert await _settle(app, pilot,
                                 lambda: app.state_manager.state.is_busy)
            await pilot.press("ctrl+u")
            await pilot.press("ctrl+u")  # second press during unwind → no-op
            await _settle(app, pilot, lambda: "⏹ interrupted" in _sys_messages(app))
            await pilot.pause()
            sys_msgs = _sys_messages(app)
            assert sys_msgs.count("⏹ interrupted") == 1

    @pytest.mark.asyncio
    async def test_queue_command_list_clear_and_idle(self, fake_run):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app._handle_command("/queue")
            assert _sys_messages(app)[-1] == "queue is empty"
            app._queued_messages.extend(["fix the parser module", "update the tests"])
            app._handle_command("/queue")
            assert "fix the parser module" in _sys_messages(app)[-1]
            app._handle_command("/queue clear")
            assert app._queued_messages == []
            assert "Queue cleared (2 dropped)" in _sys_messages(app)[-1]
            # bar hidden after clear
            assert app.query_one("#queue-rows").has_class("hidden")
            app._handle_command("/queue clear")
            assert _sys_messages(app)[-1] == "Queue is empty"

    @pytest.mark.asyncio
    async def test_queued_messages_flatten_in_bar_rows(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app.state_manager.set_busy(True)
            app._queued_messages.append("line one\nline two")
            app._update_queue_bar()
            await pilot.pause()
            bar = app.query_one("#queue-rows")
            assert not bar.has_class("hidden")
            assert "⎿ queued: line one line two" in str(bar.render())


# ── /help + completions ──────────────────────────────────────────────────────


class TestHelpAndCompletions:
    @pytest.mark.asyncio
    async def test_help_mentions_chips_queue_and_keys(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            app.action_show_help()
            await pilot.pause()
            help_text = "\n".join(_sys_messages(app))
            assert "Pasted text" in help_text
            assert "ctrl+o" in help_text and "ctrl+u" in help_text
            assert "/queue" in help_text

    def test_dropdown_and_palette_include_queue(self):
        commands = dict(SlashCommandDropdown.COMMANDS)
        assert "/queue" in commands
        from nexus.tui.palette import CommandPalette

        assert "/queue" in CommandPalette.COMMANDS


# ── REPL chip echo ───────────────────────────────────────────────────────────


class TestReplChipEcho:
    def _repl(self):
        from nexus.cli.repl import REPL

        return REPL()

    def test_long_input_echoes_chip_returns_full_text(self, capsys):
        _threshold(3)
        repl = self._repl()
        long_input = _block_of(6)
        returned = repl._echo_paste_chip(long_input)
        out = capsys.readouterr().out
        assert returned == long_input  # the agent still gets everything
        assert "[Pasted text #1 +6 lines]" in out
        assert "line 0" not in out  # no raw dump
        # shared store with the TUI
        assert get_paste_store().get(1) == long_input

    def test_short_input_no_echo(self, capsys):
        _threshold(15)
        repl = self._repl()
        returned = repl._echo_paste_chip(_block_of(5))
        assert returned == _block_of(5)
        assert capsys.readouterr().out == ""

    def test_repl_numbering_continues_after_tui_chips(self):
        store = get_paste_store()
        store.add("from the tui")
        _threshold(2)
        repl = self._repl()
        repl._echo_paste_chip(_block_of(4))
        assert store.get(2) == _block_of(4)

    def test_help_documents_tui_only_queue(self, capsys):
        repl = self._repl()
        asyncio.run(repl._handle_command("/help"))
        out = capsys.readouterr().out
        assert "TUI only" in out
        assert "inline" in out
