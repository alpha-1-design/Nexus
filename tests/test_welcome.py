"""Tests for the Nexus boot sequence module."""

import pytest
from unittest.mock import patch

from nexus.cli.welcome import (
    get_logo,
    clear,
    _get_terminal_width,
    _color_text,
    _draw_progress_bar,
)


def test_get_logo_returns_string():
    logo = get_logo()
    assert isinstance(logo, str)
    assert "NEXUS" in logo.upper() or "N" in logo or "NEURAL" in logo.upper()
    assert len(logo) > 50


def test_get_logo_contains_ascii_art():
    logo = get_logo()
    # Should contain block drawing characters
    assert "\u2588" in logo or "\u2591" in logo or "\u2554" in logo or "\u2502" in logo
    # Should have color codes
    assert "\033[" in logo


def test_clear_calls_os_system():
    with patch("os.system") as mock:
        clear()
        mock.assert_called_once()


def test_get_terminal_width_returns_positive():
    w = _get_terminal_width()
    assert isinstance(w, int)
    assert w > 0


def test_get_terminal_width_fallback():
    with patch("shutil.get_terminal_size", side_effect=Exception):
        w = _get_terminal_width()
        assert w == 80


def test_color_text_without_bold():
    result = _color_text("test", 32)
    assert "\033[32m" in result
    assert "test" in result
    assert "\033[0m" in result


def test_color_text_with_bold():
    result = _color_text("test", 36, bold=True)
    assert "\033[1;36m" in result
    assert "test" in result


def test_draw_progress_bar_at_start():
    result = _draw_progress_bar(0, 10, width=10)
    assert "0%" in result or " 0%" in result
    assert "[" in result
    assert "]" in result


def test_draw_progress_bar_at_end():
    result = _draw_progress_bar(10, 10, width=10)
    assert "100" in result or "00%" in result


def test_draw_progress_bar_midway():
    result = _draw_progress_bar(5, 10, width=10)
    assert "50" in result


def test_draw_progress_bar_zero_total():
    result = _draw_progress_bar(0, 0, width=10)
    assert isinstance(result, str)


def test_fade_print_can_run():
    from nexus.cli.welcome import fade_print
    with patch("sys.stdout.write"):
        with patch("time.sleep"):
            fade_print("hello", delay=0.001)


# ── boot-sequence UX behavior ──────────────────────────────────────────


def test_non_tty_prints_instant_minimal_banner(tmp_path, monkeypatch, capsys):
    """Non-interactive output (piped/redirected/CI) must never trigger the
    multi-second animated sequence -- it should print one line and return
    immediately."""
    from nexus.cli import welcome

    monkeypatch.setattr(welcome.sys.stdout, "isatty", lambda: False)
    welcome.display_welcome()
    captured = capsys.readouterr()
    assert "Nexus" in captured.out
    assert "\033[" not in captured.out  # no ANSI animation codes


def test_no_color_env_skips_animation(monkeypatch, capsys):
    from nexus.cli import welcome

    monkeypatch.setattr(welcome.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    welcome.display_welcome()
    captured = capsys.readouterr()
    assert "\033[" not in captured.out


def test_nexus_boot_off_env_skips_animation(monkeypatch, capsys):
    from nexus.cli import welcome

    monkeypatch.setattr(welcome.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NEXUS_BOOT", "off")
    welcome.display_welcome()
    captured = capsys.readouterr()
    assert "\033[" not in captured.out


def test_first_run_uses_full_sequence_and_writes_marker(tmp_path, monkeypatch):
    from nexus.cli import welcome

    monkeypatch.setattr(welcome, "_BOOT_MARKER", tmp_path / ".nexus" / ".boot_seen")
    monkeypatch.setattr(welcome, "_is_interactive_tty", lambda: True)

    called = {"full": False, "fast": False}
    monkeypatch.setattr(welcome, "_display_full_boot", lambda: called.__setitem__("full", True))
    monkeypatch.setattr(welcome, "_display_fast_boot", lambda: called.__setitem__("fast", True))

    assert not welcome._has_seen_boot()
    welcome.display_welcome()

    assert called["full"] is True
    assert called["fast"] is False
    assert welcome._has_seen_boot()


def test_repeat_run_uses_fast_sequence(tmp_path, monkeypatch):
    from nexus.cli import welcome

    marker = tmp_path / ".nexus" / ".boot_seen"
    marker.parent.mkdir(parents=True)
    marker.touch()
    monkeypatch.setattr(welcome, "_BOOT_MARKER", marker)
    monkeypatch.setattr(welcome, "_is_interactive_tty", lambda: True)

    called = {"full": False, "fast": False}
    monkeypatch.setattr(welcome, "_display_full_boot", lambda: called.__setitem__("full", True))
    monkeypatch.setattr(welcome, "_display_fast_boot", lambda: called.__setitem__("fast", True))

    welcome.display_welcome()

    assert called["full"] is False
    assert called["fast"] is True


def test_nexus_boot_full_env_forces_full_sequence_even_if_seen(tmp_path, monkeypatch):
    from nexus.cli import welcome

    marker = tmp_path / ".nexus" / ".boot_seen"
    marker.parent.mkdir(parents=True)
    marker.touch()
    monkeypatch.setattr(welcome, "_BOOT_MARKER", marker)
    monkeypatch.setattr(welcome, "_is_interactive_tty", lambda: True)
    monkeypatch.setenv("NEXUS_BOOT", "full")

    called = {"full": False}
    monkeypatch.setattr(welcome, "_display_full_boot", lambda: called.__setitem__("full", True))
    monkeypatch.setattr(welcome, "_display_fast_boot", lambda: (_ for _ in ()).throw(AssertionError("should not use fast path")))

    welcome.display_welcome()
    assert called["full"] is True
