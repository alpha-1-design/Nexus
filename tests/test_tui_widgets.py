"""Tests for TUI widgets — SlashCommandDropdown, StatusBar, etc."""

import pytest

from nexus.tui.widgets import SlashCommandDropdown, StatusBar


class TestSlashCommandDropdown:
    def test_all_commands_present(self):
        """Should have all expected slash commands."""
        cmd_names = [cmd for cmd, _ in SlashCommandDropdown.COMMANDS]
        assert "/help" in cmd_names
        assert "/clear" in cmd_names
        assert "/history" in cmd_names
        assert "/tools" in cmd_names
        assert "/model" in cmd_names
        assert "/provider" in cmd_names
        assert "/session" in cmd_names
        assert "/facts" in cmd_names
        assert "/voice" in cmd_names
        assert "/plan" in cmd_names
        assert "/build" in cmd_names
        assert "/safety" in cmd_names
        assert "/doctor" in cmd_names
        assert "/mcp" in cmd_names
        assert "/sync" in cmd_names
        assert "/learn" in cmd_names
        assert "/improve" in cmd_names
        assert "/exit" in cmd_names
        assert "/spawn" in cmd_names
        assert "/fact" in cmd_names

    def test_commands_have_descriptions(self):
        """Every command should have a non-empty description."""
        for cmd, desc in SlashCommandDropdown.COMMANDS:
            assert desc, f"Command {cmd} has no description"
            assert len(desc) > 2

    def test_commands_start_with_slash(self):
        """All commands should start with /."""
        for cmd, _ in SlashCommandDropdown.COMMANDS:
            assert cmd.startswith("/"), f"{cmd} does not start with /"

    def test_initial_filter_shows_all(self):
        """Empty filter should show all commands."""
        dropdown = SlashCommandDropdown(filter_text="")
        dropdown._update_filtered()
        assert len(dropdown._filtered) == len(SlashCommandDropdown.COMMANDS)

    def test_filter_matches_partial(self):
        """Partial filter should match relevant commands."""
        dropdown = SlashCommandDropdown(filter_text="/f")
        dropdown._update_filtered()
        names = [cmd for cmd, _ in dropdown._filtered]
        assert "/facts" in names
        assert "/fact" in names

    def test_filter_case_insensitive(self):
        """Filter should be case-insensitive."""
        dropdown = SlashCommandDropdown(filter_text="/HELP")
        dropdown._update_filtered()
        names = [cmd for cmd, _ in dropdown._filtered]
        assert "/help" in names

    def test_filter_no_match(self):
        """Non-matching filter should return empty list."""
        dropdown = SlashCommandDropdown(filter_text="/zzzz")
        dropdown._update_filtered()
        assert len(dropdown._filtered) == 0

    def test_filter_single_slash(self):
        """Just / should show all commands."""
        dropdown = SlashCommandDropdown(filter_text="/")
        dropdown._update_filtered()
        assert len(dropdown._filtered) == len(SlashCommandDropdown.COMMANDS)

    def test_select_next_index_cycles(self):
        """select_next should advance the index through filtered items."""
        dropdown = SlashCommandDropdown(filter_text="")
        dropdown._update_filtered()
        initial = dropdown.selected_index
        dropdown.selected_index = (dropdown.selected_index + 1) % len(dropdown._filtered)
        assert dropdown.selected_index == (initial + 1) % len(dropdown._filtered)

    def test_select_prev_index_cycles(self):
        """select_prev should move index backwards."""
        dropdown = SlashCommandDropdown(filter_text="")
        dropdown._update_filtered()
        dropdown.selected_index = 1
        dropdown.selected_index = (dropdown.selected_index - 1) % len(dropdown._filtered)
        assert dropdown.selected_index == 0

    def test_get_selected_with_results(self):
        """get_selected should return the command at selected_index."""
        dropdown = SlashCommandDropdown(filter_text="")
        dropdown._update_filtered()
        dropdown.selected_index = 0
        cmd = dropdown._filtered[0][0] if dropdown._filtered else None
        assert cmd == SlashCommandDropdown.COMMANDS[0][0]

    def test_get_selected_no_results(self):
        """get_selected should return None when no results."""
        dropdown = SlashCommandDropdown(filter_text="/zzzz")
        dropdown._update_filtered()
        assert len(dropdown._filtered) == 0

    def test_select_next_logic(self):
        """Select next logic should wrap around."""
        dropdown = SlashCommandDropdown(filter_text="")
        dropdown._update_filtered()
        # Simulate select_next without app context
        new_index = (dropdown.selected_index + 1) % len(dropdown._filtered)
        assert new_index == 1


