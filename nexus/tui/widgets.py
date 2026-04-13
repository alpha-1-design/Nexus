"""Reusable widgets for the Nexus TUI."""

from datetime import datetime
from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.match import match
from textual.css.parse import parse as parse_css
from textual.reactive import reactive
from textual.widgets import Static, Input, Label

from .colors import CSS_COLORS
from .state import (
    ChatMessage,
    MessageRole,
    ToolInfo,
    ToolStatus,
    AgentInfo,
    AgentStatus,
    ThinkingStep,
)


class ChatMessageWidget(Static):
    """Displays a single chat message."""

    def __init__(self, message: ChatMessage, **kwargs):
        super().__init__(**kwargs)
        self.message = message

    def compose(self) -> ComposeResult:
        role_str = self.message.role.name.lower()
        timestamp = self.message.timestamp.strftime("%H:%M")

        if self.message.role == MessageRole.USER:
            color = CSS_COLORS["user_message"]
        elif self.message.role == MessageRole.ASSISTANT:
            color = CSS_COLORS["assistant_message"]
        elif self.message.role == MessageRole.SYSTEM:
            color = CSS_COLORS["system_message"]
        else:
            color = CSS_COLORS["tool_call"]

        content = self._format_content()

        yield Horizontal(
            Static(f"[{timestamp}]", classes="timestamp"),
            Static(f"[{role_str}]", markup=True, classes="role"),
            Static(content, classes="content", markup=True),
            classes="message-row",
        )

    def _format_content(self) -> str:
        content = self.message.content
        if self.message.tool_name:
            content = f"[tool: {self.message.tool_name}]{content}"
        if self.message.tool_calls:
            for tc in self.message.tool_calls:
                content += f"\n[tool call: {tc.get('name', 'unknown')}]"
        return content


