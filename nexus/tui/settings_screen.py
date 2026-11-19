"""Keyboard-first settings modal for the Nexus TUI.

Pushed by `/settings` and the ctrl+s binding. Navigation is arrows/tab,
enter (or ←/→) changes the focused setting, esc closes. Every change goes
through the SettingsModel, which persists immediately and fires the app's
live-apply callback — no restart, ever.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .settings import SettingsModel


class SettingsModal(ModalScreen):
    """Settings + themes modal: grouped rows, inline current values."""

    BINDINGS = [
        # priority=True: the App itself binds a priority escape (dropdown
        # dismiss) — without matching priority here, the app would swallow
        # Esc while the modal is open and the modal could never be closed.
        Binding("escape", "close", "Close", priority=True),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("enter", "change_value", "Change"),
        ("left", "prev_value", "Prev"),
        ("right", "next_value", "Next"),
        Binding("tab", "cursor_down", show=False),
        Binding("shift+tab", "cursor_up", show=False),
    ]

    def __init__(self, on_change=None, **kwargs):
        super().__init__(**kwargs)
        self.model = SettingsModel(on_change=on_change)
        self.selected = 0
        self._row_statics: list[Static] = []
        self._row_index: dict[Static, int] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-body"):
            yield Static("SETTINGS  ·  ↑↓ move · enter/←→ change · esc close",
                         classes="panel-header settings-title")
            with VerticalScroll(id="settings-rows"):
                for group in self.model.group_order():
                    yield Static(group.upper(), classes="settings-group")
                    for row in [r for r in self.model.rows if r.group == group]:
                        yield Static(self.model.format_row(row), classes="settings-row")
            yield Static("changes apply live and are saved to ~/.nexus/settings.json",
                         classes="settings-hint")

    def on_mount(self) -> None:
        self._row_statics = list(self.query(".settings-row"))
        self._row_index = {static: i for i, static in enumerate(self._row_statics)}
        self._refresh_rows()

    # ── rendering ────────────────────────────────────────────────────────

    def _refresh_rows(self) -> None:
        for i, static in enumerate(self._row_statics):
            static.set_class(i == self.selected, "selected")
            static.update(self.model.format_row(self.model.rows[i]))

    # ── key actions ──────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        if self._row_statics:
            self.selected = min(self.selected + 1, len(self.model.rows) - 1)
            self._refresh_rows()

    def action_cursor_up(self) -> None:
        if self._row_statics:
            self.selected = max(self.selected - 1, 0)
            self._refresh_rows()

    def action_change_value(self) -> None:
        self._cycle(1)

    def action_next_value(self) -> None:
        self._cycle(1)

    def action_prev_value(self) -> None:
        self._cycle(-1)

    def _cycle(self, direction: int) -> None:
        if not self._row_statics:
            return
        row = self.model.rows[self.selected]
        try:
            row.cycle(direction)
        except Exception:
            pass  # a broken setter must never close the modal
        self._refresh_rows()

    def action_close(self) -> None:
        self.dismiss(None)
