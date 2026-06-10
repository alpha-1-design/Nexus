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
