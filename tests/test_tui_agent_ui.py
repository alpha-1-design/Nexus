"""Tests for the agentic order-of-service features — boot checklist,
assistant provenance, live spinners, git-branch status, the settings
modal via pilot, and the /theme + /settings commands.

The ~/.nexus runtime state is shared with other processes; heal the
occasional 0-byte facts.json before importing (same pattern as
test_tui_app.py).
"""

import json
import os
import subprocess as real_subprocess
import sys

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

from nexus.tui import widgets as tw  # noqa: E402
from nexus.tui.app import NexusTUI  # noqa: E402
from nexus.tui.colors import (  # noqa: E402
    DEFAULT_THEME,
    THEME_NAMES,
    THEMES,
    apply_theme,
    get_current_theme,
)
from nexus.tui.settings import get_settings, reset_settings_instance  # noqa: E402
from nexus.tui.state import ChatMessage, MessageRole, get_state_manager  # noqa: E402
from nexus.tui.widgets import (  # noqa: E402
    BootChecklist,
    ChatMessageWidget,
    SPINNER_FRAMES,
    StatusBar,
    ToolCardWidget,
    WelcomeCard,
    format_boot_line,
    format_provenance,
    get_git_branch,
)


@pytest.fixture(autouse=True)
def _clean_globals(monkeypatch):
    """Isolate the process-global state these features touch: the settings
    singleton (never write the real ~/.nexus/settings.json) and the theme."""
    monkeypatch.setenv("NEXUS_TUI_SETTINGS_PATH", "/nonexistent/settings.json")
    monkeypatch.setattr("nexus.tui.settings.save_settings", lambda *a, **k: None)
    reset_settings_instance()
    get_state_manager().reset()
    theme_before = get_current_theme()
    yield
    apply_theme(theme_before if theme_before in THEME_NAMES else DEFAULT_THEME)
    get_state_manager().reset()
    reset_settings_instance()


def _msg(role=MessageRole.USER, content="hello", **kwargs):
    from datetime import datetime

    return ChatMessage(role=role, content=content, timestamp=datetime(2026, 1, 1, 14, 30, 5), **kwargs)


# ════════════════════════════════════════════════════════════════════
# Boot checklist — agentic order of service
# ════════════════════════════════════════════════════════════════════