class ChatPanel(Vertical):
    """Scrollable chat history panel."""

    def __init__(self, messages: list[ChatMessage] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._messages = messages or []

    def compose(self) -> ComposeResult:
        yield Static("Chat History", classes="panel-header")
        with Vertical(id="messages-container"):
            pass

    def add_message(self, message: ChatMessage) -> None:
        self._messages.append(message)
        widget = ChatMessageWidget(message)
        self.query_one("#messages-container").mount(widget)
        self.scroll_end()

    def clear(self) -> None:
        self._messages = []
        container = self.query_one("#messages-container")
        container.remove_children()


class ThinkingBlock(Container):
    """A thinking step with expand/collapse."""

    expanded = reactive(True)

    def __init__(self, step: ThinkingStep, **kwargs):
        super().__init__(**kwargs)
        self.step = step

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(f"#{self.step.step_number}", classes="step-number")
            yield Static(self.step.description, classes="step-description")

    def render(self) -> str:
        icon = "[-]" if self.expanded else "[+]"
        lines = [f"{icon} {self.step.description}"]
        if self.expanded and self.step.details:
            lines.append(f"    {self.step.details}")
        return "\n".join(lines)

    def on_click(self) -> None:
        self.expanded = not self.expanded


class ThinkingPanel(Vertical):
    """Panel showing structured thinking steps."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._steps: list[ThinkingStep] = []

    def compose(self) -> ComposeResult:
        yield Static("Thinking", classes="panel-header")
        with Vertical(id="thinking-container"):
            pass

    def add_step(self, step: ThinkingStep) -> None:
        self._steps.append(step)
        widget = ThinkingBlock(step)
        self.query_one("#thinking-container").mount(widget)

    def clear(self) -> None:
        self._steps = []
        container = self.query_one("#thinking-container")
        container.remove_children()


class ToolStatusWidget(Static):
    """Displays a tool's execution status."""

    def __init__(self, tool: ToolInfo, **kwargs):
        super().__init__(**kwargs)
        self.tool = tool

    def compose(self) -> ComposeResult:
        status_icon = {
            ToolStatus.PENDING: "[ ]",
            ToolStatus.RUNNING: "[...]",
            ToolStatus.DONE: "[✓]",
            ToolStatus.ERROR: "[✗]",
        }.get(self.tool.status, "[?]")

        color = {
            ToolStatus.PENDING: CSS_COLORS["text_dim"],
            ToolStatus.RUNNING: CSS_COLORS["info"],
            ToolStatus.DONE: CSS_COLORS["success"],
            ToolStatus.ERROR: CSS_COLORS["error"],
        }.get(self.tool.status, CSS_COLORS["text"])

        duration_str = f" ({self.tool.duration_ms}ms)" if self.tool.duration_ms else ""

        yield Static(
            f"{status_icon} {self.tool.name}{duration_str}",
            markup=True,
            classes="tool-status",
        )


class ToolPanel(Vertical):
    """Panel showing tool execution status."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tools: dict[str, ToolInfo] = {}

    def compose(self) -> ComposeResult:
        yield Static("Tools", classes="panel-header")
        with Vertical(id="tools-container"):
            pass

    def update_tool(self, tool: ToolInfo) -> None:
        self._tools[tool.name] = tool
        container = self.query_one("#tools-container")

        existing = container.query(f"#tool-{tool.name}")
        if existing:
            existing.remove()

        widget = ToolStatusWidget(tool, id=f"tool-{tool.name}")
        container.mount(widget)

    def clear(self) -> None:
        self._tools = {}
        container = self.query_one("#tools-container")
        container.remove_children()


class AgentCard(Static):
    """Displays an agent's status."""

    def __init__(self, agent: AgentInfo, **kwargs):
        super().__init__(**kwargs)
        self.agent = agent

    def compose(self) -> ComposeResult:
        status_icon = {
            AgentStatus.IDLE: "●",
            AgentStatus.THINKING: "◐",
            AgentStatus.TOOL_USE: "◎",
            AgentStatus.WAITING: "○",
            AgentStatus.ERROR: "✗",
        }.get(self.agent.status, "?")

        color = {
            AgentStatus.IDLE: CSS_COLORS["text_dim"],
            AgentStatus.THINKING: CSS_COLORS["info"],
            AgentStatus.TOOL_USE: CSS_COLORS["warning"],
            AgentStatus.WAITING: CSS_COLORS["text_dim"],
            AgentStatus.ERROR: CSS_COLORS["error"],
        }.get(self.agent.status, CSS_COLORS["text"])

        model_str = f" ({self.agent.model})" if self.agent.model else ""

        yield Static(
            f"{status_icon} {self.agent.name}{model_str}",
            markup=True,
            classes="agent-card",
        )


class AgentsPanel(Vertical):
    """Panel showing active agents."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._agents: list[AgentInfo] = []

    def compose(self) -> ComposeResult:
        yield Static("Agents", classes="panel-header")
        with Vertical(id="agents-container"):
            pass

    def update_agent(self, agent: AgentInfo) -> None:
        found = False
        for i, a in enumerate(self._agents):
            if a.name == agent.name:
                self._agents[i] = agent
                found = True
                break

        if not found:
            self._agents.append(agent)

        container = self.query_one("#agents-container")
        existing = container.query(f"#agent-{agent.name}")
        if existing:
            existing.remove()

        widget = AgentCard(agent, id=f"agent-{agent.name}")
        container.mount(widget)

    def clear(self) -> None:
        self._agents = []
        container = self.query_one("#agents-container")
        container.remove_children()


class ProgressBar(Static):
    """ASCII-style progress bar."""

    percent = reactive(0.0)
    width = 20

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def render(self) -> str:
        filled = int(self.percent / 100 * self.width)
        bar = "▓" * filled + "░" * (self.width - filled)
        return f"[{bar}] {self.percent:.0f}%"


class LoadingIndicator(Static):
    """Animated spinner."""

    def __init__(self, message: str = "Loading...", **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self._spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._index = 0

    def render(self) -> str:
        char = self._spinner_chars[self._index]
        self._index = (self._index + 1) % len(self._spinner_chars)
        return f"{char} {self.message}"


class InputBar(Container):
    """Command input bar with history and completion."""

    input_value = reactive("")
    history_index = reactive(-1)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._command_history: list[str] = []
        self._history_position = 0

    def compose(self) -> ComposeResult:
        yield Static("CMD:>", classes="prompt")
        yield Input(
            placeholder="Type a message or /help for commands...",
            id="command-input",
            classes="input-field",
        )

    def on_mount(self) -> None:
        self.focused_input = self.query_one("#command-input", Input)

    @property
    def value(self) -> str:
        return self.query_one("#command-input", Input).value

    def clear(self) -> None:
        self.query_one("#command-input", Input).value = ""

    def add_to_history(self, command: str) -> None:
        if command and command != self._command_history[-1] if self._command_history else True:
            self._command_history.append(command)
            self._history_position = len(self._command_history)

    def history_up(self) -> None:
        if self._command_history and self._history_position > 0:
            self._history_position -= 1
            self.query_one("#command-input", Input).value = self._command_history[self._history_position]

    def history_down(self) -> None:
        if self._history_position < len(self._command_history):
            self._history_position += 1
            if self._history_position == len(self._command_history):
                self.query_one("#command-input", Input).value = ""
            else:
                self.query_one("#command-input", Input).value = self._command_history[self._history_position]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if command:
            self.add_to_history(command)
            self.post_message(self.CommandEntered(command))
        self.clear()

    def on_key(self, event: events.Key) -> bool:
        if event.key == "up":
            self.history_up()
            return True
        elif event.key == "down":
            self.history_down()
            return True
        return False


class CommandEntered(str):
    """Message sent when user enters a command."""

    def __new__(cls, command: str):
        return super().__new__(cls, command)


class StatusBar(Static):
    """Bottom status bar with system info."""

    def __init__(self, version: str = "0.1.0", model: str = "", termux: bool = False, battery: int = 100, **kwargs):
        super().__init__(**kwargs)
        self.version = version
        self.model = model
        self.termux = termux
        self.battery = battery

    def render(self) -> str:
        parts = [f"v{self.version}"]
        if self.model:
            parts.append(f"Model: {self.model}")
        if self.termux:
            parts.append("Termux: ✓")
        if self.battery >= 0:
            parts.append(f"Battery: {self.battery}%")
        return " | ".join(parts)


class PanelHeader(Static):
    """Reusable panel header."""

    def __init__(self, title: str, **kwargs):
        super().__init__(title, **kwargs)