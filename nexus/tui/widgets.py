"""Reusable widgets for the Nexus TUI.

Design language: minimal chrome, information-dense transcript, dim metadata,
one accent color per role. Icons are limited to ▸ ✓ ✗ ⟳ ● ○ ● style glyphs —
no emoji clutter.
"""

import os
import re
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Input, Static

from .colors import CSS_COLORS
from .state import (
    AgentInfo,
    AgentStatus,
    ChatMessage,
    MessageRole,
    ThinkingStep,
    ToolInfo,
    ToolStatus,
    get_state_manager,
)

# --- tiny inline markup for assistant text ---------------------------------
# Content is rendered as a rich `Text` (never parsed as markup), so code-like
# brackets such as `List[int]` survive rendering (a real past bug). We support
# a deliberately tiny marker subset — **bold** and %%dim%% — and HIDE the
# markers, styling just the enclosed text.
_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*|%%(.+?)%%", re.DOTALL)


def apply_inline_markup(text: Text) -> Text:
    """Return a Text with **bold** / %%dim%% markers replaced by styled runs."""
    raw = str(text)
    if not _MARKUP_RE.search(raw):
        return text
    rendered = Text()
    pos = 0
    for match in _MARKUP_RE.finditer(raw):
        if match.start() > pos:
            rendered.append(raw[pos:match.start()])
        if match.group(1) is not None:
            rendered.append(match.group(1), style="bold")
        else:
            rendered.append(match.group(2), style="dim")
        pos = match.end()
    if pos < len(raw):
        rendered.append(raw[pos:])
    return rendered


ROLE_STYLES = {
    MessageRole.USER: ("you", CSS_COLORS["user_message"]),
    MessageRole.ASSISTANT: ("nexus", CSS_COLORS["assistant_message"]),
    MessageRole.SYSTEM: ("sys", CSS_COLORS["system_message"]),
    MessageRole.TOOL: ("tool", CSS_COLORS["tool_call"]),
}
ROLE_CSS_CLASS = {
    MessageRole.USER: "role-user",
    MessageRole.ASSISTANT: "role-assistant",
    MessageRole.SYSTEM: "role-system",
    MessageRole.TOOL: "role-tool",
}

TOOL_STATUS_ICONS = {
    "pending": ("○", "dim"),
    "running": ("⟳", CSS_COLORS["accent"]),
    "done": ("✓", CSS_COLORS["success"]),
    "error": ("✗", CSS_COLORS["error"]),
}


def _truncate(value: str, limit: int) -> str:
    value = value.strip().replace("\n", " ⏎ ")
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _summarize_target(args: dict[str, Any]) -> str:
    """Pick the argument that best identifies what a tool call acts on."""
    for key in ("path", "file_path", "filePath", "pattern", "command", "url", "query", "action"):
        value = args.get(key)
        if value:
            return _truncate(str(value), 48)
    for value in args.values():
        if isinstance(value, str) and value:
            return _truncate(value, 48)
    return ""


# --- unified diff rendering -------------------------------------------------

def classify_diff_line(line: str) -> str:
    """Classify a unified diff line into a render kind.

    Returns one of: "meta" (---/+++/... markers), "hunk" (@@), "add", "del",
    "ctx".
    """
    if line.startswith(("---", "+++")):
        return "meta"
    if line.startswith("..."):
        return "meta"
    if line.startswith("@@"):
        return "hunk"
    if line.startswith("+"):
        return "add"
    if line.startswith("-"):
        return "del"
    return "ctx"


DIFF_STYLES = {
    "meta": f"dim {CSS_COLORS['diff_meta']}",
    "hunk": f"dim {CSS_COLORS['diff_meta']}",
    "add": CSS_COLORS["diff_add"],
    "del": CSS_COLORS["diff_del"],
    "ctx": "dim",
}


def render_diff(diff_text: str) -> Text:
    """Parse standard unified diff text into a colorized rich Text.

    `+` lines green, `-` lines red, `@@`/`---`/`+++` dim cyan. Long lines wrap
    naturally inside the surrounding Static, so no manual wrapping here.
    """
    text = Text()
    for line in diff_text.splitlines() or [""]:
        text.append(line, DIFF_STYLES[classify_diff_line(line)])
        text.append("\n")
    return text