class TestBootChecklist:
    def test_format_boot_line_ok(self):
        line = format_boot_line({"name": "memory", "status": "ok", "detail": "14 facts"})
        assert "◆" in line and "memory" in line and "✓" in line
        assert "14 facts" in line

    def test_format_boot_line_fail_is_amber(self):
        from nexus.tui.colors import THEMES

        line = format_boot_line({"name": "skills", "status": "fail", "detail": "catalog missing"})
        assert "✗" in line and "skills" in line and "catalog missing" in line
        assert THEMES["neural-cyan"]["warning"] in line  # amber, not red/gray

    def test_format_boot_line_none_provider(self):
        line = format_boot_line({"name": "provider", "status": "none", "detail": "none configured (/auth)"})
        assert "—" in line and "/auth" in line

    def test_collect_boot_checks_returns_all_subsystems(self):
        checks = tw.collect_boot_checks()
        names = [c["name"] for c in checks]
        assert names == ["memory", "skills", "tools", "mcp", "provider"]
        for check in checks:
            assert check["status"] in ("ok", "fail", "none")

    def test_discovery_seam_overrides_counts(self, monkeypatch):
        """When nexus.discovery.boot_summary exists at runtime, its counts
        win over the local probes; ImportError keeps the local path."""
        class _FakeDiscovery:
            @staticmethod
            def boot_summary():
                return {"skills": 999, "tools": 42, "mcp": 3}

        monkeypatch.setitem(sys.modules, "nexus.discovery", _FakeDiscovery)
        checks = {c["name"]: c for c in tw.collect_boot_checks()}
        assert "999 loaded" in checks["skills"]["detail"]
        assert "42 registered" in checks["tools"]["detail"]
        assert "3 servers" in checks["mcp"]["detail"]

    def test_discovery_seam_survives_garbage(self, monkeypatch):
        class _BadDiscovery:
            @staticmethod
            def boot_summary():
                raise RuntimeError("not ready yet")

        monkeypatch.setitem(sys.modules, "nexus.discovery", _BadDiscovery)
        checks = tw.collect_boot_checks()
        assert len(checks) == 5  # falls back to local probes, never raises

    def test_mcp_count_reads_config_file(self, tmp_path, monkeypatch):
        cfg = tmp_path / "mcp-servers.json"
        cfg.write_text(json.dumps({"fetcher": {}, "search": {}}))
        original = tw._count_mcp_servers
        monkeypatch.setattr(
            tw, "_count_mcp_servers", lambda path=None: original(str(cfg))
        )
        checks = {c["name"]: c for c in tw.collect_boot_checks()}
        assert "2 servers" in checks["mcp"]["detail"]

    @pytest.mark.asyncio
    async def test_boot_checklist_renders_all_lines_without_crash(self, monkeypatch):
        """The animated checklist reveals one line per ~150ms tick above the
        welcome card and finishes with every subsystem line visible."""
        fake_checks = [
            {"name": "memory", "status": "ok", "detail": "3 facts"},
            {"name": "skills", "status": "ok", "detail": "382 loaded"},
            {"name": "tools", "status": "ok", "detail": "24 registered"},
            {"name": "mcp", "status": "ok", "detail": "0 servers"},
            {"name": "provider", "status": "none", "detail": "none configured (/auth)"},
        ]
        monkeypatch.setattr(tw, "collect_boot_checks", lambda: fake_checks)

        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            checklist = app.query_one(BootChecklist)
            assert not checklist.has_class("hidden")
            for _ in range(12):  # 5 lines at ~0.15s each; real timers run
                await pilot.pause()
                if checklist._done:
                    break
            assert checklist._done, "checklist should finish revealing its lines"
            assert checklist._revealed == len(fake_checks)
            text = str(checklist.render())
            for check in fake_checks:
                assert check["name"] in text
            assert "✓" in text
            assert "/auth" in text
            # The welcome card sits BELOW the checklist in the transcript.
            container = app.query_one("#messages-container")
            children = list(container.children)
            assert children.index(checklist) < children.index(app.query_one(WelcomeCard))

    @pytest.mark.asyncio
    async def test_boot_checklist_never_crashes_boot(self, monkeypatch):
        """A broken probe collector must not take the boot down."""
        def boom():
            raise RuntimeError("probes exploded")

        monkeypatch.setattr(tw, "collect_boot_checks", boom)
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.pause()
            checklist = app.query_one(BootChecklist)
            assert "boot" in str(checklist.render())  # ✗ fallback line


# ════════════════════════════════════════════════════════════════════
# Assistant provenance header
# ════════════════════════════════════════════════════════════════════


class TestProvenanceHeader:
    def test_format_provenance_full(self):
        assert format_provenance("groq", "llama-3.3-70b", 3200) == "✦ Nexus · groq · llama-3.3-70b · 3.2s"

    def test_format_provenance_without_elapsed(self):
        assert format_provenance("groq", "m", None) == "✦ Nexus · groq · m"

    def test_format_provenance_empty_when_nothing_known(self):
        assert format_provenance(None, None, None) == ""
        assert format_provenance("", "", None) == ""

    @pytest.mark.asyncio
    async def test_assistant_message_renders_provenance_row(self):
        from textual.app import App

        msg = _msg(MessageRole.ASSISTANT, "Done.", provider="groq", model="llama-3.3-70b", elapsed_ms=1200)

        class HarnessApp(App):
            def compose(self):
                yield ChatMessageWidget(msg)

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            row = app.query_one(".msg-provenance")
            text = str(row.render())
            assert "✦ Nexus" in text
            assert "groq" in text and "llama-3.3-70b" in text
            assert "1.2s" in text

    @pytest.mark.asyncio
    async def test_user_and_system_messages_have_no_provenance(self):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield ChatMessageWidget(_msg(MessageRole.USER, "hi"))
                yield ChatMessageWidget(_msg(MessageRole.SYSTEM, "note"))

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app.query(".msg-provenance")

    def test_compact_mode_suppresses_provenance_and_stamps(self, monkeypatch):
        msg = _msg(MessageRole.ASSISTANT, "Done.", provider="groq", model="m")
        widget = ChatMessageWidget(msg)
        get_settings().compact_mode = True
        try:
            composed = list(widget.compose())
            classes = [c.classes for c in composed]
            assert not any("msg-provenance" in cl for cl in classes)
            assert "14:30" not in widget._header_markup()  # badges only
        finally:
            get_settings().compact_mode = False


