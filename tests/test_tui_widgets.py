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
