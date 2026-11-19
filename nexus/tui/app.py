"""Nexus TUI — Professional Terminal User Interface.

Architecture: the singleton orchestrator is the only brain; the TUI observes
it through three channels —
  1. orchestrator.set_ui_callback  → thinking/error events
  2. a recording wrapper around the tool registry → live tool cards + panels
  3. the ThinkingEngine's on_update → live thinking steps
Everything else (streaming tokens, panel layout, status) is driven from the
state manager or directly in _process_input.
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Any, Callable

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input

from ..agent import get_orchestrator
from ..thinking import get_thinking_engine
from .colors import apply_theme, cycle_theme, get_current_theme, on_theme_change, THEME_NAMES
from .settings import get_load_notes, get_settings, save_settings_now
from .settings_screen import SettingsModal
from .state import (
    AgentStatus,
    ChatMessage,
    MessageRole,
    get_state_manager,
)
from .task_wrapper import OrchestrationTask
from .widgets import (
    AgentsPanel,
    BootChecklist,
    ChatMessageWidget,
    ChatPanel,
    CommandEntered,
    InputBar,
    SlashCommandDropdown,
    StatusBar,
    SystemMonitor,
    ThinkingBlock,
    ThinkingPanel,
    ToolPanel,
    WelcomeCard,
)

# Vertical space the chat transcript is guaranteed, by screen height.
# Termux (30 rows) gets a smaller floor than a desktop (38+ rows).
_CHAT_FLOOR_LARGE = 12
_CHAT_FLOOR_SMALL = 8
_SIDE_PANEL_CAP = 12


class ToolCallRecorder:
    """Wraps a ToolRegistry so the TUI can observe every tool execution.

    The orchestrator only ever does `registry.get(name)` -> `tool.execute()`
    (plus list_all/to_openai_format for the model), so a thin proxy that
    wraps `get()` captures the entire live path without touching the
    orchestrator itself. All other attributes delegate to the real registry.
    """

    def __init__(self, registry: Any, on_start: Callable, on_finish: Callable):
        self._registry = registry
        self._on_start = on_start
        self._on_finish = on_finish
        self._seq = 0

    def next_card_id(self) -> str:
        self._seq += 1
        return f"tc{self._seq}"

    def get(self, name: str):
        tool = self._registry.get(name)
        if tool is None:
            return None
        return RecordedTool(tool, self)

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._registry, attr)


class RecordedTool:
    """Per-execution observer around a BaseTool."""

    def __init__(self, tool: Any, recorder: ToolCallRecorder):
        self._tool = tool
        self._recorder = recorder

    @property
    def definition(self):
        return self._tool.definition

    async def execute(self, **kwargs):
        name = self._tool.definition.name
        card_id = self._recorder.next_card_id()
        self._recorder._on_start(name, kwargs, card_id)
        started = time.monotonic()
        try:
            result = await self._tool.execute(**kwargs)
        except Exception as e:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._recorder._on_finish(card_id, name, False, None, str(e), None, duration_ms)
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        diff = None
        try:
            diff = result.metadata.get("diff")
        except Exception:
            pass
        self._recorder._on_finish(card_id, name, result.success, result.content, result.error, diff, duration_ms)
        return result

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._tool, attr)


class NexusTUI(App):
    """Professional Textual interface for Nexus."""

    CSS_PATH = "styles.css"
    TITLE = "Nexus"
    SUB_TITLE = "Neural OS"

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True),
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+l", "clear_screen", "Clear"),
        Binding("ctrl+g", "toggle_thinking", "Thinking"),
        Binding("ctrl+t", "toggle_tools", "Tools"),
        Binding("ctrl+a", "toggle_agents", "Agents"),
        Binding("ctrl+s", "open_settings", "Settings"),
        # Deliberately NOT priority: a priority app binding would swallow Esc
        # while a modal screen (settings) is open, so its own Esc-to-close
        # could never fire. Namespace order (focused → screen → app) already
        # dismisses the slash dropdown when no modal is on top.
        Binding("escape", "dismiss_dropdown", "Dismiss"),
        Binding("f1", "show_help", "Help"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orchestrator = get_orchestrator()
        self.state_manager = get_state_manager()
        self.state_manager.reset()
        # BUGFIX (recon M2-1): the ui_callback used to be wired to the
        # singleton but _process_input built a FRESH orchestrator per message,
        # so panels never received events. The singleton is used everywhere
        # now — both for the callback and for running turns.
        self.orchestrator.set_ui_callback(self._on_orchestrator_event)
        # Live tool observation: wrap the registry the singleton orchestrator
        # holds so start/finish feed the Tools panel and transcript cards.
        self.orchestrator.tools = ToolCallRecorder(
            self.orchestrator.tools,
            self._on_tool_start,
            self._on_tool_finish,
        )
        # ThinkingEngine is a process-global singleton; register ONCE (its
        # on_update appends without dedupe, so registering per turn would
        # duplicate every step).
        self._seen_thinking_steps: set[str] = set()
        get_thinking_engine().on_update(self._on_thinking_event)

        self._command_history: list[str] = []
        self._history_index = -1
        self._termux_mode = os.path.exists("/data/data/com.termux")
        from .. import __version__
        self._version = __version__
        self._show_dropdown = False
        self._stream_msg: ChatMessage | None = None
        self._agent_name = "nexus-core"
        self._panel_recency: dict[str, int] = {}
        self._panel_seq = 0

    # ── orchestrator event channels ─────────────────────────────

    def _on_orchestrator_event(self, event_type: str, data: Any):
        if event_type == "thinking":
            self.state_manager.add_thinking_step(
                data.get("number", 0),
                data.get("description", ""),
                data.get("details", ""),
            )
        elif event_type == "agent_status":
            status = data.get("status")
            if isinstance(status, str):
                try:
                    status = AgentStatus[status.upper()]
                except KeyError:
                    status = AgentStatus.IDLE
            self.state_manager.update_agent_status(
                data.get("name"), status, data.get("task"),
            )
        elif event_type == "error":
            self.state_manager.set_error(str(data))

    def _on_thinking_event(self, event) -> None:
        """ThinkingEngine events: ("start"|"update"|"finish", step)."""
        kind, step = event
        if kind == "start":
            if step.step_id in self._seen_thinking_steps:
                return
            self._seen_thinking_steps.add(step.step_id)
            self.state_manager.add_thinking_step(
                step_number=len(self._seen_thinking_steps),
                description=step.title,
                details=step.detail or "",
                step_id=step.step_id,
            )
        elif kind == "finish":
            self.state_manager.update_thinking_step(
                step.step_id,
                status="error" if step.state.value == "error" else "done",
                details=step.detail or "",
                duration_ms=int(step.duration_ms) if step.duration_ms else None,
            )

    def _on_tool_start(self, name: str, args: dict[str, Any], card_id: str) -> None:
        self.state_manager.start_tool(name)
        target = next((str(v) for v in args.values() if isinstance(v, str) and v), name)
        self.state_manager.update_agent_status(self._agent_name, AgentStatus.TOOL_USE, f"{name}: {target}")
        try:
            chat_panel = self.query_one("#chat-panel", ChatPanel)
            chat_panel.add_tool_card(name, args)
            self._touch_panel("tool-panel")
        except Exception:
            pass  # panel not mounted yet

    def _on_tool_finish(self, card_id: str, name: str, success: bool, result: str | None, error: str | None, diff: str | None, duration_ms: int) -> None:
        self.state_manager.finish_tool(name, result=(result or "")[:400] or None, error=error)
        try:
            chat_panel = self.query_one("#chat-panel", ChatPanel)
            chat_panel.update_tool_card(
                card_id,
                status="done" if success else "error",
                result=result,
                error=error,
                duration_ms=duration_ms,
                diff=diff,
            )
        except Exception:
            pass

    # ── layout ───────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # No Header/Footer: minimal chrome, the transcript is the hero.
        yield SystemMonitor()
        yield ChatPanel(id="chat-panel")
        yield ThinkingPanel(id="thinking-panel", classes="hidden")
        yield ToolPanel(id="tool-panel", classes="hidden")
        yield AgentsPanel(id="agents-panel", classes="hidden")
        yield InputBar(id="input-bar")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self.query_one("#command-input", Input).focus()
        self.state_manager.subscribe(self._on_state_change)
        # Live theme swaps re-render the transcript + identity card; the
        # unsubscribe handle keeps a closed app from staying registered.
        self._theme_unsubscribe = on_theme_change(self._on_theme_applied)

        # Apply the persisted theme BEFORE anything renders its first markup
        # (widget colors resolve from the live token map at render time).
        settings = get_settings()
        apply_theme(settings.theme)

        import threading

        try:
            from ..memory.shadow import get_shadow_indexer
            self.shadow_indexer = get_shadow_indexer()
            threading.Thread(target=self.shadow_indexer.start, daemon=True).start()
        except Exception:
            self.shadow_indexer = None  # optional subsystem

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        # Agentic order of service: the subsystem checklist prints one line
        # at a time ABOVE the welcome card (see BootChecklist).
        chat_panel.query_one("#messages-container").mount(BootChecklist())
        chat_panel.query_one("#messages-container").mount(WelcomeCard(version=self._version))
        self._restore_pinned_panels(settings)
        self._update_status_bar()

        # Tolerant settings load may have ignored unknown keys / reset a
        # corrupt file — say so once instead of silently swallowing it.
        notes = get_load_notes()
        if notes:
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content="Settings notes:\n" + "\n".join(f"  ∙ {n}" for n in notes),
                timestamp=datetime.now(),
            ))

    def on_unmount(self) -> None:
        """Detach from the process-global singletons. Without this, a closed
        app instance stays subscribed: the next notification hits query_one()
        on an unmounted app and crashes whoever mutated state next."""
        if getattr(self, "_theme_unsubscribe", None):
            self._theme_unsubscribe()
            self._theme_unsubscribe = None
        try:
            self.state_manager._listeners.remove(self._on_state_change)
        except (ValueError, AttributeError):
            pass
        try:
            get_thinking_engine()._callbacks.remove(self._on_thinking_event)
        except (ValueError, AttributeError):
            pass

    def on_resize(self, event: events.Resize) -> None:
        self._relayout_panels()

    def on_key(self, event: events.Key) -> None:
        if not self._show_dropdown:
            return
        dropdown = self.query_one(SlashCommandDropdown)
        if event.key == "up":
            dropdown.select_prev()
            event.stop()
        elif event.key == "down":
            dropdown.select_next()
            event.stop()
        elif event.key == "escape":
            self._hide_dropdown()
            event.stop()
        # Note: Enter is deliberately not handled here. Input's own
        # keybinding consumes Enter internally to emit Input.Submitted
        # before the raw Key event would ever bubble up to this handler,
        # so dropdown selection on Enter is handled in
        # InputBar.on_input_submitted instead, which is where Enter is
        # actually observable.

    # ── panel management ─────────────────────────────────────────

    def _touch_panel(self, panel_id: str) -> None:
        """Record that a panel received content (drives space priority)."""
        self._panel_seq += 1
        self._panel_recency[panel_id] = self._panel_seq
        self._relayout_panels()

    def _relayout_panels(self) -> None:
        """Share the vertical space below the chat evenly between the visible
        side panels, capped so the chat never drops below its floor. Panels
        shrink together as space gets tight (Termux) instead of stacking the
        chat away; space left over goes back to the chat (1fr)."""
        height = self.size.height
        if height <= 0:
            return
        chat_floor = _CHAT_FLOOR_LARGE if height >= 36 else (10 if height >= 30 else _CHAT_FLOOR_SMALL)
        try:
            monitor = self.query_one("#system-monitor")
            input_bar = self.query_one("#input-bar")
            status_bar = self.query_one("#status-bar")
        except Exception:
            return  # compose not finished yet
        fixed = monitor.outer_size.height + input_bar.outer_size.height + status_bar.outer_size.height
        available = max(0, height - fixed - chat_floor)

        # Most recently active panel first.
        ordered = sorted(self._panel_recency, key=lambda pid: self._panel_recency[pid], reverse=True)
        visible = []
        for panel_id in ordered:
            try:
                panel = self.query_one(f"#{panel_id}")
            except Exception:
                continue
            if not panel.has_class("hidden"):
                visible.append(panel)
        if not visible:
            return
        per_panel = max(3, min(_SIDE_PANEL_CAP, available // len(visible)))
        for panel in visible:
            panel.styles.max_height = per_panel

    # ── state sync ────────────────────────────────────────────────

    def _on_state_change(self, state) -> None:
        self._update_status_bar()
        thinking_panel = self.query_one("#thinking-panel", ThinkingPanel)
        tool_panel = self.query_one("#tool-panel", ToolPanel)
        agents_panel = self.query_one("#agents-panel", AgentsPanel)

        for step in state.thinking_steps:
            # Identity check (not step_id): engine steps carry ids, but
            # manually-added steps may all have step_id=None.
            blocks = [b for b in thinking_panel.query(ThinkingBlock) if b.step is step]
            if blocks:
                blocks[0].refresh_step()  # status/details changed in place
            else:
                thinking_panel.add_step(step)
        if state.thinking_steps:
            self._touch_panel("thinking-panel")

        for tool in state.tool_statuses.values():
            tool_panel.update_tool(tool)
        if state.tool_statuses:
            self._touch_panel("tool-panel")

        for agent in state.active_agents:
            agents_panel.update_agent(agent)
        if state.active_agents:
            self._touch_panel("agents-panel")

        for panel in (thinking_panel, tool_panel, agents_panel):
            panel.refresh_visibility()
        self._relayout_panels()

    # ── slash dropdown ────────────────────────────────────────────

    def get_dropdown_selection(self) -> str | None:
        """Return the currently-highlighted slash command, if the dropdown
        is open. Used by InputBar to resolve Enter-to-select instead of
        submitting the raw partially-typed text."""
        if not self._show_dropdown:
            return None
        try:
            dropdown = self.query_one(SlashCommandDropdown)
        except Exception:
            return None
        return dropdown.get_selected()

    def hide_slash_dropdown(self) -> None:
        """Public wrapper so other widgets (e.g. InputBar) can close the
        dropdown without reaching into a private method."""
        self._hide_dropdown()

    def action_dismiss_dropdown(self) -> None:
        self._hide_dropdown()

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        if value.startswith("/"):
            self._show_slash_dropdown(filter_text=value)
        else:
            if self._show_dropdown:
                self._hide_dropdown()

    def _show_slash_dropdown(self, filter_text: str = ""):
        """Show the slash command dropdown with filter."""
        self._show_dropdown = True

        existing = self.query("SlashCommandDropdown")
        if existing:
            # Update existing dropdown filter
            dropdown = existing.first()
            dropdown.filter_text = filter_text
            dropdown._update_filtered()
            dropdown._rebuild_items()
            return

        dropdown = SlashCommandDropdown(filter_text=filter_text)
        self.mount(dropdown)
        # Keep focus on input, not the dropdown
        self.query_one("#command-input", Input).focus()

    def _hide_dropdown(self):
        self._show_dropdown = False
        existing = self.query("SlashCommandDropdown")
        if existing:
            existing.remove()
        try:
            self.query_one("#command-input", Input).focus()
        except Exception:
            pass

    # ── commands / input ──────────────────────────────────────────

    def on_command_entered(self, event: CommandEntered) -> None:
        if event.command:
            self._command_history.append(event.command)
            self._history_index = len(self._command_history)
        task = OrchestrationTask(self, self._process_input(event.command))
        asyncio.create_task(task.run())

    def action_interrupt(self) -> None:
        self.notify("Interrupted — use /exit to quit", severity="warning")

    def action_command_palette(self) -> None:
        """Show the command palette."""
        from .palette import CommandPalette
        def set_command(cmd: str | None):
            if cmd:
                self.query_one("#command-input", Input).value = cmd
                self.query_one("#command-input", Input).focus()
        self.push_screen(CommandPalette(), set_command)

    def action_clear_screen(self) -> None:
        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.clear()
        self.state_manager.state.messages = []

    def action_toggle_thinking(self) -> None:
        self.query_one("#thinking-panel", ThinkingPanel).toggle_visibility()
        self._relayout_panels()

    def action_toggle_tools(self) -> None:
        self.query_one("#tool-panel", ToolPanel).toggle_visibility()
        self._relayout_panels()

    def action_toggle_agents(self) -> None:
        self.query_one("#agents-panel", AgentsPanel).toggle_visibility()
        self._relayout_panels()

    # ── settings + themes (live, persistent) ──────────────────────

    def action_open_settings(self) -> None:
        """ctrl+s / `/settings`: keyboard-first settings modal."""
        self.push_screen(SettingsModal(on_change=self._apply_live_settings))

    def _restore_pinned_panels(self, settings) -> None:
        """Restore the panels the user pinned in a previous session."""
        for panel_id in settings.pinned_panels or []:
            try:
                panel = self.query_one(f"#{panel_id}")
            except Exception:
                continue  # unknown/renamed panel id in settings
            panel.pinned = True
            panel.dismissed = False
            panel.refresh_visibility()

    def _apply_live_settings(self) -> None:
        """Called after EVERY settings change from the modal: apply to the
        running app immediately (theme, transcript, panels) — no restart."""
        settings = get_settings()
        # A theme change re-renders through the _on_theme_applied listener;
        # only rerender here when the theme did NOT change (e.g. compact
        # mode / timestamps), to avoid doing the work twice.
        theme_changed = settings.theme != get_current_theme()
        if theme_changed:
            apply_theme(settings.theme)
        else:
            self._refresh_dynamic_widgets()
        self._restore_pinned_panels(settings)
        self._relayout_panels()

    def _on_theme_applied(self, theme_name: str) -> None:
        """Theme token swap finished — re-resolve everything that bakes
        colors into markup at render time."""
        self._refresh_dynamic_widgets()

    def _refresh_dynamic_widgets(self) -> None:
        settings = get_settings()
        try:
            self.query_one(WelcomeCard).refresh_theme()
        except Exception:
            pass  # welcome card cleared
        try:
            self.query_one("#chat-panel", ChatPanel).rerender()
        except Exception:
            pass
        try:
            self.query_one(StatusBar).refresh()
        except Exception:
            pass
        # Panel headers/cards resolve colors at render(); force a repaint.
        for panel_id in ("#thinking-panel", "#tool-panel", "#agents-panel"):
            try:
                panel = self.query_one(panel_id)
            except Exception:
                continue
            for widget in panel.query("Static"):
                widget.refresh()

    def action_show_help(self) -> None:
        help_text = """
  Nexus — keys & commands
  ───────────────────────
  ctrl+p  command palette      ctrl+l  clear chat
  ctrl+g  thinking panel       ctrl+t  tools panel
  ctrl+a  agents panel         ctrl+s  settings & themes
  f1      this help

  /help /settings /theme /clear /history /tools /model /provider
  /session /facts /doctor /mcp /voice /exit
        """
        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content=help_text.strip(),
            timestamp=datetime.now(),
        ))

    def action_quit(self) -> None:
        self.exit()

    def _update_status_bar(self) -> None:
        state = self.state_manager.state
        status_bar = self.query_one(StatusBar)
        from ..providers import get_manager
        mgr = get_manager()
        # Graceful "no provider": active_provider has a default name even
        # when nothing is configured, so gate on configured providers.
        if mgr.configs:
            provider = mgr.active_provider
            cfg = mgr.get_provider_config(provider)
            model = state.active_model or (cfg.model if cfg else "")
        else:
            provider = ""
            model = state.active_model or ""
        status_bar.model = model
        status_bar.provider = provider
        status_bar.project = os.path.basename(os.getcwd())
        status_bar.battery = self._get_battery()
        status_bar.termux = self._termux_mode
        status_bar.refresh()

    def _get_battery(self) -> int:
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery:
                return battery.percent
        except Exception:
            pass
        return -1

    def _handle_command(self, command: str) -> bool:
        if not command.startswith("/"):
            return False

        parts = command[1:].split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        chat_panel = self.query_one("#chat-panel", ChatPanel)

        def system_msg(text: str) -> None:
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM, content=text, timestamp=datetime.now(),
            ))

        if cmd in ("exit", "quit", "q"):
            self.exit()
            return True

        if cmd in ("help", "h"):
            self.action_show_help()
            return True

        if cmd == "settings":
            self.action_open_settings()
            return True

        if cmd == "theme":
            if args:
                wanted = args.strip().lower().replace("_", "-")
                if wanted in THEME_NAMES:
                    apply_theme(wanted)
                    get_settings().theme = wanted
                    save_settings_now()
                    system_msg(f"Theme set to: {wanted}")
                else:
                    system_msg(
                        f"Unknown theme: {args}\nAvailable: {', '.join(THEME_NAMES)}"
                    )
            else:
                applied = cycle_theme()
                get_settings().theme = applied
                save_settings_now()
                system_msg(f"Theme: {applied}")
            return True

        if cmd == "clear":
            chat_panel.clear()
            self.state_manager.state.messages = []
            return True

        if cmd == "history":
            text = "Recent commands:\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(self._command_history[-10:])
            )
            system_msg(text)
            return True

        if cmd == "tools":
            from ..tools import get_registry
            registry = get_registry()
            text = "Available tools:\n" + "\n".join(
                f"  \u2219 {t.name}: {t.description}" for t in registry.list_all()
            )
            system_msg(text)
            return True

        if cmd == "model":
            if args:
                self.state_manager.set_active_model(args)
                system_msg(f"Model set to: {args}")
            else:
                state = self.state_manager.state
                system_msg(f"Current model: {state.active_model or 'not set'}")
            return True

        if cmd == "provider":
            from ..providers import get_manager
            mgr = get_manager()
            active = mgr.active_provider if mgr.configs else "none"
            system_msg(f"Active provider: {active}")
            return True

        if cmd == "facts":
            from ..memory import get_memory
            memory = get_memory()
            facts = memory.get_all_facts()
            text = "Stored facts:\n" if facts else "No facts stored."
            if facts:
                text += "\n".join(f"  \u2219 {k}: {v}" for k, v in facts.items())
            system_msg(text)
            return True

        if cmd == "session":
            from ..memory import get_memory
            memory = get_memory()
            session = memory.create_session()
            self.state_manager.set_session(session.id)
            system_msg(f"Session: {session.id}\nCreated: {session.created_at}")
            return True

        if cmd == "doctor":
            from ..doctor import NexusDoctor
            doctor = NexusDoctor()
            report = doctor.run_all()

            # Doctor output is plain text: the transcript renders message
            # content without Rich markup parsing (so model output with
            # brackets like List[int] survives), so no [bold]-style tags here.
            lines = ["NEXUS SYSTEM DIAGNOSTICS", ""]

            # System vitals
            sys_data = report.get("system", {})
            if sys_data:
                lines.append("SYSTEM")
                lines.append(f"  OS: {sys_data.get('os', '?')}")
                lines.append(f"  Python: {sys_data.get('python', '?')}")
                lines.append(f"  CPUs: {sys_data.get('cpus', '?')} cores")
                lines.append(f"  Memory: {sys_data.get('memory_total', '?')}")
                lines.append(f"  Disk: {sys_data.get('disk_used', '?')} / {sys_data.get('disk_total', '?')}")
                lines.append("")

            # Component checks
            check_order = ["dependencies", "environment", "config", "provider", "memory", "tools", "network", "cache", "git"]
            passed_count = 0
            for category in check_order:
                result = report.get(category, {})
                if not result:
                    continue
                passed = result.get("passed", True) and "error" not in result
                if passed:
                    passed_count += 1

                status = "\u2713" if passed else "\u2717"
                label = category.replace("_", " ").upper()

                detail = ""
                if category == "config":
                    detail = f"  provider: {result.get('active_provider', 'none')}"
                elif category == "tools":
                    detail = f"  {result.get('tool_count', 0)} tools"
                elif category == "memory":
                    detail = f"  {result.get('fact_count', 0)} facts"
                elif category == "network":
                    hosts = result.get("hosts", {})
                    good = sum(1 for v in hosts.values() if v)
                    detail = f"  {good}/{len(hosts)} hosts"
                elif category == "environment":
                    detail = f"  OS: {result.get('os', '?')}"
                elif category == "git":
                    detail = f"  branch: {result.get('branch', '?')}"
                elif category == "cache":
                    detail = f"  {result.get('found_count', 0)} artifacts"

                error = result.get("error")
                if error:
                    detail = f"  ✗ {error}"

                lines.append(f"  {status} {label}{detail}")
            lines.append("")

            # Health gauge
            total = len(check_order)
            pct = passed_count / max(total, 1)
            filled = int(pct * 20)
            bar = "\u2588" * filled + "\u2591" * (20 - filled)
            lines.append(f"Health: {bar} {pct:.0%}  ({passed_count}/{total} passed)")

            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content="\n".join(lines),
                timestamp=datetime.now(),
            ))
            return True

        if cmd == "plugins":
            # Drop-in tool-plugin cards — same renderer the REPL prints.
            try:
                from ..cli.repl import format_plugins_report
                from ..plugins import list_plugins, reload_plugins

                records = (
                    reload_plugins()
                    if "--reload" in args.lower().split()
                    else list_plugins()
                )
                system_msg(format_plugins_report(records))
            except Exception as e:
                system_msg(f"/plugins failed: {e}")
            return True

        if cmd == "discover":
            # Environment + project sniff card — same renderer as the REPL.
            try:
                from ..cli.repl import format_discover_report
                from ..discovery import discover_environment, familiarize_project

                system_msg(
                    format_discover_report(discover_environment(), familiarize_project())
                )
            except Exception as e:
                system_msg(f"/discover failed: {e}")
            return True

        if cmd == "mcp":
            system_msg("MCP: use /mcp list|add|remove")
            return True

        if cmd == "voice":
            system_msg("Voice mode: run 'nexus voice' from terminal")
            return True

        return False

    # ── the turn loop ─────────────────────────────────────────────

    def _current_provenance(self) -> tuple[str, str]:
        """(provider, model) for provenance rows — same source as the
        status bar; ("", "") when nothing is configured."""
        try:
            from ..providers import get_manager

            mgr = get_manager()
            if mgr.configs:
                provider = mgr.active_provider or ""
                cfg = mgr.get_provider_config(provider)
                model = self.state_manager.state.active_model or (cfg.model if cfg else "")
                return provider, model or ""
        except Exception:
            pass
        return "", ""

    async def _process_input(self, user_input: str) -> None:
        if not user_input.strip():
            return

        self._hide_dropdown()

        if self._handle_command(user_input):
            return

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        user_msg = ChatMessage(
            role=MessageRole.USER,
            content=user_input,
            timestamp=datetime.now(),
        )
        chat_panel.add_message(user_msg)
        self.state_manager.record_message(user_msg)

        self.state_manager.set_busy(True)
        self.state_manager.clear_thinking()
        self._seen_thinking_steps = set()

        try:
            self.state_manager.add_agent(self._agent_name, "orchestrator", model=None)
            self.state_manager.update_agent_status(self._agent_name, AgentStatus.THINKING, "processing")

            # Singleton orchestrator (recon M2-1): same instance the
            # ui_callback is wired to, so panels receive live events.
            orchestrator = self.orchestrator
            self.state_manager.state.max_context_tokens = orchestrator.config.max_context_tokens

            self._stream_msg = None
            prov_provider, prov_model = self._current_provenance()

            async def stream_callback(content: str):
                # Streaming only fires for no-tool turns (pm.stream refuses
                # tools), so this renders tokens live when the plumbing
                # allows it and is simply never called otherwise.
                if self._stream_msg is None:
                    self._stream_msg = ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=content,
                        timestamp=datetime.now(),
                        streaming=True,
                        provider=prov_provider,
                        model=prov_model,
                    )
                    chat_panel.add_message(self._stream_msg)
                else:
                    self._stream_msg.content += content
                    # Update just the last widget — no state fan-out per token.
                    widgets = chat_panel.query(ChatMessageWidget)
                    if widgets:
                        widgets[-1].refresh_content()

            turn = await orchestrator.run(user_input, stream_callback=stream_callback)

            chat_panel.end_streaming(final_content=turn.assistant_message or None)
            self._stream_msg = None
            self.state_manager.set_turn_stats(
                tokens_used=turn.tokens_used,
                duration_ms=turn.duration_ms,
                max_context_tokens=orchestrator.config.max_context_tokens,
            )

            if turn.pending_approval:
                self._handle_pending_approval(turn.pending_approval)
                return

            if turn.assistant_message:
                final_msg = ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=turn.assistant_message,
                    timestamp=datetime.now(),
                    provider=prov_provider,
                    model=prov_model,
                    elapsed_ms=turn.duration_ms or None,
                )
                chat_panel.add_message(final_msg)
                self.state_manager.record_message(final_msg)
            if turn.error:
                self.state_manager.set_error(turn.error)
                error_msg = ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"Error: {turn.error}",
                    timestamp=datetime.now(),
                )
                chat_panel.add_message(error_msg)
                self.state_manager.record_message(error_msg)
                self.state_manager.update_agent_status(self._agent_name, AgentStatus.ERROR, "turn failed")
            else:
                self.state_manager.update_agent_status(self._agent_name, AgentStatus.IDLE, None)

        except Exception as e:
            error_msg = ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"Error: {e}",
                timestamp=datetime.now(),
            )
            chat_panel.add_message(error_msg)
            self.state_manager.record_message(error_msg)
            self.state_manager.set_error(str(e))
            self.state_manager.update_agent_status(self._agent_name, AgentStatus.ERROR, "turn failed")
        finally:
            self.state_manager.set_busy(False)

    def _handle_pending_approval(self, approval: dict[str, Any]) -> None:
        result = approval.get("result")
        # The orchestrator passes a ToolResult object here (not a dict).
        metadata = getattr(result, "metadata", None) or {}
        diff = metadata.get("diff", "No diff available")
        path = metadata.get("path", "Unknown")
        tool_name = approval.get("tool_name", "tool")

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_diff_message(
            title=f"PROPOSED CHANGE — {path} ({tool_name})",
            diff=str(diff),
            body="Type 'approve' to apply, 'reject' to cancel.",
        )
        self.state_manager.set_busy(False)


def run_tui() -> None:
    """Run the TUI application."""
    app = NexusTUI()
    app.run()
