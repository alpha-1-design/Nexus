"""Tests for the finishing seams: /plugins + /discover (REPL + TUI), the
REPL destructive-bash approval prompt, and the Heartbeat/Telemetry end state.

Heartbeat/Telemetry decision (documented, see test below): neither class
exists in nexus/tui/ — the M6 purge already removed them. The live status-bar
pulse IS the heartbeat; it is now gated by the sidebar_animations setting
instead of animating unconditionally. Telemetry has no stub left to delete
(SystemMonitor already surfaces load/RAM/provider/activity).
"""

import re
from pathlib import Path

import pytest

import nexus.cli.repl as repl_module
from nexus.cli.repl import (
    REPL,
    _ApprovedBash,
    format_discover_report,
    format_plugins_report,
)
from nexus.discovery import EnvironmentProfile
from nexus.plugins import PluginRecord
from nexus.providers import Response, ToolCall
from nexus.tools import ToolResult

# ---------------------------------------------------------------------------
# Fakes (same pattern as test_repl.py: no network, no disk writes)
# ---------------------------------------------------------------------------


class _FakeTool:
    """Tool stub echoing arguments; optionally returns canned results."""

    name = "echo_tool"
    description = "Echoes arguments for tests"

    def __init__(self, results: list[ToolResult] | None = None):
        self.calls: list[dict] = []
        self._results = list(results or [])

    async def execute(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return ToolResult(success=True, content=f"ok:{kwargs.get('path', '')}")


class _FakeRegistry:
    def __init__(self, tool: _FakeTool):
        self.tool = tool

    def to_openai_format(self):
        return [{"type": "function", "function": {"name": self.tool.name, "parameters": {}}}]

    def get(self, name):
        return self.tool if name == self.tool.name else None

    def list_all(self):
        return [self.tool]


class _FakeManager:
    """Scripted provider manager (complete turns only)."""

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.active_provider = "fake"

    async def stream(self, messages, tools=None, **kwargs):
        for chunk in self.turns.pop(0):
            yield chunk

    async def complete(self, messages, tools=None, **kwargs):
        return self.turns.pop(0)

    async def switch_model(self, model, provider_name=None):
        pass

    def get_provider_config(self, name):
        return None


def _make_repl(tool: _FakeTool | None = None) -> REPL:
    """Build a REPL wired to fakes, mirroring tests/test_repl.py."""
    repl = REPL()
    repl.team = None  # skip auto-spawn heuristics
    repl.registry = _FakeRegistry(tool or _FakeTool())
    repl.manager = _FakeManager([Response(content="done", model="fake")])
    repl.memory.save_session = lambda session: None  # keep tests off disk
    repl._get_system_prompt = lambda: "SYSTEM"  # skip indexer/memory lookups
    repl._check_tool_safety = lambda name, args: (True, None)  # skip visual scan
    repl._show_context_pulse = lambda: None
    return repl


class _RecordingApprovedBash:
    """Stands in for _ApprovedBash: records the approval flag + execute calls."""

    calls: list[dict] = []

    def __init__(self, approved: bool = False):
        self.approved = bool(approved)

    async def execute(self, **kwargs) -> ToolResult:
        _RecordingApprovedBash.calls.append({"approved": self.approved, **kwargs})
        return ToolResult(success=True, content="APPROVED-OUTPUT")


# ---------------------------------------------------------------------------
# /plugins + /discover: REPL handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repl_plugins_good_and_broken_rows(capsys, monkeypatch):
    """Loaded plugins render ● cards with tool counts; failed ones get an
    error: row and are never silent."""
    records = [
        PluginRecord(
            name="uptime",
            version="1.0.0",
            tools=["plugin_uptime_uptime_check"],
            status="loaded",
            warnings=["manifest declares no tools"][:0],  # none
        ),
        PluginRecord(
            name="broken",
            version="",
            status="failed",
            kind="invalid",
            error="import failed: boom at import",
        ),
    ]
    monkeypatch.setattr("nexus.plugins.list_plugins", lambda: records)
    repl = _make_repl()

    handled = await repl._handle_command("/plugins")

    out = capsys.readouterr().out
    assert handled
    assert "● uptime v1.0.0 — 1 tool(s)" in out
    assert "plugin_uptime_uptime_check" in out
    assert "✗ broken — error: import failed: boom at import" in out


@pytest.mark.asyncio
async def test_repl_plugins_empty_dir(capsys, monkeypatch):
    monkeypatch.setattr("nexus.plugins.list_plugins", lambda: [])
    repl = _make_repl()

    await repl._handle_command("/plugins")

    assert "No plugins found" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_repl_plugins_reload_flag_rescans(capsys, monkeypatch):
    """--reload must go through reload_plugins, not the cached list."""
    reloaded: list[bool] = []
    monkeypatch.setattr("nexus.plugins.list_plugins", lambda: [])
    monkeypatch.setattr("nexus.plugins.reload_plugins", lambda: reloaded.append(True) or [])
    repl = _make_repl()

    await repl._handle_command("/plugins --reload")

    assert reloaded == [True]
    assert "No plugins found" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_repl_discover_environment_and_project_sniff(capsys, monkeypatch):
    """/discover renders platform, capability presence/absence and the
    project sniff from the discovery module."""
    profile = EnvironmentProfile(
        platform="termux",
        platform_detail="Termux 0.118",
        python_version="3.12.3",
        shell="/data/data/com.termux/files/usr/bin/bash",
        capabilities={"textual": True, "rich": True, "playwright": False},
        tools={"git": "/usr/bin/git", "gh": None},
    )
    project = {
        "path": "/workspace",
        "languages": [{"language": "python", "files": 120}],
        "manifest_count": 2,
        "has_nexus_md": True,
        "git_branch": "main",
        "is_git": True,
    }
    monkeypatch.setattr("nexus.discovery.discover_environment", lambda force=False: profile)
    monkeypatch.setattr("nexus.discovery.familiarize_project", lambda ws=None: project)
    repl = _make_repl()

    handled = await repl._handle_command("/discover")

    out = capsys.readouterr().out
    assert handled
    assert "platform: termux — Termux 0.118" in out
    assert "capabilities present: rich, textual" in out
    assert "capabilities missing: playwright" in out
    assert "tools missing: gh" in out
    assert "python (120 files)" in out
    assert "manifests: 2 · NEXUS.md: yes · git: main" in out


def test_repl_slash_completion_list_new_commands():
    """Tab completion must surface /plugins and /discover."""
    repl = _make_repl()
    repl._setup_readline()
    assert "/plugins" in repl._slash_commands
    assert "/discover" in repl._slash_commands


@pytest.mark.asyncio
async def test_repl_help_mentions_new_commands(capsys):
    repl = _make_repl()
    await repl._handle_command("/help")
    out = capsys.readouterr().out
    assert "/plugins [--reload]" in out
    assert "/discover" in out


# ---------------------------------------------------------------------------
# /plugins + /discover: TUI dropdown, palette and handler
# ---------------------------------------------------------------------------


def test_slash_dropdown_contains_new_commands():
    from nexus.tui.widgets import SlashCommandDropdown

    cmds = [cmd for cmd, _ in SlashCommandDropdown.COMMANDS]
    assert "/plugins" in cmds
    assert "/discover" in cmds
    descriptions = dict(SlashCommandDropdown.COMMANDS)
    assert descriptions["/plugins"]
    assert descriptions["/discover"]


def test_command_palette_contains_new_commands():
    from nexus.tui.palette import CommandPalette

    assert "/plugins" in CommandPalette.COMMANDS
    assert "/discover" in CommandPalette.COMMANDS


@pytest.mark.asyncio
async def test_tui_handle_command_plugins_renders_system_card(monkeypatch):
    """The TUI routes /plugins through its native command handler to the
    same system-message rendering the REPL card uses."""
    from nexus.tui.app import NexusTUI
    from nexus.tui.state import MessageRole, get_state_manager

    get_state_manager().reset()
    records = [
        PluginRecord(name="uptime", version="1.0.0", tools=["plugin_uptime_check"], status="loaded"),
        PluginRecord(name="broken", status="failed", kind="invalid", error="import failed: boom"),
    ]
    monkeypatch.setattr("nexus.plugins.list_plugins", lambda: records)

    app = NexusTUI()
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        assert app._handle_command("/plugins")
        await pilot.pause()

        chat = app.query_one("#chat-panel")
        system_texts = [m.content for m in chat._messages if m.role == MessageRole.SYSTEM]
        assert any("uptime v1.0.0 — 1 tool(s)" in t for t in system_texts)
        assert any("error: import failed: boom" in t for t in system_texts)


@pytest.mark.asyncio
async def test_tui_handle_command_discover_renders_system_card(monkeypatch):
    from nexus.tui.app import NexusTUI
    from nexus.tui.state import MessageRole, get_state_manager

    get_state_manager().reset()
    profile = EnvironmentProfile(platform="linux", python_version="3.12.3")
    project = {"path": "/w", "languages": [], "manifest_count": 1, "has_nexus_md": False,
               "git_branch": None, "is_git": False}
    monkeypatch.setattr("nexus.discovery.discover_environment", lambda force=False: profile)
    monkeypatch.setattr("nexus.discovery.familiarize_project", lambda ws=None: project)

    app = NexusTUI()
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        assert app._handle_command("/discover")
        await pilot.pause()

        chat = app.query_one("#chat-panel")
        system_texts = [m.content for m in chat._messages if m.role == MessageRole.SYSTEM]
        assert any("platform: linux" in t and "Project:" in t for t in system_texts)


# ---------------------------------------------------------------------------
# Shared card renderers (unit edges)
# ---------------------------------------------------------------------------


def test_format_plugins_report_empty_and_warnings():
    assert format_plugins_report([]) == "No plugins found."
    rec = PluginRecord(name="reg", kind="register", warnings=["register() plugin: name your tools"])
    text = format_plugins_report([rec])
    assert "● reg" in text and "0 tool(s)" in text
    assert "⚠ register() plugin" in text


def test_format_discover_report_minimal_profile():
    text = format_discover_report(
        EnvironmentProfile(platform="docker"), {"path": "/x", "languages": [], "manifest_count": 0}
    )
    assert "platform: docker" in text
    assert "capabilities: none probed" in text
    assert "languages: none detected" in text


# ---------------------------------------------------------------------------
# Destructive-bash approval prompt
# ---------------------------------------------------------------------------


_APPROVAL_META = {
    "action": "require_approval",
    "path": "",
    "command": "shutdown -h now",
    "requires_permission": True,
    "approval_reason": "system power control",
}


@pytest.mark.asyncio
async def test_approval_prompt_yes_reexecutes_once_with_override(monkeypatch):
    """y → the command re-executes exactly once through the explicit
    override path (_ApprovedBash with approved=True), and the transcript
    commits the re-run output."""
    bash = _FakeTool(results=[
        ToolResult(success=True, content="APPROVAL_REQUIRED", metadata=dict(_APPROVAL_META)),
    ])
    bash.name = "bash"
    repl = _make_repl(bash)
    repl.streaming = False
    repl.manager = _FakeManager([
        Response(content="", model="fake", tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "shutdown -h now"}),
        ]),
        Response(content="done", model="fake"),
    ])
    prompts: list[str] = []
    monkeypatch.setattr(
        REPL, "_ask_yes_no", lambda self, prompt: prompts.append(prompt) or True
    )
    monkeypatch.setattr(repl_module, "_ApprovedBash", _RecordingApprovedBash)
    _RecordingApprovedBash.calls = []

    result = await repl._generate_response("reboot the box", print_result=False)

    assert result == "done"
    assert len(prompts) == 1
    assert prompts[0] == "⚠ approval required: system power control — run? [y/N] "
    # The real bash tool was called exactly once (the flagged attempt) and
    # the approved re-run went through the override path with the flag set.
    assert len(bash.calls) == 1
    assert _RecordingApprovedBash.calls == [{"approved": True, "command": "shutdown -h now"}]
    tool_msgs = [m for m in repl.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "APPROVED-OUTPUT"


@pytest.mark.asyncio
async def test_approval_prompt_no_stays_blocked(monkeypatch):
    """N (or anything but y) → the command never executes and the tool
    message says it was declined."""
    bash = _FakeTool(results=[
        ToolResult(success=True, content="APPROVAL_REQUIRED", metadata=dict(_APPROVAL_META)),
    ])
    bash.name = "bash"
    repl = _make_repl(bash)
    repl.streaming = False
    repl.manager = _FakeManager([
        Response(content="", model="fake", tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "shutdown -h now"}),
        ]),
        Response(content="declined then", model="fake"),
    ])
    monkeypatch.setattr(REPL, "_ask_yes_no", lambda self, prompt: False)
    monkeypatch.setattr(repl_module, "_ApprovedBash", _RecordingApprovedBash)
    _RecordingApprovedBash.calls = []

    await repl._generate_response("reboot the box", print_result=False)

    assert _RecordingApprovedBash.calls == []  # never re-executed
    # The failure-hint pass rewrites the content to "Error: <reason>" — the
    # committed tool message still carries the decline, never the output.
    tool_msgs = [m for m in repl.messages if m.role == "tool"]
    assert "user declined approval" in tool_msgs[0].content
    assert "APPROVED-OUTPUT" not in tool_msgs[0].content