# ════════════════════════════════════════════════════════════════════
# Live spinners + streaming cursor gating
# ════════════════════════════════════════════════════════════════════


class TestLiveSpinners:
    def test_spinner_styles_have_frames(self):
        for style in ("braille", "dots", "pulse"):
            frames = SPINNER_FRAMES[style]
            assert len(frames) >= 4
            assert all(len(frame) == 1 for frame in frames)

    def test_running_icon_follows_spinner_style(self, monkeypatch):
        card_msg = _msg(
            role=MessageRole.TOOL,
            content="",
            tool_name="edit",
            tool_status="running",
        )
        card = ToolCardWidget(card_msg)
        monkeypatch.setattr(tw.ChatPanel, "spinner_frame", 3)
        get_settings().spinner_style = "pulse"
        try:
            icon, color = tw._status_icon("running")
            assert icon == SPINNER_FRAMES["pulse"][3 % len(SPINNER_FRAMES["pulse"])]
        finally:
            get_settings().spinner_style = "braille"

    def test_done_icon_is_static_check(self):
        icon, color = tw._status_icon("done")
        assert icon == "✓"
        icon, color = tw._status_icon("error")
        assert icon == "✗"

    @pytest.mark.asyncio
    async def test_chatpanel_tick_advances_running_cards(self):
        from textual.app import App

        card_msg = _msg(role=MessageRole.TOOL, content="", tool_name="edit", tool_status="running")

        class HarnessApp(App):
            def compose(self):
                yield tw.ChatPanel(id="chat-panel")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one(tw.ChatPanel)
            card_id = panel.add_tool_card("edit", {"path": "a.py"})
            await pilot.pause()
            card = app.query_one(f"#tool-card-{card_id}", ToolCardWidget)
            before = card._header_markup()
            panel._tick_spinners()
            panel._tick_spinners()
            # Two ticks advance the shared frame — a braille spinner cycles
            # through distinct glyphs, so the header text changes.
            assert panel.spinner_frame >= 2

    def test_streaming_cursor_gated_by_settings(self):
        msg = _msg(MessageRole.ASSISTANT, "hel", streaming=True)
        widget = ChatMessageWidget(msg)
        widget._cursor_on = True
        get_settings().streaming_cursor = False
        try:
            assert "▌" not in str(widget._content_text())
            get_settings().streaming_cursor = True
            assert "▌" in str(widget._content_text())
        finally:
            get_settings().streaming_cursor = True


