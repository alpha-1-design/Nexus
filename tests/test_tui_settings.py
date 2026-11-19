"""Tests for TUI user preferences — persistence, tolerance, live model.

The settings singleton and theme token map are process-global, so every
test here runs under fixtures that snapshot and restore them.
"""

import dataclasses
import json
import os

import pytest

from nexus.tui.colors import (
    CSS_COLORS,
    DEFAULT_THEME,
    THEME_NAMES,
    apply_theme,
    get_current_theme,
)
from nexus.tui.settings import (
    SPINNER_STYLES,
    TIMESTAMP_MODES,
    TUISettings,
    SettingsModel,
    default_settings_path,
    format_timestamp,
    get_load_notes,
    get_settings,
    load_settings,
    reset_settings_instance,
    save_settings,
)


@pytest.fixture(autouse=True)
def _isolated_settings_and_theme(tmp_path, monkeypatch):
    """Point settings at a per-test file, reset the singleton, and restore
    the theme + singleton contents afterwards (they are process-global)."""
    monkeypatch.setenv("NEXUS_TUI_SETTINGS_PATH", str(tmp_path / "settings.json"))
    reset_settings_instance()
    snapshot = dataclasses.asdict(get_settings())
    theme_before = get_current_theme()
    yield
    current = get_settings()
    for f in dataclasses.fields(TUISettings):
        setattr(current, f.name, snapshot[f.name])
    apply_theme(theme_before if theme_before in THEME_NAMES else DEFAULT_THEME)
    reset_settings_instance()


def _write(tmp_path, payload) -> str:
    path = tmp_path / "settings.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestSettingsRoundtrip:
    def test_missing_file_returns_defaults(self, tmp_path):
        settings, notes = load_settings(str(tmp_path / "absent.json"))
        assert settings == TUISettings()
        assert notes == []

    def test_save_then_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "settings.json")
        settings = TUISettings(
            theme="synthwave",
            timestamps="12h",
            show_diffs=False,
            streaming_cursor=False,
            compact_mode=True,
            spinner_style="dots",
            pinned_panels=["tool-panel", "agents-panel"],
            sidebar_animations=False,
        )
        save_settings(settings, path)
        loaded, notes = load_settings(path)
        assert loaded == settings
        assert notes == []
        # Every field survives the round trip.
        for f in dataclasses.fields(TUISettings):
            assert getattr(loaded, f.name) == getattr(settings, f.name)

    def test_save_is_atomic_no_tmp_leftovers(self, tmp_path):
        path = str(tmp_path / "settings.json")
        save_settings(TUISettings(), path)
        assert os.path.exists(path)
        leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
        assert leftovers == []
        # The written file parses and carries the dataclass fields.
        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["theme"] == "neural-cyan"
        assert "timestamps" in data

    def test_corrupt_file_tolerated_with_defaults(self, tmp_path):
        path = _write(tmp_path, "{ this is not json ][")
        settings, notes = load_settings(path)
        assert settings == TUISettings()
        assert any("unreadable" in n or "defaults" in n for n in notes)

    def test_empty_file_tolerated(self, tmp_path):
        path = _write(tmp_path, "")
        settings, notes = load_settings(path)
        assert settings == TUISettings()
        assert notes  # something was said about the broken file

    def test_non_object_file_tolerated(self, tmp_path):
        path = _write(tmp_path, [1, 2, 3])
        settings, notes = load_settings(path)
        assert settings == TUISettings()
        assert any("not an object" in n for n in notes)

    def test_unknown_keys_ignored_with_note(self, tmp_path):
        path = _write(tmp_path, {"theme": "mono", "warp_drive": True})
        settings, notes = load_settings(path)
        assert settings.theme == "mono"  # known keys still applied
        assert any("warp_drive" in n and "unknown" in n.lower() for n in notes)

    def test_wrong_typed_value_reset_with_note(self, tmp_path):
        path = _write(tmp_path, {"show_diffs": "yes-please"})
        settings, notes = load_settings(path)
        assert settings.show_diffs is True
        assert any("show_diffs" in n for n in notes)

    def test_out_of_vocabulary_enum_reset(self, tmp_path):
        path = _write(tmp_path, {"timestamps": "solar", "spinner_style": "disco"})
        settings, notes = load_settings(path)
        assert settings.timestamps == "24h"
        assert settings.spinner_style == "braille"
        assert any("timestamps" in n for n in notes)
        assert any("spinner_style" in n for n in notes)

    def test_pinned_panels_survive_roundtrip(self, tmp_path):
        path = str(tmp_path / "settings.json")
        settings = TUISettings(pinned_panels=["thinking-panel"])
        save_settings(settings, path)
        loaded, _ = load_settings(path)
        assert loaded.pinned_panels == ["thinking-panel"]


