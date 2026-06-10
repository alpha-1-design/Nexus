"""Tests for the Nexus UI indicator utilities."""

import time
import threading
from unittest.mock import patch

import pytest

from nexus.ui import (
    LoadingIndicator,
    ProgressTracker,
    Spinner,
    prompt_line,
    render_status_line,
    with_loading,
)


def test_spinner_start_stop():
    spinner = Spinner("testing")
    spinner.start()
    assert spinner._running is True
    assert spinner._thread is not None
    assert spinner._thread.is_alive()
    spinner.stop()
    assert spinner._running is False


def test_spinner_message_stored():
    spinner = Spinner("custom message")
    assert spinner.message == "custom message"


def test_loading_indicator_wraps_spinner():
    indicator = LoadingIndicator("working")
    indicator.start()
    assert indicator._spinner._running is True
    indicator.stop()
    assert indicator._spinner._running is False


def test_progress_tracker_initial_state():
    tracker = ProgressTracker(total=10, description="test")
    assert tracker.total == 10
    assert tracker.current == 0
    assert tracker.description == "test"


def test_progress_tracker_step():
    tracker = ProgressTracker(total=4)
    with patch("sys.stdout.write"):
        with patch("sys.stdout.flush"):
            tracker.step("step one")
            assert tracker.current == 1
            tracker.step("step two")
            assert tracker.current == 2
            tracker.step("step three")
            assert tracker.current == 3


def test_progress_tracker_zero_total():
    tracker = ProgressTracker(total=0)
    with patch("sys.stdout.write"):
        with patch("sys.stdout.flush"):
            tracker.step()
            assert tracker.current == 1
            tracker.finish()


def test_progress_tracker_finish():
    tracker = ProgressTracker(total=3)
    with patch("sys.stdout.write"):
        with patch("sys.stdout.flush"):
            for _ in range(3):
                tracker.step()
            tracker.finish()


def test_with_loading_success():
    @with_loading
    def dummy_func():
        return 42

    result = dummy_func()
    assert result == 42


def test_with_loading_exception():
    @with_loading
    def failing_func():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        failing_func()


def test_render_status_line_empty():
    result = render_status_line([])
    assert result == ""


def test_render_status_line_with_color():
    result = render_status_line([("hello", "32")])
    assert "\033[32m" in result
    assert "hello" in result
    assert "\033[0m" in result


def test_render_status_line_without_color():
    result = render_status_line([("hello", None)])
    assert result == "hello"


def test_render_status_line_multiple():
    parts = [("alpha", "31"), ("beta", None), ("gamma", "36")]
    result = render_status_line(parts)
    assert "alpha" in result
    assert "beta" in result
    assert "gamma" in result


def test_prompt_line_ready():
    result = prompt_line(prefix="nexus", status="ready")
    assert "nexus" in result
    assert "\u25CF" in result  # dot indicator


def test_prompt_line_with_provider():
    result = prompt_line(provider="openai")
    assert "openai" in result
    assert "@" in result


def test_prompt_line_busy():
    result = prompt_line(status="busy")
    assert "\u25CF" in result  # dot indicator


def test_prompt_line_error():
    result = prompt_line(status="error")
    assert "\u25CF" in result  # dot indicator


def test_prompt_line_default_prefix():
    result = prompt_line()
    assert "nexus" in result