@pytest.mark.asyncio
async def test_no_metadata_no_prompt(monkeypatch):
    """A normal (non-flagged) tool result must never trigger the prompt."""
    repl = _make_repl(_FakeTool())
    repl.streaming = False
    repl.manager = _FakeManager([
        Response(content="", model="fake", tool_calls=[
            ToolCall(id="c1", name="echo_tool", arguments={"path": "x.py"}),
        ]),
        Response(content="done", model="fake"),
    ])

    def _no_prompt(self, prompt):
        raise AssertionError("approval prompt shown without require_approval metadata")

    monkeypatch.setattr(REPL, "_ask_yes_no", _no_prompt)

    result = await repl._generate_response("echo the file", print_result=False)

    assert result == "done"


@pytest.mark.asyncio
async def test_require_approval_on_non_bash_tools_is_out_of_scope(monkeypatch):
    """The REPL gate is bash-only: the diff approval contract (edit/write +
    orchestrator pending_approval) must not be intercepted here."""
    edit = _FakeTool(results=[
        ToolResult(success=True, content="PROPOSED CHANGE", metadata={
            "action": "require_approval", "path": "a.py", "diff": "--- a\n+++ b",
        }),
    ])
    edit.name = "edit"
    repl = _make_repl(edit)
    repl.streaming = False
    repl.manager = _FakeManager([
        Response(content="", model="fake", tool_calls=[
            ToolCall(id="c1", name="edit", arguments={"path": "a.py"}),
        ]),
        Response(content="done", model="fake"),
    ])

    def _no_prompt(self, prompt):
        raise AssertionError("REPL approval prompt must stay bash-only")

    monkeypatch.setattr(REPL, "_ask_yes_no", _no_prompt)

    result = await repl._generate_response("edit the file", print_result=False)

    assert result == "done"