class TestSlashCommandDropdownRendering:
    """Regression tests for a real crash: `SlashCommandDropdown` defined a
    method named `_render`, which collided with Textual's own internal
    `Widget._render()` used by the framework to produce the widget's
    visual output. Because our `_render()` returned None (it only mounted
    child widgets), Textual treated that None as the render result and
    crashed with `AttributeError: 'NoneType' object has no attribute
    'render_strips'` the moment the dropdown was actually drawn on screen.
    None of the older tests caught this because they replicated the
    dropdown's index arithmetic inline instead of calling the real
    `select_next`/`select_prev`/`_rebuild_items` methods and never
    mounted the widget in a running app to force a real render pass.
    """

    @pytest.mark.asyncio
    async def test_dropdown_renders_without_crashing(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield SlashCommandDropdown(filter_text="/h")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            # If the _render naming collision regresses, Textual raises
            # inside its own rendering pipeline during this pause/refresh,
            # which surfaces as an exception from run_test().
            dropdown = app.query_one(SlashCommandDropdown)
            assert len(dropdown.children) > 0

    def test_rebuild_items_method_exists_not_shadowing_render(self):
        """`_render` must never be reintroduced as a method name on a
        Textual Container/Widget subclass -- it silently shadows the
        framework's own internal rendering hook, which caused a real
        production crash. The rebuild-children logic must live under a
        different name."""
        assert hasattr(SlashCommandDropdown, "_rebuild_items")
        # SlashCommandDropdown itself must not define its own _render;
        # any _render found on it should only be the one inherited from
        # Textual's own Widget/Container base classes.
        own_methods = vars(SlashCommandDropdown)
        assert "_render" not in own_methods

    @pytest.mark.asyncio
    async def test_select_next_and_prev_call_real_methods_without_crashing(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield SlashCommandDropdown(filter_text="")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            dropdown = app.query_one(SlashCommandDropdown)
            start = dropdown.selected_index
            dropdown.select_next()
            assert dropdown.selected_index == (start + 1) % len(dropdown._filtered)
            dropdown.select_prev()
            assert dropdown.selected_index == start
            await pilot.pause()  # force another render pass after mutation


class TestChatMessageWidgetMarkupSafety:
    """Regression tests for a real content-corruption bug: message content
    was passed straight into a Rich-markup-enabled Static widget, so any
    text containing bracketed substrings that happen to look like style
    tags -- e.g. `List[int]`, `Dict[str, int]`, `config[key]`, which is
    extremely common in code a coding agent produces -- was silently
    stripped from the visible output.
    """

    def test_type_annotated_code_is_not_stripped(self):
        from nexus.tui.state import ChatMessage, MessageRole
        from nexus.tui.widgets import ChatMessageWidget
        from datetime import datetime

        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content="def foo(x: List[int]) -> Dict[str, int]: ...",
            timestamp=datetime.now(),
        )
        widget = ChatMessageWidget(msg)
        # markup must be disabled for content so brackets are never
        # interpreted as Rich style tags
        rendered = widget._format_content()
        assert "List[int]" in rendered
        assert "Dict[str, int]" in rendered

    @pytest.mark.asyncio
    async def test_bracketed_content_survives_actual_render(self):
        from textual.app import App
        from nexus.tui.state import ChatMessage, MessageRole
        from nexus.tui.widgets import ChatMessageWidget
        from datetime import datetime

        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Use config[key] and List[int] carefully.",
            timestamp=datetime.now(),
        )

        class HarnessApp(App):
            def compose(self):
                yield ChatMessageWidget(msg)

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            content_widget = app.query_one(".content")
            rendered_text = content_widget.content
            # Whether it's a plain str or a Rich Text object, the bracketed
            # substrings must be present in the final rendered text.
            text_str = str(rendered_text)
            assert "config[key]" in text_str
            assert "List[int]" in text_str