class TestSettingsSingleton:
    def test_get_settings_is_singleton(self):
        first = get_settings()
        assert get_settings() is first

    def test_singleton_reads_env_path(self, tmp_path):
        path = _write(tmp_path, {"theme": "amber-crt"})
        reset_settings_instance()
        assert get_settings().theme == "amber-crt"

    def test_load_notes_survive_singleton(self, tmp_path):
        path = _write(tmp_path, {"nope": 1})
        reset_settings_instance()
        get_settings()  # loads via env path
        assert any("nope" in n for n in get_load_notes())

    def test_default_settings_path_under_home(self, monkeypatch):
        monkeypatch.delenv("NEXUS_TUI_SETTINGS_PATH", raising=False)
        assert default_settings_path().endswith(os.path.join(".nexus", "settings.json"))


class TestTimestampFormatting:
    def test_24h_format(self):
        from datetime import datetime

        dt = datetime(2026, 1, 1, 14, 30, 5)
        assert format_timestamp(dt, "24h") == "14:30"

    def test_12h_format(self):
        from datetime import datetime

        assert format_timestamp(datetime(2026, 1, 1, 14, 30), "12h") == "2:30 PM"
        assert format_timestamp(datetime(2026, 1, 1, 9, 5), "12h") == "9:05 AM"

    def test_off_format_is_empty(self):
        from datetime import datetime

        assert format_timestamp(datetime(2026, 1, 1, 14, 30), "off") == ""

    def test_default_reads_singleton(self):
        from datetime import datetime

        get_settings().timestamps = "12h"
        assert format_timestamp(datetime(2026, 1, 1, 14, 30)) == "2:30 PM"

    def test_modes_vocab(self):
        assert TIMESTAMP_MODES == ("24h", "12h", "off")
        assert SPINNER_STYLES == ("braille", "dots", "pulse")


class TestSettingsModel:
    """The headless row model behind the modal — grouping, cycling, live save."""

    def _model(self) -> SettingsModel:
        return SettingsModel(on_change=lambda: None)

    def test_group_order_and_content(self):
        model = self._model()
        assert model.group_order() == ["Appearance", "Behavior", "Panels"]
        labels = [r.label for r in model.rows]
        assert "theme" in labels and "timestamps" in labels
        assert "show diffs" in labels and "compact mode" in labels
        assert "pin tools panel" in labels

    def test_theme_row_cycles_through_all_themes(self):
        model = self._model()
        theme_row = model.rows[0]
        assert theme_row.choices == list(THEME_NAMES)
        seen = []
        for _ in THEME_NAMES:
            theme_row.cycle(1)
            seen.append(get_settings().theme)
        assert set(seen) == set(THEME_NAMES)

    def test_boolean_row_toggles(self):
        model = self._model()
        diffs_row = next(r for r in model.rows if r.label == "show diffs")
        before = get_settings().show_diffs
        diffs_row.cycle(1)
        assert get_settings().show_diffs is (not before)
        diffs_row.cycle(1)
        assert get_settings().show_diffs is before

    def test_pinned_panel_row_toggles_membership(self):
        model = self._model()
        row = next(r for r in model.rows if r.label == "pin tools panel")
        assert "tool-panel" not in get_settings().pinned_panels
        row.cycle(1)
        assert "tool-panel" in get_settings().pinned_panels
        row.cycle(1)
        assert "tool-panel" not in get_settings().pinned_panels

    def test_every_change_fires_on_change_callback(self):
        fired = []
        model = SettingsModel(on_change=lambda: fired.append(1))
        for row in model.rows:
            row.cycle(1)
        assert len(fired) == len(model.rows)

    def test_format_row_shows_current_value_inline(self):
        model = self._model()
        row = next(r for r in model.rows if r.label == "show diffs")
        get_settings().show_diffs = True
        assert "✓" in model.format_row(row)
        get_settings().show_diffs = False
        assert "—" in model.format_row(row)
        theme_row = model.rows[0]
        assert get_settings().theme in model.format_row(theme_row)

    def test_broken_on_change_callback_never_corrupts_save(self, tmp_path):
        def boom():
            raise RuntimeError("callback exploded")

        model = SettingsModel(on_change=boom)
        model.rows[0].cycle(1)  # must not raise
        # The value still changed and persisted despite the callback.
        assert get_settings().theme != "neural-cyan"
        loaded, _ = load_settings(str(tmp_path / "settings.json"))
        assert loaded.theme == get_settings().theme
