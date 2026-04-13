"""Nexus TUI - Main Textual Application."""

import asyncio
import os
import sys
from datetime import datetime
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.match import match
from textual.css.parse import parse as parse_css
from textual.screen import Screen, ModalScreen
from textual.widgets import Static, Input, Header, Footer

from .colors import CSS_COLORS
from .state import (
    TUIState,
    TUIStateManager,
    get_state_manager,
    MessageRole,
    AgentStatus,
    ToolStatus,
    ChatMessage,
    ThinkingStep,
    AgentInfo,
    ToolInfo,
)
from .widgets import (
    ChatMessageWidget,
    ChatPanel,
    ThinkingPanel,
    ToolPanel,
    AgentsPanel,
    InputBar,
    StatusBar,
    CommandEntered,
)


class NexusTUI(App):
    """Main Textual application for Nexus."""

    CSS_PATH = "styles.css"
    TITLE = "Nexus - Terminal User Interface"
    SUB_TITLE = "Rehoboth Genesis"

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True),
        Binding("ctrl+p", "command_palette", "Command Palette"),
        Binding("ctrl+l", "clear_screen", "Clear Screen"),
        Binding("ctrl+g", "toggle_thinking", "Toggle Thinking Panel"),
        Binding("ctrl+t", "toggle_tools", "Toggle Tools Panel"),
        Binding("ctrl+a", "toggle_agents", "Toggle Agents Panel"),
        Binding("f1", "show_help", "Help"),
        Binding("f2", "show_status", "Status"),
        Binding("escape", "quit", "Quit", priority=True),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state_manager = get_state_manager()
        self.state_manager.reset()
        self._input_buffer = ""
        self._command_history: list[str] = []
        self._history_index = -1
        self._termux_mode = os.path.exists("/data/data/com.termux")
        self._version = "0.1.0"

        from .. import __version__
        self._version = __version__

    def compose(self) -> ComposeResult:
        """Create the layout."""
        yield Header()

        with Container(id="app-container"):
            yield ChatPanel(id="chat-panel")
            yield ThinkingPanel(id="thinking-panel")
            yield ToolPanel(id="tool-panel")
            yield AgentsPanel(id="agents-panel")

        yield InputBar(id="input-bar")

        status = StatusBar(
            version=self._version,
            model="",
            termux=self._termux_mode,
            battery=self._get_battery(),
        )
        yield status

        yield Footer()

    def _get_battery(self) -> int:
        """Get battery percentage if available."""
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery:
                return battery.percent
        except Exception:
            pass
        return -1

    def on_mount(self) -> None:
        """Initialize on mount."""
        self.state_manager.subscribe(self._on_state_change)

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content="Welcome to Nexus TUI! Type /help for available commands.",
            timestamp=datetime.now(),
        ))

        self._update_status_bar()

    def _on_state_change(self, state: TUIState) -> None:
        """Handle state changes from the state manager."""
        chat_panel = self.query_one("#chat-panel", ChatPanel)
        thinking_panel = self.query_one("#thinking-panel", ThinkingPanel)
        tool_panel = self.query_one("#tool-panel", ToolPanel)
        agents_panel = self.query_one("#agents-panel", AgentsPanel)

        for msg in state.messages[-5:]:
            if len(chat_panel._messages) == 0 or chat_panel._messages[-1] != msg:
                chat_panel.add_message(msg)

        for step in state.thinking_steps:
            thinking_panel.add_step(step)

        for tool in state.tool_statuses.values():
            tool_panel.update_tool(tool)

        for agent in state.active_agents:
            agents_panel.update_agent(agent)

    def action_interrupt(self) -> None:
        """Handle Ctrl+C interrupt."""
        self.post_message(self.Notification("Interrupted - use /exit to quit", severity="warning"))

    def action_command_palette(self) -> None:
        """Placeholder for command palette."""
        self.post_message(self.Notification("Command palette not yet implemented", severity="info"))

    def action_clear_screen(self) -> None:
        """Clear the chat panel."""
        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.clear()
        self.state_manager.state.messages = []

    def action_toggle_thinking(self) -> None:
        """Toggle thinking panel visibility."""
        panel = self.query_one("#thinking-panel", ThinkingPanel)
        panel.toggle_class("hidden")

    def action_toggle_tools(self) -> None:
        """Toggle tools panel visibility."""
        panel = self.query_one("#tool-panel", ToolPanel)
        panel.toggle_class("hidden")

    def action_toggle_agents(self) -> None:
        """Toggle agents panel visibility."""
        panel = self.query_one("#agents-panel", AgentsPanel)
        panel.toggle_class("hidden")

    def action_show_help(self) -> None:
        """Show help overlay."""
        help_text = """
╔══════════════════════════════════════════════════════════╗
║                    Nexus TUI Help                        ║
╠══════════════════════════════════════════════════════════╣
║  Keyboard Shortcuts:                                    ║
║    Ctrl+C    - Interrupt current operation                ║
║    Ctrl+P    - Command palette (placeholder)             ║
║    Ctrl+L    - Clear screen                              ║
║    Ctrl+G    - Toggle thinking panel                     ║
║    Ctrl+T    - Toggle tools panel                        ║
║    Ctrl+A    - Toggle agents panel                       ║
║    F1        - Show this help                            ║
║    F2        - Show status                               ║
║    Escape    - Quit                                      ║
║                                                          ║
║  Slash Commands:                                         ║
║    /help     - Show this help                            ║
║    /clear    - Clear chat history                        ║
║    /history  - Show command history                     ║
║    /tools    - List available tools                     ║
║    /model    - Show/switch model                        ║
║    /facts    - Show stored facts                        ║
║    /session  - Show session info                         ║
║    /exit     - Exit the TUI                              ║
╚══════════════════════════════════════════════════════════╝
        """
        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content=help_text.strip(),
            timestamp=datetime.now(),
        ))

    def action_show_status(self) -> None:
        """Show status information."""
        state = self.state_manager.state
        status_text = f"""
Session: {state.session_id or 'new'}
Model: {state.active_model or 'not set'}
Messages: {len(state.messages)}
Thinking Steps: {len(state.thinking_steps)}
Tools: {len(state.tool_statuses)}
Agents: {len(state.active_agents)}
        """.strip()

        chat_panel = self.query_one("#chat-panel", ChatPanel)
        chat_panel.add_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content=status_text,
            timestamp=datetime.now(),
        ))

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def _update_status_bar(self) -> None:
        """Update the status bar with current info."""
        state = self.state_manager.state
        status = self.query_one(StatusBar)

        model = state.active_model or "not set"
        if hasattr(status, "model"):
            status.model = model

    def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True if handled."""
        if not command.startswith("/"):
            return False

        parts = command[1:].split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        chat_panel = self.query_one("#chat-panel", ChatPanel)

        if cmd in ("exit", "quit", "q"):
            self.exit()
            return True

        elif cmd in ("help", "h"):
            self.action_show_help()
            return True

        elif cmd == "clear":
            chat_panel.clear()
            self.state_manager.state.messages = []
            return True

        elif cmd == "history":
            history_text = "Recent commands:\n" + "\n".join(
                f"  {i+1}. {cmd}" for i, cmd in enumerate(self._command_history[-10:])
            )
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=history_text,
                timestamp=datetime.now(),
            ))
            return True

        elif cmd == "tools":
            from ..tools import get_registry
            registry = get_registry()
            tools_text = "Available tools:\n" + "\n".join(
                f"  - {t.name}: {t.description}" for t in registry.list_all()
            )
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=tools_text,
                timestamp=datetime.now(),
            ))
            return True

        elif cmd == "model":
            if args:
                self.state_manager.set_active_model(args)
                chat_panel.add_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"Model switched to: {args}",
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

        elif cmd == "facts":
            from ..memory import get_memory
            memory = get_memory()
            facts = memory.get_all_facts()
            if facts:
                facts_text = "Stored facts:\n" + "\n".join(
                    f"  - {k}: {v}" for k, v in facts.items()
                )
            else:
                facts_text = "No facts stored."
            chat_panel.add_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=facts_text,
                timestamp=datetime.now(),
            ))
            return True

        elif cmd == "session":
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

        return False

    async def _process_input(self, user_input: str) -> None:
        """Process user input through the orchestrator."""
        if not user_input.strip():
            return

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
            from ..providers import get_manager
            from ..tools import get_registry
            from ..memory import get_memory
            from ..agent.orchestrator import AgentOrchestrator, AgentConfig

            manager = get_manager()
            registry = get_registry()
            memory = get_memory()

            config = AgentConfig(
                stream=True,
                verbose=False,
            )

            orchestrator = AgentOrchestrator(
                provider_manager=manager,
                tool_registry=registry,
                memory=memory,
                config=config,
            )

            async def stream_callback(content: str):
                chat_panel.add_message(ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=content,
                    timestamp=datetime.now(),
                ))

            turn = await orchestrator.run(user_input, stream_callback=stream_callback)

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

    def on_input_bar_command_entered(self, event: CommandEntered) -> None:
        """Handle command entered in input bar."""
        command = str(event).strip()
        if command:
            self._command_history.append(command)
            self._history_index = len(self._command_history)

        asyncio.create_task(self._process_input(command))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        command = event.value.strip()
        if command:
            self._command_history.append(command)
            self._history_index = len(self._command_history)

        asyncio.create_task(self._process_input(command))

        input_bar = self.query_one("#input-bar", InputBar)
        input_bar.clear()


class Notification(events.Message):
    """Custom notification message."""

    def __init__(self, message: str, severity: str = "info"):
        super().__init__()
        self.message = message
        self.severity = severity


class NotificationOverlay(Static):
    """Overlay for displaying notifications."""

    def __init__(self, message: str, severity: str = "info", **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self.severity = severity

    def compose(self) -> ComposeResult:
        color = {
            "info": CSS_COLORS["info"],
            "warning": CSS_COLORS["warning"],
            "error": CSS_COLORS["error"],
            "success": CSS_COLORS["success"],
        }.get(self.severity, CSS_COLORS["text"])

        yield Static(self.message, markup=True)


def run_tui() -> None:
    """Run the TUI application."""
    app = NexusTUI()
    app.run()