class TestInputBarSingleDispatch:
    """Regression test for a severe bug: `InputBar.on_input_submitted`
    posted a `CommandEntered` message but never called `event.stop()`, so
    the same `Input.Submitted` event continued bubbling to the App's own
    (now-removed) `on_input_submitted` handler -- meaning every single
    user message was dispatched and executed *twice*.
    """

    def test_input_submitted_event_is_stopped(self):
        from nexus.tui.widgets import InputBar
        import inspect

        source = inspect.getsource(InputBar.on_input_submitted)
        assert "event.stop()" in source, (
            "InputBar.on_input_submitted must call event.stop() or the "
            "Input.Submitted message will bubble to the App and cause "
            "duplicate command dispatch"
        )

    def test_app_no_longer_defines_duplicate_submit_handlers(self):
        """The App used to define on_input_submitted and
        on_input_bar_command_entered in addition to the correct
        on_command_entered handler, causing commands to be processed
        multiple times over redundant paths. Only on_command_entered
        should remain."""
        from nexus.tui.app import NexusTUI

        assert hasattr(NexusTUI, "on_command_entered")
        assert not hasattr(NexusTUI, "on_input_submitted")
        assert not hasattr(NexusTUI, "on_input_bar_command_entered")
        assert not hasattr(NexusTUI, "on_input_key_down")


class TestStatusBar:
    def test_default_values(self):
        """Default status bar should have correct initial values."""
        bar = StatusBar(version="0.1.0")
        assert bar.version == "0.1.0"
        assert bar.message == ""
        assert bar.model == ""
        assert bar.project == ""

    def test_with_model(self):
        """Should store model when provided."""
        bar = StatusBar(version="0.1.0", model="gpt-4")
        assert bar.model == "gpt-4"

    def test_with_project(self):
        """Should store project when provided."""
        bar = StatusBar(version="0.1.0", project="myproject")
        assert bar.project == "myproject"

    def test_with_termux(self):
        """Should store termux flag."""
        bar = StatusBar(version="0.1.0", termux=True)
        assert bar.termux is True

    def test_with_battery(self):
        """Should store battery value."""
        bar = StatusBar(version="0.1.0", battery=80)
        assert bar.battery == 80

    def test_update_changes_message(self):
        """update() should set the message."""
        bar = StatusBar(version="0.1.0")
        bar.update("custom status")
        assert bar.message == "custom status"


# ════════════════════════════════════════════════════════════════════
# TUI v2 tests — transcript redesign (timestamps, tool cards, diffs,
# panel visibility, welcome card, status bar)
# ════════════════════════════════════════════════════════════════════

from datetime import datetime

from nexus.tui.state import (
    AgentInfo,
    AgentStatus,
    ChatMessage,
    MessageRole,
    ThinkingStep,
    ToolInfo,
    ToolStatus,
)
from nexus.tui.widgets import (
    ChatMessageWidget,
    DiffBlock,
    ThinkingPanel,
    ToolCardWidget,
    WelcomeCard,
    apply_inline_markup,
    classify_diff_line,
    render_diff,
    TOOL_STATUS_ICONS,
)
from rich.text import Text


def _msg(role=MessageRole.USER, content="hello", **kwargs):
    return ChatMessage(role=role, content=content, timestamp=datetime(2026, 1, 1, 14, 30, 5), **kwargs)


