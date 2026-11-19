"""TUI user preferences — persistent, keyboard-first settings.

Settings live in `~/.nexus/settings.json` and are the single source of
truth for everything the TUI renders dynamically: theme, timestamp format,
diff visibility, spinner style, compact mode, pinned panels. Load is
tolerant (missing/corrupt file → defaults, unknown keys ignored with a
note); saves are atomic (tmp file + os.replace) so a crash mid-write can
never corrupt the previous good config.

The SettingsScreen modal applies every change LIVE — it mutates the
singleton settings, persists immediately, and calls back into the app so
widgets re-render without a restart.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field, fields
from typing import Any, Callable

# Timestamp rendering modes: "24h" → 14:05, "12h" → 02:05 PM, "off" → hidden.
TIMESTAMP_MODES = ("24h", "12h", "off")
SPINNER_STYLES = ("braille", "dots", "pulse")

_SETTINGS_VERSION = 1


@dataclass
class TUISettings:
    """Everything the TUI needs to know about user preferences."""

    theme: str = "neural-cyan"
    timestamps: str = "24h"
    show_diffs: bool = True
    streaming_cursor: bool = True
    compact_mode: bool = False
    spinner_style: str = "braille"
    # Panel ids the user pinned open (e.g. ["tool-panel"]); restored at boot.
    pinned_panels: list[str] = field(default_factory=list)
    sidebar_animations: bool = True


def default_settings_path() -> str:
    """`~/.nexus/settings.json` — overridable for tests via env."""
    return os.environ.get(
        "NEXUS_TUI_SETTINGS_PATH",
        os.path.join(os.path.expanduser("~"), ".nexus", "settings.json"),
    )


def _coerce(value: Any, current: Any) -> Any:
    """Coerce a JSON value to the field's type; return original on mismatch."""
    if isinstance(current, bool):
        return value if isinstance(value, bool) else current
    if isinstance(current, int) and not isinstance(current, bool):
        return value if isinstance(value, int) and not isinstance(value, bool) else current
    if isinstance(current, str):
        return str(value) if isinstance(value, str) else current
    if isinstance(current, list):
        return list(value) if isinstance(value, list) else current
    return current


def load_settings(path: str | None = None) -> tuple[TUISettings, list[str]]:
    """Load settings from disk; tolerant of missing/corrupt files.

    Returns (settings, notes). Notes carry human-readable warnings ("unknown
    key X ignored") instead of failing the boot — a hand-edited or
    partially-written file must never keep the TUI from starting.
    """
    path = path or default_settings_path()
    settings = TUISettings()
    notes: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return settings, notes
    except Exception:
        notes.append(f"settings file unreadable, using defaults ({path})")
        return settings, notes

    if not isinstance(data, dict):
        notes.append("settings file is not an object, using defaults")
        return settings, notes

    known = {f.name: f for f in fields(TUISettings)}
    for key, value in data.items():
        if key == "version":
            continue  # writer metadata, not a setting — never flagged
        if key not in known:
            notes.append(f"unknown settings key ignored: {key}")
            continue
        current = getattr(settings, key)
        coerced = _coerce(value, current)
        if coerced != value:
            notes.append(f"settings key '{key}' had wrong type, reset to default")
            continue
        setattr(settings, key, coerced)

    # Enum-like fields: an out-of-vocabulary value falls back to default.
    if settings.timestamps not in TIMESTAMP_MODES:
        notes.append(f"timestamps '{settings.timestamps}' not recognized, reset to 24h")
        settings.timestamps = "24h"
    if settings.spinner_style not in SPINNER_STYLES:
        notes.append(f"spinner_style '{settings.spinner_style}' not recognized, reset to braille")
        settings.spinner_style = "braille"

    return settings, notes


def save_settings(settings: TUISettings, path: str | None = None) -> None:
    """Atomically persist settings (tmp file in the same dir + os.replace)."""
    path = path or default_settings_path()
    payload = {
        "version": _SETTINGS_VERSION,
        **{f.name: getattr(settings, f.name) for f in fields(TUISettings)},
    }
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        pass  # a failed save must never take the app down


# ── singleton accessor ───────────────────────────────────────────────────────

_settings_instance: TUISettings | None = None
_load_notes: list[str] = []


def get_settings() -> TUISettings:
    """Process-global settings singleton (loaded from disk on first touch)."""
    global _settings_instance, _load_notes
    if _settings_instance is None:
        _settings_instance, _load_notes = load_settings()
    return _settings_instance


def get_load_notes() -> list[str]:
    """Warnings from the last settings load (unknown keys, corrupt file…)."""
    return list(_load_notes)