class DiffBlock(Static):
    """Bordered block that renders a unified diff inside a tool card."""

    def __init__(self, diff_text: str, **kwargs):
        super().__init__(**kwargs)
        self.diff_text = diff_text

    def render(self) -> Text:
        return render_diff(self.diff_text)


# --- transcript --------------------------------------------------------------


class ChatMessageWidget(Container):
    """One transcript entry: role badge + dim HH:MM stamp + wrapped body, with
    a left accent border color-coded per role."""

    def __init__(self, message: ChatMessage, **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self._cursor_on = True

    # Rich markup is safe here: only badge/stamp strings contain markup, and
    # content is rendered as a Text object (brackets never parsed).
    def _header_markup(self) -> str:
        label, color = ROLE_STYLES.get(self.message.role, ROLE_STYLES[MessageRole.SYSTEM])
        stamp = self.message.timestamp.strftime("%H:%M")
        header = f"[bold {color}]{label}[/] [dim]{stamp}[/]"
        if self.message.role == MessageRole.TOOL and not self.message.is_tool_card:
            header += f" [dim]{escape(self.message.tool_name or '')}[/]"
        return header

    def _content_text(self) -> Text:
        content = self.message.content or ""
        if self.message.tool_name and not self.message.is_tool_card:
            content = f"[{self.message.tool_name}] {content}"
        for tc in self.message.tool_calls:
            content += f"\n[tool call: {tc.get('name', 'unknown')}]"
        text = Text(content)
        text = apply_inline_markup(text)
        if self.message.streaming and self._cursor_on:
            text.append("▌", style=CSS_COLORS["accent"])
        return text

    # Kept for API compat with earlier revisions / external callers: returns
    # the plain (never-markup-parsed) content string.
    def _format_content(self) -> str:
        return self._content_text().plain

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), classes="msg-header")
        yield Static(self._content_text(), classes="content")

    def on_mount(self) -> None:
        self.add_class(ROLE_CSS_CLASS.get(self.message.role, "role-system"))

    def toggle_cursor(self) -> None:
        """Blink the streaming cursor (driven by ChatPanel's interval)."""
        self._cursor_on = not self._cursor_on
        self.refresh_content()

    def refresh_content(self) -> None:
        try:
            self.query_one(".content", Static).update(self._content_text())
        except Exception:
            pass  # not mounted yet — compose() will render fresh


class ToolCardWidget(Container):
    """Collapsible tool-call card in the transcript.

    Collapsed: `▸ Edit src/foo.py ✓ 0.4s`. Enter (when focused) or click
    expands to args summary, result excerpt and — for edit/write — the
    unified diff.
    """

    can_focus = True
    expanded = reactive(False)
    BINDINGS = [Binding("enter", "toggle_expand", "Expand")]

    def __init__(self, message: ChatMessage, **kwargs):
        super().__init__(**kwargs)
        self.message = message

    def _header_markup(self) -> str:
        msg = self.message
        arrow = "▾" if self.expanded else "▸"
        name = (msg.tool_name or "tool").capitalize()
        icon, color = TOOL_STATUS_ICONS.get(msg.tool_status or "running", TOOL_STATUS_ICONS["running"])
        parts = [f"[dim]{arrow}[/] [bold]{escape(name)}[/]"]
        target = _summarize_target(msg.tool_args)
        if target:
            parts.append(f"[dim]{escape(target)}[/]")
        parts.append(f"[{color}]{icon}[/]")
        if msg.tool_duration_ms is not None:
            parts.append(f"[dim]{msg.tool_duration_ms / 1000:.1f}s[/]")
        return " ".join(parts)

    def _args_markup(self) -> str:
        lines = []
        for key, value in (self.message.tool_args or {}).items():
            shown = _truncate(str(value), 120)
            lines.append(f"[dim]{escape(str(key))}[/] {escape(shown)}")
        return "\n".join(lines) if lines else "[dim]no arguments[/]"

    def _result_markup(self) -> str:
        if self.message.tool_error:
            return f"[{CSS_COLORS['error']}]✗ {escape(_truncate(self.message.tool_error, 300))}[/]"
        if self.message.tool_result:
            excerpt = _truncate(self.message.tool_result, 400)
            # Hide the raw diff from the excerpt — it gets its own block.
            if self.message.tool_diff and self.message.tool_diff in excerpt:
                excerpt = excerpt.replace(self.message.tool_diff, "").strip() or "(see diff below)"
            return f"[dim]{escape(excerpt)}[/]"
        return "[dim]…[/]"

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), classes="tool-card-header")
        with Container(classes="tool-card-body"):
            yield Static(self._args_markup(), classes="tool-card-args")
            if self.message.tool_diff:
                yield DiffBlock(self.message.tool_diff, classes="tool-card-diff")
            yield Static(self._result_markup(), classes="tool-card-result")

    def watch_expanded(self, expanded: bool) -> None:
        self.set_class(expanded, "expanded")
        try:
            self.query_one(".tool-card-header", Static).update(self._header_markup())
        except Exception:
            pass

    def action_toggle_expand(self) -> None:
        self.expanded = not self.expanded

    def on_click(self, event: events.Click) -> None:
        self.focus()
        self.action_toggle_expand()

    def set_status(
        self,
        status: str,
        result: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
        diff: str | None = None,
    ) -> None:
        """Live-update the card while/after the tool runs."""
        msg = self.message
        msg.tool_status = status
        if result is not None:
            msg.tool_result = result
        if error is not None:
            msg.tool_error = error
        if duration_ms is not None:
            msg.tool_duration_ms = duration_ms
        if diff is not None:
            msg.tool_diff = diff
        try:
            self.query_one(".tool-card-header", Static).update(self._header_markup())
            self.query_one(".tool-card-result", Static).update(self._result_markup())
        except Exception:
            pass
        if diff and not self.query(".tool-card-diff"):
            try:
                self.query_one(".tool-card-body").mount(DiffBlock(diff, classes="tool-card-diff"))
            except Exception:
                pass