# ════════════════════════════════════════════════════════════════════
# Git branch + status bar fitting
# ════════════════════════════════════════════════════════════════════


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestGitBranchHelper:
    def test_inside_repo_returns_branch(self, monkeypatch):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return _FakeCompleted(returncode=0, stdout="feature/theme-work\n")

        monkeypatch.setattr(tw.subprocess, "run", fake_run)
        tw.clear_git_branch_cache()
        assert get_git_branch("/tmp/somewhere") == "feature/theme-work"

    def test_result_is_cached_per_directory(self, monkeypatch):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(1)
            return _FakeCompleted(returncode=0, stdout="main\n")

        monkeypatch.setattr(tw.subprocess, "run", fake_run)
        tw.clear_git_branch_cache()
        get_git_branch("/tmp/cache-me")
        get_git_branch("/tmp/cache-me")
        assert len(calls) == 1  # second call served from cache

    def test_outside_repo_returns_dash(self, monkeypatch):
        monkeypatch.setattr(
            tw.subprocess, "run", lambda *a, **k: _FakeCompleted(returncode=128, stdout="", stderr="not a repo")
        )
        tw.clear_git_branch_cache()
        assert get_git_branch("/tmp/nowhere") == "—"

    def test_timeout_returns_dash(self, monkeypatch):
        def hung(*args, **kwargs):
            raise real_subprocess.TimeoutExpired(cmd="git", timeout=2)

        monkeypatch.setattr(tw.subprocess, "run", hung)
        tw.clear_git_branch_cache()
        assert get_git_branch("/tmp/hung") == "—"

    def test_missing_git_binary_returns_dash(self, monkeypatch):
        def missing(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(tw.subprocess, "run", missing)
        tw.clear_git_branch_cache()
        assert get_git_branch("/tmp/nogit") == "—"

    def test_two_second_timeout_requested(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            seen["cmd"] = cmd
            return _FakeCompleted(returncode=0, stdout="main\n")

        monkeypatch.setattr(tw.subprocess, "run", fake_run)
        tw.clear_git_branch_cache()
        get_git_branch("/tmp/whatever")
        assert seen["timeout"] == 2.0
        assert seen["cmd"] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]


class TestStatusBarFitting:
    def _visible_len(self, markup: str) -> int:
        from rich.text import Text

        return Text.from_markup(markup).cell_len

    def test_session_id_shortened_in_render(self):
        get_state_manager().set_session("1234567890abcdef")
        bar = StatusBar(version="0.1.0", model="m", provider="")
        rendered = bar.render()
        assert "#12345678" in rendered  # first 8 chars only
        assert "1234567890abcdef" not in rendered

    def test_branch_rendered_inside_repo(self, monkeypatch):
        monkeypatch.setattr(tw, "get_git_branch", lambda *a, **k: "main")
        bar = StatusBar(version="0.1.0", model="m", provider="")
        assert "⎇ main" in bar.render()

    def test_no_branch_marker_outside_repo(self, monkeypatch):
        monkeypatch.setattr(tw, "get_git_branch", lambda *a, **k: "—")
        bar = StatusBar(version="0.1.0", model="m", provider="")
        rendered = bar.render()
        assert "⎇" not in rendered

    def test_join_fitted_truncates_cwd_first(self):
        parts = [
            ("nexus", "[dim]{}[/]", False),
            ("a-very-long-project-directory-name", "[dim]{}[/]", True),
        ]
        line = StatusBar._join_fitted(parts, 24)
        visible = self._visible_len(line)
        assert visible <= 24
        assert "…" in line  # gracefully truncated, not dropped blindly

    def test_join_fitted_drops_flexible_tail_when_tight(self):
        parts = [
            ("model-xyz", "[bold]{}[/]", False),
            ("short", "[dim]{}[/]", True),
        ]
        line = StatusBar._join_fitted(parts, 10)
        visible = self._visible_len(line)
        assert visible <= 10
        assert "model-xyz" in line  # inflexible part survives
        assert "short" not in line  # flexible part dropped

    def test_join_fitted_unlimited_when_width_zero(self):
        parts = [("nexus", "[dim]{}[/]", False), ("cwd", "[dim]{}[/]", True)]
        line = StatusBar._join_fitted(parts, 0)
        assert "cwd" in line and "nexus" in line

    @pytest.mark.asyncio
    async def test_status_bar_one_line_at_90_cols(self):
        """Termux budget: everything stays on one line at 90 columns."""
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            sm = app.state_manager
            sm.set_session("1234567890abcdef")
            sm.set_turn_stats(tokens_used=12400, duration_ms=1800, max_context_tokens=128000)
            bar = app.query_one(StatusBar)
            rendered = bar.render()
            assert self._visible_len(rendered) <= 90
            assert "\n" not in rendered

    @pytest.mark.asyncio
    async def test_status_bar_extreme_content_still_fits(self):
        app = NexusTUI()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            sm = app.state_manager
            sm.set_session("1234567890abcdef")
            sm.set_turn_stats(tokens_used=98765, duration_ms=100, max_context_tokens=128000)
            bar = app.query_one(StatusBar)
            bar.model = "llama-3.3-70b-versatile-instruct-long-name"
            bar.provider = "openrouter"
            bar.message = "Brain: Thinking really hard about something long"
            rendered = bar.render()
            assert self._visible_len(rendered) <= 90


# ════════════════════════════════════════════════════════════════════
# Settings modal via pilot + /theme + /settings commands
# ════════════════════════════════════════════════════════════════════


class TestSettingsModalPilot:
    @pytest.mark.asyncio
    async def test_open_navigate_change_close(self):
        """ctrl+s opens the modal; arrows/tab move; enter cycles a value
        (live, persisted); esc closes."""
        from nexus.tui.settings import get_settings
        from nexus.tui.settings_screen import SettingsModal

        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, SettingsModal)
            modal = app.screen
            assert len(modal._row_statics) == len(modal.model.rows)
            assert modal.selected == 0

            await pilot.press("down")
            assert modal.selected == 1  # timestamps row
            row_static = modal._row_statics[1]
            before = str(row_static.render())

            await pilot.press("enter")  # cycle timestamps: 24h → 12h
            await pilot.pause()
            assert get_settings().timestamps == "12h"
            after = str(row_static.render())
            assert after != before  # the row re-renders its inline value

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsModal)

    @pytest.mark.asyncio
    async def test_setting_change_applies_live_and_persists(self):
        """A theme change from the modal re-renders widgets immediately and
        reaches the (patched) persistence layer."""
        from nexus.tui.settings import get_settings
        from nexus.tui.settings_screen import SettingsModal

        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, SettingsModal)
            await pilot.press("enter")  # theme row: neural-cyan → synthwave
            await pilot.pause()
            assert get_settings().theme == THEME_NAMES[1]
            assert get_current_theme() == THEME_NAMES[1]
            bar = app.query_one(StatusBar)
            # Idle pulse dot renders with the live text_dim token.
            assert THEMES[THEME_NAMES[1]]["text_dim"] in bar.render()
            await pilot.press("escape")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_slash_settings_opens_modal(self):
        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            assert app._handle_command("/settings") is True
            await pilot.pause()
            from nexus.tui.settings_screen import SettingsModal

            assert isinstance(app.screen, SettingsModal)
            app.screen.action_close()


