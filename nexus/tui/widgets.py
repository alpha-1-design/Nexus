"""Reusable widgets for the Nexus TUI.

Design language: minimal chrome, information-dense transcript, dim metadata,
one accent color per role. Icons are limited to ▸ ✓ ✗ ⟳ ● ○ ● style glyphs —
no emoji clutter.
"""

import json
import os
import re
import subprocess
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Input, Static

from .colors import (
    CSS_COLORS,
    DEFAULT_BRAND_COLOR,
    apply_theme,
    format_provider_badge,
    get_current_theme,
    get_provider_brand_color,
    on_theme_change,
)
from .settings import (
    TUISettings,
    format_timestamp,
    get_settings,
)
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

# Status glyphs + live token colors. Colors are re-resolved on every theme
# swap by _refresh_theme_colors (below), so `TOOL_STATUS_ICONS["running"][1]`
# always reflects the active theme.
TOOL_STATUS_ICONS = {
    "pending": ("○", CSS_COLORS["text_dim"]),
    "running": ("⟳", CSS_COLORS["accent"]),
    "done": ("✓", CSS_COLORS["success"]),
    "error": ("✗", CSS_COLORS["error"]),
}

# Animated spinner frames for running tool cards, per spinner_style setting.
SPINNER_FRAMES: dict[str, tuple[str, ...]] = {
    "braille": ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    "dots": ("◐", "◓", "◑", "◒"),
    "pulse": ("▁", "▃", "▅", "▆", "▇", "▆", "▅", "▃"),
}

# Agent status glyphs (colors live, re-resolved on theme swap).
AGENT_STATUS_ICONS = {
    AgentStatus.IDLE: ("○", "text_dim"),
    AgentStatus.THINKING: ("⟳", "accent"),
    AgentStatus.TOOL_USE: ("▸", "warning"),
    AgentStatus.WAITING: ("◐", "text_dim"),
    AgentStatus.ERROR: ("✗", "error"),
}


def _refresh_theme_colors(theme_name: str | None = None) -> None:
    """Re-resolve the color-bearing module dicts after a theme swap.

    These dicts freeze hex values at import time (and hold tuple values the
    tests unpack), so instead of rebinding them we mutate their entries in
    place — every reader sees the active theme on its next render. Takes the
    (ignored) theme name argument so apply_theme can call it directly."""
    ROLE_STYLES[MessageRole.USER] = ("you", CSS_COLORS["user_message"])
    ROLE_STYLES[MessageRole.ASSISTANT] = ("nexus", CSS_COLORS["assistant_message"])
    ROLE_STYLES[MessageRole.SYSTEM] = ("sys", CSS_COLORS["system_message"])
    ROLE_STYLES[MessageRole.TOOL] = ("tool", CSS_COLORS["tool_call"])
    TOOL_STATUS_ICONS["pending"] = ("○", CSS_COLORS["text_dim"])
    TOOL_STATUS_ICONS["running"] = ("⟳", CSS_COLORS["accent"])
    TOOL_STATUS_ICONS["done"] = ("✓", CSS_COLORS["success"])
    TOOL_STATUS_ICONS["error"] = ("✗", CSS_COLORS["error"])


on_theme_change(_refresh_theme_colors)


def _status_icon(status: str | None) -> tuple[str, str]:
    """(glyph, color) for a tool-card/tool status, with the running glyph
    animated per the user's spinner_style setting."""
    key = status or "pending"
    if key == "running":
        style = get_settings().spinner_style
        frames = SPINNER_FRAMES.get(style, SPINNER_FRAMES["braille"])
        glyph = frames[ChatPanel.spinner_frame % len(frames)]
        return glyph, TOOL_STATUS_ICONS["running"][1]
    return TOOL_STATUS_ICONS.get(key, TOOL_STATUS_ICONS["pending"])


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


def format_provenance(provider: str | None, model: str | None, elapsed_ms: int | None = None) -> str:
    """Pure provenance-line formatter for assistant messages.

    `✦ Nexus · groq · llama-3.3-70b · 3.2s` — provider/model when known,
    elapsed seconds only when known. Empty string when there is nothing to
    show (so non-agent-looking system/echo messages stay clean).
    """
    parts = ["✦ Nexus"]
    if provider:
        parts.append(provider)
    if model:
        parts.append(model)
    if elapsed_ms is not None and elapsed_ms >= 0:
        parts.append(f"{elapsed_ms / 1000:.1f}s")
    if len(parts) == 1:
        return ""
    return " · ".join(parts)


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