def reset_settings_instance() -> None:
    """Drop the singleton (tests and theme reloads)."""
    global _settings_instance, _load_notes
    _settings_instance = None
    _load_notes = []


def save_settings_now() -> None:
    """Persist the current singleton to disk immediately."""
    if _settings_instance is not None:
        save_settings(_settings_instance)


def format_timestamp(dt, mode: str | None = None) -> str:
    """Render a datetime per the user's timestamps preference.

    "24h" → `14:05`, "12h" → `02:05 PM`, "off" → `` (callers hide the row).
    """
    mode = mode or get_settings().timestamps
    if mode == "off":
        return ""
    if mode == "12h":
        return dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%H:%M")


# ── the settings modal ───────────────────────────────────────────────────────
# Keyboard-first: ↑/↓ (or tab) moves, enter / ← → changes or toggles the
# focused row, esc closes. Every change applies to the live widgets through
# the on_change callback and is persisted immediately.


class SettingRow:
    """One editable row in the settings modal."""

    def __init__(self, group: str, label: str, getter: Callable, setter: Callable, choices: list | None = None):
        self.group = group
        self.label = label
        self.getter = getter
        self.setter = setter
        # choices=None means boolean toggle; otherwise cycle through values.
        self.choices = choices

    def cycle(self, direction: int = 1) -> None:
        """Advance (or rewind) the value and persist it."""
        current = self.getter()
        if self.choices is None:
            self.setter(not bool(current))
        else:
            try:
                idx = self.choices.index(current)
            except ValueError:
                idx = 0
            idx = (idx + direction) % len(self.choices)
            self.setter(self.choices[idx])


class SettingsModel:
    """Row model for the settings modal — pure logic, testable headless.

    The actual Textual screen (SettingsModal in app.py) renders these rows
    and forwards keystrokes; every mutation goes through SettingRow.cycle,
    which persists immediately and fires the on_change callback so the app
    can apply changes LIVE without a restart.
    """

    def __init__(self, settings: TUISettings | None = None, on_change: Callable[[], None] | None = None):
        self.settings = settings or get_settings()
        self.on_change = on_change
        self.rows: list[SettingRow] = self._build_rows()

    def _build_rows(self) -> list[SettingRow]:
        s = self.settings
        from .colors import THEME_NAMES

        def _set(name: str):
            def setter(value):
                setattr(s, name, value)
                save_settings_now()
                self._notify()
            return setter

        def _set_pinned(panel_id: str):
            def setter(value: bool):
                if value and panel_id not in s.pinned_panels:
                    s.pinned_panels.append(panel_id)
                elif not value and panel_id in s.pinned_panels:
                    s.pinned_panels.remove(panel_id)
                save_settings_now()
                self._notify()
            return setter

        return [
            SettingRow("Appearance", "theme", lambda: s.theme, _set("theme"), choices=list(THEME_NAMES)),
            SettingRow("Appearance", "timestamps", lambda: s.timestamps, _set("timestamps"), choices=list(TIMESTAMP_MODES)),
            SettingRow("Appearance", "spinner style", lambda: s.spinner_style, _set("spinner_style"), choices=list(SPINNER_STYLES)),
            SettingRow("Appearance", "sidebar animations", lambda: s.sidebar_animations, _set("sidebar_animations")),
            SettingRow("Behavior", "show diffs", lambda: s.show_diffs, _set("show_diffs")),
            SettingRow("Behavior", "streaming cursor", lambda: s.streaming_cursor, _set("streaming_cursor")),
            SettingRow("Behavior", "compact mode", lambda: s.compact_mode, _set("compact_mode")),
            SettingRow("Panels", "pin thinking panel", lambda: "thinking-panel" in s.pinned_panels,
                       _set_pinned("thinking-panel")),
            SettingRow("Panels", "pin tools panel", lambda: "tool-panel" in s.pinned_panels,
                       _set_pinned("tool-panel")),
            SettingRow("Panels", "pin agents panel", lambda: "agents-panel" in s.pinned_panels,
                       _set_pinned("agents-panel")),
        ]

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass  # a broken callback must not corrupt the save

    def group_order(self) -> list[str]:
        """Group labels in display order, only groups that have rows."""
        seen: list[str] = []
        for row in self.rows:
            if row.group not in seen:
                seen.append(row.group)
        return seen

    def format_row(self, row: SettingRow) -> str:
        """`show diffs            ✓` — label padded, current value inline."""
        current = row.getter()
        if isinstance(current, bool):
            value = "✓" if current else "—"
        else:
            value = str(current)
        label = row.label.ljust(20)
        return f"{label}{value}"