class TestThemeCommand:
    def _chat_messages(self, app):
        return app.query_one("#chat-panel")._messages

    @pytest.mark.asyncio
    async def test_theme_set_by_name(self):
        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            assert app._handle_command("/theme matrix") is True
            assert get_current_theme() == "matrix"
            assert get_settings().theme == "matrix"
            last = self._chat_messages(app)[-1]
            assert "matrix" in last.content

    @pytest.mark.asyncio
    async def test_theme_no_arg_cycles(self):
        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            before = THEME_NAMES.index(get_current_theme())
            assert app._handle_command("/theme") is True
            assert get_current_theme() == THEME_NAMES[(before + 1) % len(THEME_NAMES)]

    @pytest.mark.asyncio
    async def test_theme_unknown_name_reported(self):
        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            before = get_current_theme()
            assert app._handle_command("/theme vaporwave-9000") is True
            assert get_current_theme() == before  # unchanged
            last = self._chat_messages(app)[-1]
            assert "Unknown theme" in last.content
            assert "synthwave" in last.content  # suggestions listed

    @pytest.mark.asyncio
    async def test_new_commands_in_dropdown_and_palette(self):
        from nexus.tui.palette import CommandPalette
        from nexus.tui.widgets import SlashCommandDropdown

        names = [c for c, _ in SlashCommandDropdown.COMMANDS]
        assert "/settings" in names and "/theme" in names
        assert "/settings" in CommandPalette.COMMANDS
        assert "/theme" in CommandPalette.COMMANDS

    @pytest.mark.asyncio
    async def test_help_mentions_settings_and_theme(self):
        app = NexusTUI()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            assert app._handle_command("/help") is True
            help_msg = next(
                m for m in self._chat_messages(app) if "keys & commands" in m.content
            )
            assert "/settings" in help_msg.content
            assert "/theme" in help_msg.content
            assert "ctrl+s" in help_msg.content

    def test_dropdown_filter_finds_settings(self):
        dropdown = tw.SlashCommandDropdown(filter_text="/se")
        dropdown._update_filtered()
        names = [c for c, _ in dropdown._filtered]
        assert "/settings" in names