def _diff_styles() -> dict[str, str]:
    """Diff line styles resolved against the LIVE theme tokens (semantic
    green/red in every theme, tinted per theme)."""
    return {
        "meta": f"dim {CSS_COLORS['diff_meta']}",
        "hunk": f"dim {CSS_COLORS['diff_meta']}",
        "add": CSS_COLORS["diff_add"],
        "del": CSS_COLORS["diff_del"],
        "ctx": "dim",
    }


def render_diff(diff_text: str) -> Text:
    """Parse standard unified diff text into a colorized rich Text.

    `+` lines green, `-` lines red, `@@`/`---`/`+++` dim accent. Long lines
    wrap naturally inside the surrounding Static, so no manual wrapping here.
    """
    styles = _diff_styles()
    text = Text()
    for line in diff_text.splitlines() or [""]:
        text.append(line, styles[classify_diff_line(line)])
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
    a left accent border color-coded per role.

    Assistant entries additionally carry a dim provenance row —
    `✦ Nexus · provider/model · 3.2s` — like a real agent run log. User
    messages stay compact; system messages are untouched.
    """

    def __init__(self, message: ChatMessage, **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self._cursor_on = True

    # Rich markup is safe here: only badge/stamp strings contain markup, and
    # content is rendered as a Text object (brackets never parsed).
    def _header_markup(self) -> str:
        label, color = ROLE_STYLES.get(self.message.role, ROLE_STYLES[MessageRole.SYSTEM])
        header = f"[bold {color}]{label}[/]"
        settings = get_settings()
        if not settings.compact_mode:
            stamp = format_timestamp(self.message.timestamp, settings.timestamps)
            if stamp:
                header += f" [dim]{stamp}[/]"
        if self.message.role == MessageRole.TOOL and not self.message.is_tool_card:
            header += f" [dim]{escape(self.message.tool_name or '')}[/]"
        return header

    def _provenance_markup(self) -> str | None:
        """`✦ Nexus · groq · llama-3.3-70b · 3.2s` — provenance line for
        assistant messages, in the assistant role color, dim body."""
        if self.message.role != MessageRole.ASSISTANT:
            return None
        text = format_provenance(
            self.message.provider,
            self.message.model,
            self.message.elapsed_ms,
        )
        if not text:
            return None
        color = ROLE_STYLES[MessageRole.ASSISTANT][1]
        return f"[dim {color}]{escape(text)}[/]"

    def _content_text(self) -> Text:
        content = self.message.content or ""
        if self.message.tool_name and not self.message.is_tool_card:
            content = f"[{self.message.tool_name}] {content}"
        for tc in self.message.tool_calls:
            content += f"\n[tool call: {tc.get('name', 'unknown')}]"
        text = Text(content)
        text = apply_inline_markup(text)
        if self.message.streaming and self._cursor_on and get_settings().streaming_cursor:
            text.append("▌", style=CSS_COLORS["accent"])
        return text

    # Kept for API compat with earlier revisions / external callers: returns
    # the plain (never-markup-parsed) content string.
    def _format_content(self) -> str:
        return self._content_text().plain

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), classes="msg-header")
        provenance = self._provenance_markup()
        if provenance and not get_settings().compact_mode:
            yield Static(provenance, classes="msg-provenance")
        yield Static(self._content_text(), classes="content")

    def on_mount(self) -> None:
        self.add_class(ROLE_CSS_CLASS.get(self.message.role, "role-system"))
        if get_settings().compact_mode:
            self.add_class("compact")

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
        icon, color = _status_icon(msg.tool_status or "running")
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
            if self.message.tool_diff and get_settings().show_diffs:
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
        if diff and get_settings().show_diffs and not self.query(".tool-card-diff"):
            try:
                self.query_one(".tool-card-body").mount(DiffBlock(diff, classes="tool-card-diff"))
            except Exception:
                pass


class ChatPanel(VerticalScroll):
    """The hero panel: scrollable chat transcript.

    Drives two cheap animations: the streaming cursor blink and the live
    spinner on running tool cards (frame index shared class-wide so one
    interval advances every card in lockstep).
    """

    spinner_frame: int = 0

    def __init__(self, messages: list[ChatMessage] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._messages = messages or []
        self._card_seq = 0
        self._streaming_widget: ChatMessageWidget | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="messages-container")

    def on_mount(self) -> None:
        # Blink the streaming cursor while a message is being received, and
        # animate the glyph of any running tool card.
        self.set_interval(0.5, self._tick_cursor)
        self.set_interval(0.12, self._tick_spinners)

    def _tick_cursor(self) -> None:
        if self._streaming_widget is not None:
            self._streaming_widget.toggle_cursor()

    def _tick_spinners(self) -> None:
        """Advance the shared spinner frame and re-render running cards."""
        ChatPanel.spinner_frame = (ChatPanel.spinner_frame + 1) % 64
        for card in self.query(ToolCardWidget):
            if card.message.tool_status == "running":
                try:
                    card.query_one(".tool-card-header", Static).update(card._header_markup())
                except Exception:
                    pass  # card mid-mount

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

    def rerender(self) -> None:
        """Re-mount every transcript message in order.

        Header/provenance/diff markup bakes colors and settings into Statics
        at compose time, so live theme/settings changes re-mount instead of
        trying to patch every child. Tool-card expansion state is preserved;
        a streaming message stays the live streaming widget.
        """
        container = self.query_one("#messages-container")
        expanded: dict[str | None, bool] = {}
        for card in self.query(ToolCardWidget):
            expanded[card.message.card_id] = card.expanded
        container.remove_children()
        self._streaming_widget = None
        for msg in self._messages:
            if msg.is_tool_card:
                widget = ToolCardWidget(msg, id=f"tool-card-{msg.card_id}")
                if expanded.get(msg.card_id):
                    widget.expanded = True
            else:
                widget = ChatMessageWidget(msg)
                if msg.streaming:
                    self._streaming_widget = widget
            container.mount(widget)
        self.call_after_refresh(self._scroll_to_end)


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
    """Boot-time session card: ASCII logo, provider badge, version, tips.

    Provider-aware identity: the logo tints with the connected provider's
    brand color; with no provider configured it falls back to the current
    theme's `logo_tint` token (neutral cyan on the default theme). A live
    theme swap re-tints the card in place via refresh_theme().
    """

    def __init__(self, version: str = "0.1.0", provider: str = "", model: str = "", **kwargs):
        super().__init__(**kwargs)
        self.version = version
        if not provider:
            # Auto-detect from the provider manager (same source the status
            # bar uses) so callers don't have to plumb it through.
            provider, model = _detect_active_provider()
        self.provider = provider
        self.model = model

    def compose(self) -> ComposeResult:
        self.brand_color, self.badge = resolve_welcome_identity(self.provider, self.model)
        logo = r"""
  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝"""
        yield Static(f"[bold {self.brand_color}]{logo}[/]", classes="welcome-logo")
        if self.badge:
            yield Static(
                f"[bold {self.brand_color}]{escape(self.badge)}[/]",
                classes="welcome-badge",
            )
        yield Static(
            f"[dim]v{self.version} · self-hosted neural coding agent[/]",
            classes="welcome-sub",
        )
        yield Static(
            "\n".join(
                [
                    f"[dim]›[/] [bold]/help[/] [dim]— every slash command[/]",
                    f"[dim]›[/] [bold]ctrl+s[/] [dim]— settings & themes[/]",
                    f"[dim]›[/] [bold]ctrl+t[/] [dim]— live tool activity[/]",
                    f"[dim]›[/] [bold]ctrl+g[/] [dim]— agent thinking[/]",
                    f"[dim]›[/] [dim]just start typing — the CMD:> prompt is live[/]",
                ]
            ),
            classes="welcome-tips",
        )

    def refresh_theme(self) -> None:
        """Re-resolve the logo tint after a live theme swap."""
        self.brand_color, self.badge = resolve_welcome_identity(self.provider, self.model)
        try:
            logo = self.query_one(".welcome-logo", Static)
            statics = list(self.query("Static"))
            if statics:
                # compose() renders the logo as the first Static; swap only
                # its color runs, keeping the ASCII art untouched.
                text = Text.from_markup(str(logo.render()))
                logo.update(f"[bold {self.brand_color}]{text.plain}[/]")
            if self.badge and self.query(".welcome-badge"):
                self.query_one(".welcome-badge", Static).update(
                    f"[bold {self.brand_color}]{escape(self.badge)}[/]"
                )
        except Exception:
            pass  # not mounted yet


def _detect_active_provider() -> tuple[str, str]:
    """(provider, model) for the configured provider, same source as the
    status bar; ("", "") when nothing is configured."""
    try:
        from ..providers import get_manager

        manager = get_manager()
        if getattr(manager, "configs", None):
            provider = manager.active_provider or ""
            config = manager.get_provider_config(provider)
            return provider, (config.model if config else "")
    except Exception:
        pass
    return "", ""


def resolve_welcome_identity(provider: str = "", model: str = "") -> tuple[str, str | None]:
    """Pure selection logic for the welcome card's provider-aware identity.

    Returns (brand_color_hex, badge_text_or_None). A connected provider
    tints the identity with its brand color in EVERY theme; with no provider
    configured the logo follows the current theme's `logo_tint` token; an
    unknown provider name keeps the neutral accent. No pixel rendering
    involved — also the unit-tested seam.
    """
    if not provider:
        return CSS_COLORS["logo_tint"], None
    return get_provider_brand_color(provider), format_provider_badge(provider, model)


# ── boot checklist (agentic order of service) ────────────────────────────────


def _count_mcp_servers(path: str | None = None) -> int:
    """MCP servers configured in ~/.nexus/mcp-servers.json (0 when absent)."""
    path = path or os.path.join(os.path.expanduser("~"), ".nexus", "mcp-servers.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0
    if isinstance(data, dict):
        return len(data)
    if isinstance(data, list):
        return len(data)
    return 0


def _discovery_boot_summary() -> dict[str, Any] | None:
    """Richer boot counts from nexus.discovery.boot_summary() when the
    parallel-built module exists at runtime; None otherwise. The seam is
    defensive ON PURPOSE: ImportError, bad payloads and exploding calls all
    fall back to the local counts below — boot never waits on it."""
    try:
        from ..discovery import boot_summary  # type: ignore[attr-defined]
    except Exception:
        return None
    try:
        summary = boot_summary()
        return summary if isinstance(summary, dict) else None
    except Exception:
        return None


def collect_boot_checks() -> list[dict[str, str]]:
    """Probe every subsystem the TUI leans on. Returns ordered dicts:
    {name, status: "ok"|"fail"|"none", detail}. Every probe is wrapped so a
    misbehaving subsystem degrades to a ✗ line instead of crashing boot.
    `nexus.discovery.boot_summary()` (when present at runtime) overrides the
    counts with richer data; local probes remain the fallback."""
    summary = _discovery_boot_summary()

    def count_of(key: str, local: int) -> int:
        value = summary.get(key) if summary else None
        return value if isinstance(value, int) else local

    checks: list[dict[str, str]] = []

    # memory
    try:
        from ..memory import get_memory

        facts = len(get_memory().get_all_facts())
        checks.append({"name": "memory", "status": "ok", "detail": f"{facts} facts"})
    except Exception as e:
        checks.append({"name": "memory", "status": "fail", "detail": str(e)[:60]})

    # skills
    try:
        from ..skills.matcher import get_skill_matcher

        matcher = get_skill_matcher()
        if not matcher.is_indexed:
            matcher.ensure_index()
        checks.append({
            "name": "skills",
            "status": "ok",
            "detail": f"{count_of('skills', matcher.skill_count)} loaded",
        })
    except Exception as e:
        checks.append({"name": "skills", "status": "fail", "detail": str(e)[:60]})

    # tools
    try:
        from ..tools import get_registry

        checks.append({
            "name": "tools",
            "status": "ok",
            "detail": f"{count_of('tools', len(get_registry().list_all()))} registered",
        })
    except Exception as e:
        checks.append({"name": "tools", "status": "fail", "detail": str(e)[:60]})

    # mcp servers configured on disk
    try:
        mcp_count = count_of("mcp", _count_mcp_servers())
        checks.append({"name": "mcp", "status": "ok", "detail": f"{mcp_count} servers"})
    except Exception as e:
        checks.append({"name": "mcp", "status": "fail", "detail": str(e)[:60]})

    # provider (reuse the welcome card's detector — same source of truth)
    try:
        provider, _model = _detect_active_provider()
        if provider:
            checks.append({"name": "provider", "status": "ok", "detail": provider})
        else:
            checks.append({"name": "provider", "status": "none", "detail": "none configured (/auth)"})
    except Exception as e:
        checks.append({"name": "provider", "status": "fail", "detail": str(e)[:60]})

    return checks


def format_boot_line(check: dict[str, str], glyph: str = "◆") -> str:
    """One checklist line: `◆ memory … ✓ 14 facts` / `✗ skills … <reason>`
    (amber) / `— provider … none configured (/auth)`. Pure → unit-tested."""
    from .colors import CSS_COLORS

    name = check.get("name", "?")
    status = check.get("status", "ok")
    detail = check.get("detail", "")
    if status == "fail":
        reason = detail or "unavailable"
        return f"[{CSS_COLORS['warning']}]✗ {escape(name)} … {escape(reason)}[/]"
    if status == "none":
        return f"[{CSS_COLORS['text_dim']}]— {escape(name)} … {escape(detail)}[/]"
    body = f"[{CSS_COLORS['accent']}]{glyph}[/] [dim]{escape(name)} … [/][{CSS_COLORS['success']}]✓[/]"
    if detail:
        body += f" [dim]{escape(detail)}[/]"
    return body


class BootChecklist(Static):
    """Animated subsystem checklist rendered above the welcome card on boot.

    One line every BOOT_DELAY seconds (Textual interval — pilot-safe), each
    a `◆ <name> … ✓ <detail>` line; failures show `✗ <reason>` in amber and
    NEVER crash boot. Stays visible above the welcome card as a run log.
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._checks: list[dict[str, str]] = []
        self._revealed = 0
        self._done = False

    def on_mount(self) -> None:
        self.add_class("boot-checklist")
        self.set_interval(0.15, self._tick)
        self._tick()  # first line immediately

    def _tick(self) -> None:
        if self._done:
            return
        if not self._checks:
            try:
                self._checks = collect_boot_checks()
            except Exception:
                self._checks = [{"name": "boot", "status": "fail", "detail": "probes unavailable"}]
        self._revealed = min(self._revealed + 1, len(self._checks))
        if self._revealed >= len(self._checks):
            self._done = True
        self._render_lines()

    def _render_lines(self) -> None:
        glyph = "◆"
        lines = [format_boot_line(c, glyph) for c in self._checks[: self._revealed]]
        self.update("\n".join(lines) if lines else "")


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
            running = any(step.status == "running" for step in self._steps)
            suffix = f"  [{CSS_COLORS['accent']}]◆ working…[/]" if running else ""
            self.query_one(".panel-header", Static).update(f"THINKING [dim]· {len(self._steps)}[/]{suffix}")
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
                self._update_header()
                return
        self._update_header()

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
        ("/settings", "Open settings (theme, timestamps, spinners)"),
        ("/theme", "Switch theme (cycle or /theme <name>)"),
        ("/skills", "Match bundled skills for a task"),
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
        ("/plugins", "Tool plugin cards (--reload rescans)"),
        ("/discover", "Environment + project sniff"),
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