class TestChatMessageTimestamp:
    """Transcript entries carry a dim HH:MM stamp next to the role badge."""

    def test_header_contains_hhmm_stamp(self):
        widget = ChatMessageWidget(_msg())
        header = widget._header_markup()
        assert "14:30" in header

    def test_role_labels(self):
        assert "you" in ChatMessageWidget(_msg(MessageRole.USER))._header_markup()
        assert "nexus" in ChatMessageWidget(_msg(MessageRole.ASSISTANT))._header_markup()
        assert "sys" in ChatMessageWidget(_msg(MessageRole.SYSTEM))._header_markup()

    @pytest.mark.asyncio
    async def test_rendered_header_shows_badge_and_stamp(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield ChatMessageWidget(_msg())

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            header = app.query_one(".msg-header")
            text = str(header.render())
            assert "you" in text and "14:30" in text

    @pytest.mark.asyncio
    async def test_role_css_class_for_accent_border(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield ChatMessageWidget(_msg(MessageRole.ASSISTANT))

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(ChatMessageWidget).has_class("role-assistant")


class TestInlineMarkup:
    """**bold** / %%dim%% markers are hidden, not shown literally."""

    def test_bold_markers_removed_and_styled(self):
        text = apply_inline_markup(Text("say **hello** now"))
        assert str(text) == "say hello now"
        styles = [str(s.style) for s in text.spans]
        assert any("bold" in s for s in styles)

    def test_dim_markers_removed_and_styled(self):
        text = apply_inline_markup(Text("keep %%this%% quiet"))
        assert str(text) == "keep this quiet"
        styles = [str(s.style) for s in text.spans]
        assert any("dim" in s for s in styles)

    def test_code_brackets_untouched(self):
        text = apply_inline_markup(Text("def f(x: List[int]) -> Dict[str, int]"))
        assert "List[int]" in str(text)

    def test_plain_text_returned_unchanged(self):
        plain = Text("no markers here")
        assert str(apply_inline_markup(plain)) == "no markers here"

    def test_widget_content_hides_markers(self):
        widget = ChatMessageWidget(_msg(MessageRole.ASSISTANT, "do **it**"))
        assert str(widget._content_text()) == "do it"


class TestToolCardWidget:
    """Collapsed one-line card; expandable to args/result/diff."""

    def _card(self, **kwargs):
        msg = _msg(
            role=MessageRole.TOOL,
            content="",
            tool_name="edit",
            tool_args={"path": "src/foo.py", "old_string": "a", "new_string": "b"},
            tool_status="done",
            tool_result="Edit applied successfully",
            tool_duration_ms=412,
            **kwargs,
        )
        return ToolCardWidget(msg)

    def test_collapsed_header(self):
        card = self._card()
        header = card._header_markup()
        assert "▸" in header
        assert "Edit" in header
        assert "src/foo.py" in header
        assert "✓" in header
        assert "0.4s" in header

    def test_running_and_error_icons(self):
        running = self._card()
        running.message.tool_status = "running"
        assert "⟳" in running._header_markup()
        failed = self._card()
        failed.message.tool_status = "error"
        assert "✗" in failed._header_markup()
        assert TOOL_STATUS_ICONS["done"][0] == "✓"

    def test_expanded_action_flips_state(self):
        card = self._card()
        assert card.expanded is False
        card.action_toggle_expand()
        assert card.expanded is True
        card.action_toggle_expand()
        assert card.expanded is False

    @pytest.mark.asyncio
    async def test_enter_key_expands_focused_card(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield self.card

        harness = HarnessApp()
        harness.card = self._card()
        async with harness.run_test() as pilot:
            await pilot.pause()
            card = harness.card
            card.focus()
            await pilot.pause()
            await pilot.press("enter")
            assert card.expanded is True
            assert card.has_class("expanded")

    @pytest.mark.asyncio
    async def test_expanded_card_shows_args_and_result(self):
        from textual.app import App

        card = self._card()
        card.expanded = True

        class HarnessApp(App):
            def compose(self):
                yield card

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            args_text = str(app.query_one(".tool-card-args").render())
            assert "path" in args_text
            result_text = str(app.query_one(".tool-card-result").render())
            assert "Edit applied successfully" in result_text

    def test_set_status_updates_header(self):
        card = self._card()
        card.message.tool_status = "running"
        assert "⟳" in card._header_markup()
        card.set_status("error", error="File not found", duration_ms=50)
        assert card.message.tool_status == "error"
        assert "✗" in card._header_markup()
        assert "File not found" in card._result_markup()


class TestDiffRenderer:
    """Unified diff → + green, - red, @@/---/+++ dim cyan."""

    SAMPLE = (
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " context\n"
        "-old line\n"
        "+new line\n"
        "... 3 more lines\n"
    )

    def test_classify_lines(self):
        assert classify_diff_line("--- a/x") == "meta"
        assert classify_diff_line("+++ b/x") == "meta"
        assert classify_diff_line("@@ -1 +1 @@") == "hunk"
        assert classify_diff_line("+added") == "add"
        assert classify_diff_line("-removed") == "del"
        assert classify_diff_line(" context") == "ctx"
        assert classify_diff_line("... 12 more lines") == "meta"

    def test_render_diff_colors(self):
        text = render_diff(self.SAMPLE)
        styles = [str(s.style) for s in text.spans]
        assert any("34d399" in s for s in styles), "+ lines must be green"
        assert any("f87171" in s for s in styles), "- lines must be red"
        assert any("67e8f9" in s for s in styles), "@@/--- must be dim cyan"
        assert any("dim" in s for s in styles)

    def test_render_diff_preserves_content(self):
        text = render_diff(self.SAMPLE)
        plain = str(text)
        assert "+new line" in plain
        assert "-old line" in plain
        assert "@@ -1,2 +1,2 @@" in plain
        assert "... 3 more lines" in plain

    def test_diff_block_renders_text(self):
        block = DiffBlock(self.SAMPLE)
        result = block.render()
        assert isinstance(result, Text)
        assert "+new line" in str(result)


class TestPanelVisibility:
    """Side panels hide entirely when empty and not pinned."""

    @pytest.mark.asyncio
    async def test_empty_panel_hidden_content_shows_it(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield ThinkingPanel(id="thinking-panel", classes="hidden")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one(ThinkingPanel)
            assert panel.has_class("hidden")
            assert not panel.should_show

            panel.add_step(ThinkingStep(step_number=1, description="thinking", details="d"))
            panel.refresh_visibility()
            assert not panel.has_class("hidden")
            assert panel.should_show

    @pytest.mark.asyncio
    async def test_toggle_cycle_hide_then_pin(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield ThinkingPanel(id="thinking-panel", classes="hidden")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one(ThinkingPanel)
            panel.add_step(ThinkingStep(step_number=1, description="s", details=""))
            panel.refresh_visibility()

            panel.toggle_visibility()  # visible → dismissed
            assert panel.has_class("hidden")
            assert panel.should_show is False

            panel.toggle_visibility()  # hidden → pinned (visible even if empty)
            panel.clear()
            assert not panel.has_class("hidden")
            assert panel.pinned is True
            assert panel.should_show is True


class TestStatusBarRender:
    """model • provider • context % • tokens • cwd + pulse dot."""

    @pytest.fixture(autouse=True)
    def _clean_state(self):
        from nexus.tui.state import get_state_manager
        get_state_manager().reset()
        yield
        get_state_manager().reset()

    def _bar(self, **kwargs):
        from nexus.tui.widgets import StatusBar
        return StatusBar(version="0.1.0", **kwargs)

    def test_no_provider_state(self):
        bar = self._bar(model="", provider="")
        rendered = bar.render()
        assert "no model" in rendered
        assert "no provider" in rendered
        assert "nexus" in rendered

    def test_model_and_provider_shown(self):
        bar = self._bar(model="gpt-4o", provider="openai")
        rendered = bar.render()
        assert "gpt-4o" in rendered
        assert "openai" in rendered

    def test_context_and_tokens(self):
        from nexus.tui.state import get_state_manager
        sm = get_state_manager()
        sm.set_turn_stats(tokens_used=12400, duration_ms=1800, max_context_tokens=128000)
        bar = self._bar(model="m", provider="p")
        rendered = bar.render()
        assert "ctx 10%" in rendered
        assert "12.4k tok" in rendered

    def test_token_formatting(self):
        from nexus.tui.widgets import StatusBar
        assert StatusBar._fmt_tokens(999) == "999"
        assert StatusBar._fmt_tokens(12400) == "12.4k"

    def test_busy_pulse_is_accent(self):
        from nexus.tui.state import get_state_manager
        sm = get_state_manager()
        sm.set_busy(True)
        bar = self._bar()
        assert bar.render().count("●") >= 1


class TestChatPanelStreaming:
    """Streaming messages render with a blinking cursor and finalize."""

    @pytest.mark.asyncio
    async def test_streaming_message_updates_and_finalizes(self):
        from textual.app import App

        from nexus.tui.widgets import ChatPanel

        class HarnessApp(App):
            def compose(self):
                yield ChatPanel(id="chat-panel")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one(ChatPanel)
            msg = ChatMessage(role=MessageRole.ASSISTANT, content="hel", streaming=True)
            panel.add_message(msg)
            await pilot.pause()
            assert panel._streaming_widget is not None
            before = str(panel._streaming_widget._content_text())
            assert "▌" in before

            panel._tick_cursor()
            after = str(panel._streaming_widget._content_text())
            assert "▌" not in after  # blink toggled off

            panel.end_streaming(final_content="hello world")
            await pilot.pause()
            assert panel._streaming_widget is None
            assert msg.streaming is False
            assert msg.content == "hello world"


class TestWelcomeCard:
    """Boot card: ASCII logo, version, quick tips."""

    @pytest.mark.asyncio
    async def test_welcome_card_content(self):
        from textual.app import App

        card = WelcomeCard(version="9.9.9")

        class HarnessApp(App):
            def compose(self):
                yield card

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            texts = [str(c.render()) for c in card.children]
            joined = "\n".join(texts)
            assert "███╗" in joined  # ASCII logo art is present
            assert "9.9.9" in joined
            assert "/help" in joined
            assert "ctrl+t" in joined
            assert "ctrl+g" in joined
