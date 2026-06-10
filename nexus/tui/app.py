"""Nexus TUI — Professional Terminal User Interface."""

import asyncio
import os
from datetime import datetime
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input

from ..agent import get_orchestrator
from .state import (
    AgentInfo,
    AgentStatus,
    ChatMessage,
    MessageRole,
    TUIState,
    get_state_manager,
)
from .task_wrapper import OrchestrationTask
from .widgets import (
    AgentsPanel,
    ChatMessageWidget,
    ChatPanel,
    CommandEntered,
    InputBar,
    SlashCommandDropdown,
    StatusBar,
    SystemMonitor,
    ThinkingPanel,
    ToolPanel,
)


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
        Binding("escape", "dismiss_dropdown", "Dismiss", priority=True),
        Binding("f1", "show_help", "Help"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orchestrator = get_orchestrator()
        self.state_manager = get_state_manager()
        self.orchestrator.set_ui_callback(self._on_orchestrator_event)
        self.state_manager.reset()
        self._command_history: list[str] = []
        self._history_index = -1
        self._termux_mode = os.path.exists("/data/data/com.termux")
        self._version = "0.1.0"
        self._show_dropdown = False
        from .. import __version__
        self._version = __version__

    def _on_orchestrator_event(self, event_type: str, data: Any):
        if event_type == "thinking":
            self.state_manager.add_thinking_step(
                data.get("number", 0),
                data.get("description", ""),
                data.get("details", ""),
            )
        elif event_type == "agent_status":
            self.state_manager.update_agent_status(
                data.get("name"), data.get("status"), data.get("task"),
            )

    def compose(self) -> ComposeResult:
        yield Header()
        yield SystemMonitor()
        yield ChatPanel(id="chat-panel")
        yield ThinkingPanel(id="thinking-panel")
        yield ToolPanel(id="tool-panel")
        yield AgentsPanel(id="agents-panel")
        yield InputBar(id="input-bar")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#command-input", Input).focus()
        self.state_manager.subscribe(self._on_state_change)

        import threading
        from ..memory.shadow import get_shadow_indexer
        self.shadow_indexer = get_shadow_indexer()
        threading.Thread(target=self.shadow_indexer.start, daemon=True).start()

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content="Welcome to Nexus. Type /help for commands.",
            timestamp=datetime.now(),
        ))
        self._update_status_bar()

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        if value.startswith("/"):
            self._show_slash_dropdown(filter_text=value)
        else:
            if self._show_dropdown:
                self._hide_dropdown()

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
        elif event.key == "enter":
            selected = dropdown.get_selected()
            if selected:
                self.query_one("#command-input", Input).value = selected + " "
                self._hide_dropdown()
            event.stop()
        elif event.key == "escape":
            self._hide_dropdown()
            event.stop()

    def action_dismiss_dropdown(self) -> None:
        self._hide_dropdown()

    def on_input_key_down(self, event: events.Key) -> None:
        if self._show_dropdown:
            dropdown = self.query_one(SlashCommandDropdown)
            if event.key == "up":
                dropdown.select_prev()
                event.stop()
            elif event.key == "down":
                dropdown.select_next()
                event.stop()
            elif event.key == "enter":
                selected = dropdown.get_selected()
                if selected:
                    self.query_one("#command-input", Input).value = selected + " "
                    self._hide_dropdown()
                event.stop()
            elif event.key == "escape":
                self._hide_dropdown()
                event.stop()
        elif event.key == "escape":
            self._hide_dropdown()

    def _on_state_change(self, state: TUIState) -> None:
        self._update_status_bar()
        chat_panel = self.query_one("#chat-panel", ChatPanel)
        thinking_panel = self.query_one("#thinking-panel", ThinkingPanel)
        tool_panel = self.query_one("#tool-panel", ToolPanel)
        agents_panel = self.query_one("#agents-panel", AgentsPanel)

        if state.messages:
            last_msg = state.messages[-1]
            if not chat_panel._messages or chat_panel._messages[-1] != last_msg:
                chat_panel.add_message(last_msg)

        for step in state.thinking_steps:
            if step not in thinking_panel._steps:
                thinking_panel.add_step(step)

        for tool in state.tool_statuses.values():
            tool_panel.update_tool(tool)

        for agent in state.active_agents:
            agents_panel.update_agent(agent)

    def _show_slash_dropdown(self, filter_text: str = ""):
        """Show the slash command dropdown with filter."""
        self._show_dropdown = True

        existing = self.query("SlashCommandDropdown")
        if existing:
            # Update existing dropdown filter
            dropdown = existing.first()
            dropdown.filter_text = filter_text
            dropdown._update_filtered()
            dropdown._render()
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
        self.query_one("#command-input", Input).focus()

    def action_dismiss_dropdown(self):
        self._hide_dropdown()

    def on_command_entered(self, event: CommandEntered) -> None:
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
        panel = self.query_one("#thinking-panel", ThinkingPanel)
        panel.toggle_class("hidden")

    def action_toggle_tools(self) -> None:
        panel = self.query_one("#tool-panel", ToolPanel)
        panel.toggle_class("hidden")

    def action_toggle_agents(self) -> None:
        panel = self.query_one("#agents-panel", AgentsPanel)
        panel.toggle_class("hidden")

    def action_show_help(self) -> None:
        help_text = """
╔══════════════════════════════════════════════════╗
║  Nexus — Keyboard Shortcuts                     ║
╠══════════════════════════════════════════════════╣
║  Ctrl+P    Commands     Command palette          ║
║  Ctrl+L    Clear        Clear chat               ║
║  Ctrl+G    Thinking     Toggle thinking panel    ║
║  Ctrl+T    Tools        Toggle tools panel       ║
║  Ctrl+A    Agents       Toggle agents panel      ║
║  F1        Help         Show this help           ║
║  Escape    Dismiss      Dismiss popups           ║
║                                                ║
║  /help — Show help                              ║
║  /clear — Clear chat                           ║
║  /tools — List tools                           ║
║  /model — Switch model                         ║
║  /provider — Show provider info                ║
║  /doctor — Run diagnostics                     ║
║  /session — Session info                       ║
║  /facts — Stored facts                         ║
║  /voice — Voice mode                           ║
║  /mcp — MCP servers                            ║
║  /exit — Quit                                  ║
╚══════════════════════════════════════════════════╝
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
        status_bar.model = state.active_model or mgr.active_provider or ""
        status_bar.project = os.path.basename(os.getcwd())
        status_bar.battery = self._get_battery()
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

        if cmd in ("exit", "quit", "q"):
            self.exit()
            return True

        if cmd in ("help", "h"):
            self.action_show_help()
            return True

        if cmd == "clear":
            chat_panel.clear()
            self.state_manager.state.messages = []
            return True

        if cmd == "history":
            text = "Recent commands:\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(self._command_history[-10:])
            )
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM, content=text, timestamp=datetime.now(),
            ))
            return True

        if cmd == "tools":
            from ..tools import get_registry
            registry = get_registry()
            text = "Available tools:\n" + "\n".join(
                f"  \u2219 {t.name}: {t.description}" for t in registry.list_all()
            )
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM, content=text, timestamp=datetime.now(),
            ))
            return True

        if cmd == "model":
            if args:
                self.state_manager.set_active_model(args)
                chat_panel.add_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"Model set to: {args}",
                    timestamp=datetime.now(),
                ))
            else:
                state = self.state_manager.state
                chat_panel.add_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"Current model: {state.active_model or 'not set'}",
                    timestamp=datetime.now(),
                ))
            return True

        if cmd == "provider":
            from ..providers import get_manager
            mgr = get_manager()
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"Active provider: {mgr.active_provider or 'none'}",
                timestamp=datetime.now(),
            ))
            return True

        if cmd == "facts":
            from ..memory import get_memory
            memory = get_memory()
            facts = memory.get_all_facts()
            text = "Stored facts:\n" if facts else "No facts stored."
            if facts:
                text += "\n".join(f"  \u2219 {k}: {v}" for k, v in facts.items())
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM, content=text, timestamp=datetime.now(),
            ))
            return True

        if cmd == "session":
            from ..memory import get_memory
            memory = get_memory()
            session = memory.create_session()
            self.state_manager.set_session(session.id)
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"Session: {session.id}\nCreated: {session.created_at}",
                timestamp=datetime.now(),
            ))
            return True

        if cmd == "doctor":
            from ..doctor import NexusDoctor
            doctor = NexusDoctor()
            report = doctor.run_all()

            lines = ["[bold cyan]NEXUS SYSTEM DIAGNOSTICS[/]", ""]

            # System vitals
            sys_data = report.get("system", {})
            if sys_data:
                lines.append(f"[bold]SYSTEM[/]")
                lines.append(f"  OS: [white]{sys_data.get('os', '?')}[/]")
                lines.append(f"  Python: [white]{sys_data.get('python', '?')}[/]")
                lines.append(f"  CPUs: [white]{sys_data.get('cpus', '?')}[/] cores")
                lines.append(f"  Memory: [white]{sys_data.get('memory_total', '?')}[/]")
                lines.append(f"  Disk: [white]{sys_data.get('disk_used', '?')} / {sys_data.get('disk_total', '?')}[/]")
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

                status = "[green]\u2713[/]" if passed else "[red]\u2717[/]"
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
                    detail = f"  [red]{error}[/]"

                lines.append(f"  {status} [bold]{label}[/]{detail}")
            lines.append("")

            # Health gauge
            total = len(check_order)
            pct = passed_count / max(total, 1)
            filled = int(pct * 20)
            bar = "\u2588" * filled + "\u2591" * (20 - filled)
            color = "green" if pct >= 0.8 else "yellow" if pct >= 0.5 else "red"
            lines.append(f"Health: [{color}]{bar} {pct:.0%}[/]  ({passed_count}/{total} passed)")

            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content="\n".join(lines),
                timestamp=datetime.now(),
            ))
            return True

        if cmd == "mcp":
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content="MCP: use /mcp list|add|remove",
                timestamp=datetime.now(),
            ))
            return True

        if cmd == "voice":
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content="Voice mode: run 'nexus voice' from terminal",
                timestamp=datetime.now(),
            ))
            return True

        return False

    async def _process_input(self, user_input: str) -> None:
        if not user_input.strip():
            return

        self._hide_dropdown()

        if self._handle_command(user_input):
            return

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.USER,
            content=user_input,
            timestamp=datetime.now(),
        ))

        self.state_manager.set_busy(True)

        try:
            from ..agent.orchestrator import AgentConfig, AgentOrchestrator
            from ..memory import get_memory
            from ..providers import get_manager
            from ..tools import get_registry

            manager = get_manager()
            registry = get_registry()
            memory = get_memory()
            config = AgentConfig(stream=True, verbose=False)
            orchestrator = AgentOrchestrator(
                provider_manager=manager,
                tool_registry=registry,
                memory=memory,
                config=config,
            )

            self._current_assistant_message = None

            async def stream_callback(content: str):
                if self._current_assistant_message is None:
                    self._current_assistant_message = ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=content,
                        timestamp=datetime.now(),
                    )
                    chat_panel.add_message(self._current_assistant_message)
                else:
                    self._current_assistant_message.content += content
                    widgets = chat_panel.query(ChatMessageWidget)
                    if widgets:
                        widgets[-1].message = self._current_assistant_message
                        widgets[-1].refresh()
                self.state_manager.refresh()

            turn = await orchestrator.run(user_input, stream_callback=stream_callback)
            self.state_manager.refresh()

            if turn.pending_approval:
                self._handle_pending_approval(turn.pending_approval)
                return

            if turn.assistant_message:
                chat_panel.add_message(ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=turn.assistant_message,
                    timestamp=datetime.now(),
                ))
            if turn.error:
                self.state_manager.set_error(turn.error)

        except Exception as e:
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"Error: {e}",
                timestamp=datetime.now(),
            ))
            self.state_manager.set_error(str(e))
        finally:
            self.state_manager.set_busy(False)

    def _handle_pending_approval(self, approval: dict[str, Any]) -> None:
        result = approval.get("result", {})
        diff = result.get("metadata", {}).get("diff", "No diff available")
        path = result.get("metadata", {}).get("path", "Unknown")
        display = f"\n{'='*60}\nPROPOSED CHANGE: {path}\n{'='*60}\n{diff}"

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM, content=display, timestamp=datetime.now(),
        ))
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content="Type 'approve' to apply, 'reject' to cancel.",
            timestamp=datetime.now(),
        ))
        self.state_manager.set_busy(False)

    def on_input_bar_command_entered(self, event: CommandEntered) -> None:
        command = str(event).strip()
        if command:
            self._command_history.append(command)
            self._history_index = len(self._command_history)
        asyncio.create_task(self._process_input(command))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if command:
            self._command_history.append(command)
            self._history_index = len(self._command_history)
        asyncio.create_task(self._process_input(command))
        self.query_one("#input-bar", InputBar).clear()


def run_tui() -> None:
    """Run the TUI application."""
    app = NexusTUI()
    app.run()