class ChatPanel(VerticalScroll):
    """The hero panel: scrollable chat transcript."""

    def __init__(self, messages: list[ChatMessage] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._messages = messages or []
        self._card_seq = 0
        self._streaming_widget: ChatMessageWidget | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="messages-container")

    def on_mount(self) -> None:
        # Blink the streaming cursor while a message is being received.
        self.set_interval(0.5, self._tick_cursor)

    def _tick_cursor(self) -> None:
        if self._streaming_widget is not None:
            self._streaming_widget.toggle_cursor()

    def _mount(self, widget: Container) -> None:
        self.query_one("#messages-container").mount(widget)
        self.call_after_refresh(self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        self.scroll_end(animate=False)

    def add_message(self, message: ChatMessage) -> None:
        self._messages.append(message)
        widget = ChatMessageWidget(message)
        if message.streaming:
            self._streaming_widget = widget
        self._mount(widget)

    def add_tool_card(self, tool_name: str, args: dict[str, Any]) -> str:
        """Mount a collapsed tool-call card. Returns its card id."""
        self._card_seq += 1
        card_id = f"tc{self._card_seq}"
        msg = ChatMessage(
            role=MessageRole.TOOL,
            content="",
            tool_name=tool_name,
            tool_args=dict(args or {}),
            tool_status="running",
            card_id=card_id,
        )
        self._messages.append(msg)
        self._mount(ToolCardWidget(msg, id=f"tool-card-{card_id}"))
        return card_id

    def update_tool_card(
        self,
        card_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
        diff: str | None = None,
    ) -> None:
        try:
            widget = self.query_one(f"#tool-card-{card_id}", ToolCardWidget)
        except Exception:
            return  # card was cleared mid-run
        widget.set_status(status, result=result, error=error, duration_ms=duration_ms, diff=diff)
        self.call_after_refresh(self._scroll_to_end)

    def add_diff_message(self, title: str, diff: str, body: str | None = None) -> None:
        """System message pairing a heading with a rendered unified diff."""
        self._mount(DiffMessageWidget(title, diff, body))

    def end_streaming(self, final_content: str | None = None) -> None:
        """Stop the blink and lock in the final assistant text."""
        widget = self._streaming_widget
        if widget is None:
            return
        self._streaming_widget = None
        widget.message.streaming = False
        if final_content:
            widget.message.content = final_content
        widget.refresh_content()

    def clear(self) -> None:
        self._messages = []
        self._streaming_widget = None
        self._card_seq = 0
        self.query_one("#messages-container").remove_children()


class DiffMessageWidget(Container):
    """Approval-flow message: heading + unified diff + optional instruction."""

    def __init__(self, title: str, diff: str, body: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.diff = diff
        self.body = body

    def compose(self) -> ComposeResult:
        yield Static(f"[bold {CSS_COLORS['warning']}]{escape(self.title)}[/]", classes="msg-header")
        yield DiffBlock(self.diff)
        if self.body:
            yield Static(f"[dim]{escape(self.body)}[/]", classes="content")


class WelcomeCard(Container):
    """Boot-time session card: ASCII logo, version, quick tips."""

    def __init__(self, version: str = "0.1.0", **kwargs):
        super().__init__(**kwargs)
        self.version = version

    def compose(self) -> ComposeResult:
        logo = r"""
  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝"""
        yield Static(f"[bold {CSS_COLORS['accent']}]{logo}[/]", classes="welcome-logo")
        yield Static(
            f"[dim]v{self.version} · self-hosted neural coding agent[/]",
            classes="welcome-sub",
        )
        yield Static(
            "\n".join(
                [
                    f"[dim]›[/] [bold]/help[/] [dim]— every slash command[/]",
                    f"[dim]›[/] [bold]ctrl+t[/] [dim]— live tool activity[/]",
                    f"[dim]›[/] [bold]ctrl+g[/] [dim]— agent thinking[/]",
                    f"[dim]›[/] [dim]just start typing — the CMD:> prompt is live[/]",
                ]
            ),
            classes="welcome-tips",
        )


# --- side panels --------------------------------------------------------------


class CollapsiblePanel(VerticalScroll):
    """Side panel that hides entirely when empty and not pinned.

    ctrl+g/t/a toggle: hides a visible panel, pins a hidden one. When content
    arrives unpinned, the panel auto-shows until dismissed.
    """

    pinned = reactive(False)
    dismissed = reactive(False)

    def has_content(self) -> bool:
        return False

    @property
    def should_show(self) -> bool:
        if self.pinned:
            return True
        return self.has_content() and not self.dismissed

    def refresh_visibility(self) -> None:
        self.set_class(not self.should_show, "hidden")

    def toggle_visibility(self) -> None:
        if self.should_show:
            self.pinned = False
            self.dismissed = True
        else:
            self.pinned = True
            self.dismissed = False
        self.refresh_visibility()


class ThinkingBlock(Container):
    """A thinking step with expand/collapse (reactive watcher actually
    collapses it — the original had no watcher and the toggle did nothing)."""

    expanded = reactive(True)

    def __init__(self, step: ThinkingStep, **kwargs):
        super().__init__(**kwargs)
        self.step = step

    def _header_markup(self) -> str:
        arrow = "▾" if self.expanded else "▸"
        icon, color = TOOL_STATUS_ICONS.get(self.step.status, TOOL_STATUS_ICONS["done"])
        header = f"[dim]{arrow}[/] [{color}]{icon}[/] [dim]#{self.step.step_number}[/] "
        header += escape(self.step.description)
        if self.step.duration_ms is not None:
            header += f" [dim]{self.step.duration_ms / 1000:.1f}s[/]"
        return header

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), classes="step-header")
        if self.step.details:
            yield Static(f"[dim]{escape(self.step.details)}[/]", classes="step-details")

    def watch_expanded(self, expanded: bool) -> None:
        self.set_class(expanded, "expanded")
        try:
            self.query_one(".step-header", Static).update(self._header_markup())
            self.query_one(".step-details", Static).display = expanded
        except Exception:
            pass

    def on_click(self) -> None:
        self.expanded = not self.expanded

    def refresh_step(self) -> None:
        try:
            self.query_one(".step-header", Static).update(self._header_markup())
            details = self.query_one(".step-details", Static)
            details.update(f"[dim]{escape(self.step.details)}[/]")
            details.display = self.expanded and bool(self.step.details)
        except Exception:
            pass