def test_approved_bash_default_stays_gated():
    """Without the explicit flag the subclass applies the same destructive
    gate as BashTool; with approved=True the gate is skipped for the one
    human-approved re-run."""
    plain = _ApprovedBash()
    assert plain.approved is False
    assert plain._match_destructive("shutdown -h now") == "system power control"

    approved = _ApprovedBash(approved=True)
    assert approved.approved is True
    assert approved._match_destructive("shutdown -h now") is None
    # Non-destructive commands are unaffected either way.
    assert approved._match_destructive("ls -la") is None


def test_ask_yes_no_only_y_approves(monkeypatch):
    repl = _make_repl()
    for answer, expected in (("y", True), ("YES ", True), ("n", False), ("", False), ("ok", False)):
        monkeypatch.setattr("builtins.input", lambda prompt="", a=answer: a)
        assert repl._ask_yes_no("run? [y/N] ") is expected
    # EOF at the prompt declines (never auto-approve).
    def _raise(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", _raise)
    assert repl._ask_yes_no("run? [y/N] ") is False


# ---------------------------------------------------------------------------
# Heartbeat / Telemetry end state
# ---------------------------------------------------------------------------


def test_no_heartbeat_or_telemetry_classes_in_tui():
    """End-state documentation: Heartbeat and Telemetry do not exist in
    nexus/tui/ — the M6 purge removed them and no stub was left behind. The
    status-bar pulse is the heartbeat (now settings-gated, see below) and
    SystemMonitor is the telemetry line; nothing else was resurrected."""
    tui_dir = Path(repl_module.__file__).parent.parent / "tui"
    offenders = []
    for path in sorted(tui_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in ("Heartbeat", "Telemetry"):
            if re.search(rf"class\s+{name}\b", text):
                offenders.append(f"{path.name}: class {name}")
    assert offenders == []


def test_status_bar_pulse_gated_by_sidebar_animations(monkeypatch):
    """The heartbeat glyph animates only when sidebar_animations is on;
    with it off the dot stays solid and no refresh fires (phone battery)."""
    from nexus.tui.settings import get_settings
    from nexus.tui.widgets import StatusBar

    settings = get_settings()
    monkeypatch.setattr(settings, "sidebar_animations", True)
    bar = StatusBar()
    refreshes: list[int] = []
    monkeypatch.setattr(bar, "refresh", lambda: refreshes.append(1))

    bar._pulse()
    assert bar._pulse_state == 1
    assert len(refreshes) == 1

    monkeypatch.setattr(settings, "sidebar_animations", False)
    bar._pulse()
    assert bar._pulse_state == 1  # frozen: no animation ticks
    assert len(refreshes) == 1    # no repaint either