# ── git helpers ──────────────────────────────────────────────────────────────

_git_branch_cache: dict[str, str] = {}


def get_git_branch(cwd: str | None = None, timeout: float = 2.0) -> str:
    """Branch name for `cwd` via cached `git rev-parse --abbrev-ref HEAD`.

    `—` when outside a repo, on timeout, or when git is missing. The result
    is cached per directory: the status bar re-renders on every pulse tick,
    and forking git 2×/s on a phone is exactly the kind of tax we avoid.
    """
    cwd = cwd or os.getcwd()
    if cwd in _git_branch_cache:
        return _git_branch_cache[cwd]
    branch = "—"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            branch = (result.stdout or "").strip() or "—"
    except Exception:
        branch = "—"
    _git_branch_cache[cwd] = branch
    return branch


def clear_git_branch_cache() -> None:
    """Drop the cache (tests; and a cheap way to re-probe after /clear)."""
    _git_branch_cache.clear()


class StatusBar(Static):
    """Bottom status line: pulse dot · model · provider · context % · tokens
    · git branch · session · cwd — one line even at Termux widths, with the
    flexible tail parts truncated gracefully when the budget runs out."""

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
        # The status-bar heartbeat: a live pulse glyph on a cheap 0.5s
        # set_interval. Honors the sidebar_animations setting — with
        # animations off the dot stays solid and skips the per-tick
        # refresh tax on phones.
        if not get_settings().sidebar_animations:
            return
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

    def _render_parts(self) -> list[tuple[str, str, bool]]:
        """(raw_text, markup_template, flexible) parts for the status line.

        Flexible parts (session, cwd, message) are truncated first when the
        line exceeds the terminal width; everything else stays intact.
        """
        state = get_state_manager().state
        pulse = "●" if self._pulse_state < 2 else "◌"
        pulse_color = CSS_COLORS["accent"] if state.is_busy else CSS_COLORS["text_dim"]

        parts: list[tuple[str, str, bool]] = [
            (pulse, f"[{pulse_color}]{{}}[/]", False),
            ("nexus", "[dim]{}[/]", False),
        ]
        # model • provider, graceful "no provider" state. When a provider is
        # configured, its NAME renders in the provider's brand color with a
        # matching dot — the identity follows the connected brain.
        if self.model:
            # Flexible: a long model id is truncated LAST (after session/cwd)
            # so a 43-char model never pushes the line past the Termux width.
            parts.append((self.model, "[bold]{}[/]", True))
        else:
            parts.append(("no model", "[dim]{}[/]", False))
        if self.provider:
            brand = get_provider_brand_color(self.provider)
            parts.append(("●", f"[{brand}]{{}}[/]", False))
            parts.append((self.provider, f"[{brand}]{{}}[/]", True))
        else:
            parts.append(("no provider", "[dim]{}[/]", False))

        # context-used %
        if state.max_context_tokens > 0 and state.context_tokens > 0:
            pct = min(1.0, state.context_tokens / state.max_context_tokens)
            color = CSS_COLORS["success"] if pct < 0.7 else CSS_COLORS["warning"] if pct < 0.9 else CSS_COLORS["error"]
            parts.append((f"ctx {pct:.0%}", f"[{color}]{{}}[/]", False))
        if state.total_tokens:
            parts.append((f"{self._fmt_tokens(state.total_tokens)} tok", "[dim]{}[/]", False))

        # git branch — '—' outside a repo (cached; a fork per pulse would
        # tax a phone). Rendered dim and only when inside a repo.
        branch = get_git_branch()
        if branch and branch != "—":
            parts.append((f"⎇ {branch}", "[dim]{}[/]", True))

        # short session id, when a session exists
        if state.session_id:
            parts.append((f"#{state.session_id[:8]}", "[dim]{}[/]", True))

        cwd = self.project or os.path.basename(os.getcwd())
        if cwd:
            parts.append((cwd, "[dim]{}[/]", True))

        if self.termux and self.battery >= 0:
            icon = "●" if self.battery > 50 else "◐" if self.battery > 20 else "○"
            parts.append((f"{icon} {self.battery}%", "{}", False))
        if self.message:
            parts.append((self.message, "[dim]{}[/]", True))

        return parts

    @staticmethod
    def _join_fitted(parts: list[tuple[str, str, bool]], max_width: int) -> str:
        """Join parts with ' · '; over budget, truncate flexible parts from
        the tail (with an ellipsis) until it fits, then drop the rest.

        max_width <= 0 means unlimited (unmounted bars in unit tests).
        """
        def assemble(parts: list[tuple[str, str, bool]]) -> str:
            return " · ".join(t.format(escape(p)) for p, t, _ in parts)

        if max_width <= 0:
            return assemble(parts)
        sep_len = 3
        budget = max_width - 4  # '#status-bar' padding: 0 2 (both sides)

        def total(parts):
            if not parts:
                return 0
            return sum(len(p) for p, _, _ in parts) + sep_len * (len(parts) - 1)

        parts = list(parts)
        while total(parts) > budget:
            # Find the last flexible part; shrink it, or drop it if tiny.
            for i in range(len(parts) - 1, -1, -1):
                raw, template, flexible = parts[i]
                if not flexible:
                    continue
                overflow = total(parts) - budget
                keep = len(raw) - overflow - 1
                if keep >= 4:
                    parts[i] = (raw[:keep].rstrip() + "…", template, True)
                else:
                    parts.pop(i)
                break
            else:
                break  # nothing flexible left — let it overflow
        return assemble(parts)

    def render(self) -> str:
        parts = self._render_parts()
        width = self.size.width if self.size and self.size.width > 0 else 0
        return self._join_fitted(parts, width)