class ThinkingPanel(CollapsiblePanel):
    """Panel showing structured thinking steps."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._steps: list[ThinkingStep] = []

    def compose(self) -> ComposeResult:
        yield Static("THINKING", classes="panel-header")
        yield Vertical(id="thinking-container")

    def has_content(self) -> bool:
        return bool(self._steps)

    def _update_header(self) -> None:
        try:
            self.query_one(".panel-header", Static).update(f"THINKING [dim]· {len(self._steps)}[/]")
        except Exception:
            pass

    def add_step(self, step: ThinkingStep) -> ThinkingBlock:
        self._steps.append(step)
        widget = ThinkingBlock(step)
        self.query_one("#thinking-container").mount(widget)
        self._update_header()
        return widget

    def update_step(self, step_id: str, status: str, details: str | None = None, duration_ms: int | None = None) -> None:
        for block in self.query(ThinkingBlock):
            if block.step.step_id == step_id:
                block.step.status = status
                if details is not None:
                    block.step.details = details
                if duration_ms is not None:
                    block.step.duration_ms = duration_ms
                block.refresh_step()
                return

    def clear(self) -> None:
        self._steps = []
        self.query_one("#thinking-container").remove_children()
        self._update_header()


class ToolStatusWidget(Static):
    """Displays a tool's execution status."""

    def __init__(self, tool: ToolInfo, **kwargs):
        super().__init__(**kwargs)
        self.tool = tool

    def render(self) -> str:
        icon_key = {
            ToolStatus.PENDING: "pending",
            ToolStatus.RUNNING: "running",
            ToolStatus.DONE: "done",
            ToolStatus.ERROR: "error",
        }.get(self.tool.status, "pending")
        icon, color = TOOL_STATUS_ICONS[icon_key]

        duration_str = f" [dim]{self.tool.duration_ms}ms[/]" if self.tool.duration_ms else ""
        return f"[{color}]{icon}[/] {escape(self.tool.name)}{duration_str}"