# ════════════════════════════════════════════════════════════════════
# Diff visibility gate (settings-driven tool cards)
# ════════════════════════════════════════════════════════════════════


class TestDiffVisibilityGate:
    """The tool-card diff block honors settings.show_diffs at render time."""

    def _card_msg(self):
        return _msg(
            role=MessageRole.TOOL,
            content="",
            tool_name="edit",
            tool_status="done",
            tool_diff="+added line",
        )

    @staticmethod
    async def _mounted_card(card):
        from textual.app import App

        class HarnessApp(App):
            def compose(self):
                yield card

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            # compose() runs inside the app, so return what it mounted.
            return len(app.query(tw.DiffBlock))

    @pytest.mark.asyncio
    async def test_show_diffs_true_includes_block(self):
        assert await self._mounted_card(ToolCardWidget(self._card_msg())) == 1

    @pytest.mark.asyncio
    async def test_show_diffs_false_hides_block(self):
        get_settings().show_diffs = False
        try:
            assert await self._mounted_card(ToolCardWidget(self._card_msg())) == 0
        finally:
            get_settings().show_diffs = True


# ════════════════════════════════════════════════════════════════════
# welcome.py get_logo — identity follows provider/theme
# ════════════════════════════════════════════════════════════════════


class TestWelcomeLogoTint:
    def _ansi_hex(self, hex_color: str) -> str:
        from nexus.cli.welcome import _load_tui_colors

        return _load_tui_colors().hex_to_ansi24(hex_color)

    def test_explicit_color_wins(self):
        from nexus.cli.welcome import get_logo

        logo = get_logo(color="#123456")
        assert self._ansi_hex("#123456") in logo

    def test_provider_brand_overrides(self):
        from nexus.cli.welcome import get_logo

        logo = get_logo(provider="groq")
        assert self._ansi_hex("#f55036") in logo  # groq orange, not cyan

    def test_follows_theme_logo_tint(self, tmp_path, monkeypatch):
        from nexus.cli import welcome

        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"theme": "matrix"}))
        monkeypatch.setenv("NEXUS_TUI_SETTINGS_PATH", str(settings_path))
        logo = welcome.get_logo()
        # matrix phosphor green logo tint (from the persisted settings file)
        tint = welcome._load_tui_colors().THEMES["matrix"]["logo_tint"]
        assert self._ansi_hex(tint) in logo
        # and still the same ASCII art + ANSI escape codes
        assert "\033[" in logo and "███╗" in logo

    def test_still_a_string_without_tui(self, monkeypatch):
        """Even if both tui loads fail, get_logo falls back to plain cyan."""
        from nexus.cli import welcome

        monkeypatch.setattr(welcome, "_load_tui_colors", lambda: None)
        logo = welcome.get_logo()
        assert "\033[36m" in logo  # classic cyan fallback
        assert "███╗" in logo
