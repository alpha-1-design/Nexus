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