class ToolPanel(CollapsiblePanel):
    """Panel showing tool execution status."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tools: dict[str, ToolInfo] = {}

    def compose(self) -> ComposeResult:
        yield Static("TOOLS", classes="panel-header")
        yield Vertical(id="tools-container")

    def has_content(self) -> bool:
        return bool(self._tools)

    def update_tool(self, tool: ToolInfo) -> None:
        self._tools[tool.name] = tool
        container = self.query_one("#tools-container")

        # Update in place: remove()+remount with the same id races with the
        # async removal and raises DuplicateIds.
        existing = container.query(f"#tool-{tool.name}")
        if existing:
            existing.first().tool = tool
            existing.first().refresh()
        else:
            container.mount(ToolStatusWidget(tool, id=f"tool-{tool.name}"))
        self._update_header()

    def _update_header(self) -> None:
        try:
            self.query_one(".panel-header", Static).update(f"TOOLS [dim]· {len(self._tools)}[/]")
        except Exception:
            pass

    def clear(self) -> None:
        self._tools = {}
        self.query_one("#tools-container").remove_children()
        self._update_header()


class AgentCard(Static):
    """Displays an agent's status."""

    def __init__(self, agent: AgentInfo, **kwargs):
        super().__init__(**kwargs)
        self.agent = agent

    def render(self) -> str:
        icon, color = {
            AgentStatus.IDLE: ("○", "dim"),
            AgentStatus.THINKING: ("⟳", CSS_COLORS["accent"]),
            AgentStatus.TOOL_USE: ("▸", CSS_COLORS["warning"]),
            AgentStatus.WAITING: ("◐", CSS_COLORS["text_dim"]),
            AgentStatus.ERROR: ("✗", CSS_COLORS["error"]),
        }.get(self.agent.status, ("○", "dim"))

        task = f" [dim]{escape(_truncate(self.agent.current_task or '', 40))}[/]" if self.agent.current_task else ""
        return f"[{color}]{icon}[/] [bold]{escape(self.agent.name)}[/]{task}"


class AgentsPanel(CollapsiblePanel):
    """Panel showing active agents."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._agents: list[AgentInfo] = []

    def compose(self) -> ComposeResult:
        yield Static("AGENTS", classes="panel-header")
        yield Vertical(id="agents-container")

    def has_content(self) -> bool:
        return bool(self._agents)

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
        # Update in place (see ToolPanel.update_tool for why not remount).
        existing = container.query(f"#agent-{agent.name}")
        if existing:
            existing.first().agent = agent
            existing.first().refresh()
        else:
            container.mount(AgentCard(agent, id=f"agent-{agent.name}"))
        self._update_header()

    def _update_header(self) -> None:
        try:
            self.query_one(".panel-header", Static).update(f"AGENTS [dim]· {len(self._agents)}[/]")
        except Exception:
            pass

    def clear(self) -> None:
        self._agents = []
        self.query_one("#agents-container").remove_children()
        self._update_header()


class ProgressBar(Static):
    """ASCII-style progress bar."""

    percent = reactive(0.0)
    width = 20

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
    """Command input bar with history, completion and a dim keybind hint."""

    input_value = reactive("")
    history_index = reactive(-1)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._command_history: list[str] = []
        self._history_position = 0

    def compose(self) -> ComposeResult:
        with Horizontal(classes="input-row"):
            yield Static("CMD:>", classes="prompt")
            yield Input(
                placeholder="Ask anything… /help for commands",
                id="command-input",
                classes="input-field",
            )
        yield Static(
            f"[dim]ctrl+t[/] tools  [dim]ctrl+g[/] thinking  [dim]ctrl+a[/] agents  [dim]ctrl+l[/] clear  [dim]ctrl+p[/] commands",
            classes="input-hint",
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
        # CRITICAL: without stop(), this Input.Submitted message continues
        # bubbling to ancestors (e.g. the App's own on_input_submitted),
        # which previously caused every single message to be dispatched
        # and executed *twice*.
        event.stop()

        # Input's internal keybinding for Enter consumes the raw key event
        # before it would ever reach the App's on_key handler, so dropdown
        # selection has to be handled right here -- at the point where
        # Enter is genuinely processed -- rather than via a Key event that
        # never actually arrives while the dropdown is open.
        dropdown_selection = getattr(self.app, "get_dropdown_selection", lambda: None)()
        if dropdown_selection is not None:
            self.query_one("#command-input", Input).value = dropdown_selection + " "
            if hasattr(self.app, "hide_slash_dropdown"):
                self.app.hide_slash_dropdown()
            return

        command = event.value.strip()
        if command:
            self.add_to_history(command)
            self.post_message(CommandEntered(command))
        self.clear()

    def on_click(self, event: events.Click) -> None:
        """Force focus to the input field on click."""
        self.query_one("#command-input", Input).focus()

    def on_key(self, event: events.Key) -> bool:
        if event.key == "up":
            self.history_up()
            return True
        elif event.key == "down":
            self.history_down()
            return True
        return False


class CommandEntered(events.Message):
    """Message sent when user enters a command."""

    def __init__(self, command: str):
        super().__init__()
        self.command = command


class SlashCommandDropdown(Container):
    """Dropdown showing available slash commands when user types /."""

    COMMANDS: list[tuple[str, str]] = [
        ("/help", "Show help"),
        ("/init", "Scan this project and generate NEXUS.md context"),
        ("/clear", "Clear chat history"),
        ("/history", "Show command history"),
        ("/tools", "List available tools"),
        ("/model", "Switch AI model"),
        ("/provider", "Show/switch provider"),
        ("/session", "Show session info"),
        ("/facts", "Show stored facts"),
        ("/fact", "Add a fact"),
        ("/voice", "Enter voice mode"),
        ("/spawn", "Spawn a team agent"),
        ("/plan", "Enter plan mode"),
        ("/build", "Execute plan"),
        ("/safety", "Set safety mode"),
        ("/doctor", "Run diagnostics"),
        ("/mcp", "MCP server management"),
        ("/sync", "Sync status"),
        ("/learn", "Learning stats"),
        ("/improve", "Self-improvement"),
        ("/exit", "Exit Nexus"),
    ]

    def __init__(self, filter_text: str = "", **kwargs):
        super().__init__(id="slash-dropdown", **kwargs)
        self.filter_text = filter_text
        self.selected_index = 0
        self._filtered: list[tuple[str, str]] = []

    def on_mount(self):
        self._update_filtered()
        self._rebuild_items()

    def _update_filtered(self):
        f = self.filter_text.strip().lower()
        if not f or f == "/":
            self._filtered = list(self.COMMANDS)
        else:
            self._filtered = [
                (cmd, desc) for cmd, desc in self.COMMANDS
                if f in cmd.lower()
            ]

    def _rebuild_items(self):
        self.remove_children()
        for i, (cmd, desc) in enumerate(self._filtered[:10]):
            cls = "slash-item highlighted" if i == self.selected_index else "slash-item"
            self.mount(
                Horizontal(
                    Static(cmd, classes="name"),
                    Static(desc, classes="desc"),
                    classes=cls,
                )
            )

    def select_next(self):
        if self._filtered:
            self.selected_index = (self.selected_index + 1) % len(self._filtered)
            self._rebuild_items()

    def select_prev(self):
        if self._filtered:
            self.selected_index = (self.selected_index - 1) % len(self._filtered)
            self._rebuild_items()

    def get_selected(self) -> str | None:
        if self._filtered and 0 <= self.selected_index < len(self._filtered):
            return self._filtered[self.selected_index][0]
        return None


class SystemMonitor(Horizontal):
    """Top system monitoring bar showing real-time metrics."""

    def __init__(self, **kwargs):
        super().__init__(id="system-monitor", **kwargs)

    def on_mount(self):
        self.set_interval(1.0, self._refresh_metrics)
        self._metrics: list[str] = []
        self._refresh_metrics()

    def _refresh_metrics(self):
        import random

        from ..providers import get_manager

        # Provider status (works even without provider)
        manager = get_manager()
        if manager.configs:
            provider_name = manager.active_provider or "none"
            provider_status = f"CORE: {provider_name}"
        else:
            provider_status = "CORE: offline"

        # System info
        try:
            load = os.getloadavg()[0]
        except (AttributeError, OSError):
            load = random.uniform(0.5, 3.0)
        mem_info = ""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        total_kb = int(line.split()[1])
                        mem_info = f"RAM: {total_kb // 1024 // 1024}GB"
                        break
        except Exception:
            mem_info = "RAM: —"

        state = get_state_manager().state
        activity = "busy" if state.is_busy else "idle"
        activity_color = CSS_COLORS["accent"] if state.is_busy else "dim"

        self._metrics = [
            provider_status,
            f"LOAD: {load:.1f}",
            mem_info,
            f"[{activity_color}]● {activity}[/]",
        ]

        widgets = list(self.children)
        for i, text in enumerate(self._metrics):
            if i < len(widgets):
                widgets[i].update(text)

    def compose(self) -> ComposeResult:
        for _ in range(4):
            yield Static("", classes="system-metric")


class StatusBar(Static):
    """Bottom status line: pulse dot · model · provider · context % · tokens · cwd."""

    def __init__(self, version: str = "0.1.0", model: str = "", provider: str = "", project: str = "", termux: bool = False, battery: int = 100, **kwargs):
        super().__init__(**kwargs)
        self.version = version
        self.model = model
        self.provider = provider
        self.project = project
        self.termux = termux
        self.battery = battery
        self.message = ""
        self._pulse_state = 0

    def on_mount(self):
        self.set_interval(0.5, self._pulse)

    def _pulse(self):
        self._pulse_state = (self._pulse_state + 1) % 4
        self.refresh()

    def update(self, message: str = ""):
        self.message = message
        self.refresh()

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def render(self) -> str:
        state = get_state_manager().state
        pulse = "●" if self._pulse_state < 2 else "◌"
        pulse_color = CSS_COLORS["accent"] if state.is_busy else CSS_COLORS["text_dim"]

        parts = [f"[{pulse_color}]{pulse}[/] [dim]nexus[/]"]
        # model • provider, graceful "no provider" state
        if self.model:
            parts.append(f"[bold]{self.model}[/]")
        else:
            parts.append("[dim]no model[/]")
        parts.append(f"[dim]{self.provider or 'no provider'}[/]")

        # context-used %
        if state.max_context_tokens > 0 and state.context_tokens > 0:
            pct = min(1.0, state.context_tokens / state.max_context_tokens)
            color = CSS_COLORS["success"] if pct < 0.7 else CSS_COLORS["warning"] if pct < 0.9 else CSS_COLORS["error"]
            parts.append(f"[{color}]ctx {pct:.0%}[/]")
        if state.total_tokens:
            parts.append(f"[dim]{self._fmt_tokens(state.total_tokens)} tok[/]")

        cwd = self.project or os.path.basename(os.getcwd())
        parts.append(f"[dim]{cwd}[/]")

        if self.termux and self.battery >= 0:
            icon = "●" if self.battery > 50 else "◐" if self.battery > 20 else "○"
            parts.append(f"{icon} {self.battery}%")
        if self.message:
            parts.append(f"[dim]{escape(self.message)}[/]")

        return f" [dim]·[/] ".join(parts)
