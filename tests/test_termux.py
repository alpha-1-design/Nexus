"""Tests for the Termux integration layer.

This sandbox doesn't run inside actual Termux, so these tests focus on:
  - correct graceful degradation when Termux binaries aren't available
  - correct command construction (without actually invoking subprocess)
  - the pure-data helper classes (BatteryStatus, StatusBar)
"""

from unittest.mock import MagicMock, patch

import pytest

from nexus.termux.api import TermuxAPI, get_termux_api
from nexus.termux.battery import BatteryStatus
from nexus.termux.status_bar import StatusBar


# ── detection / degradation ─────────────────────────────────────────────


def test_detect_termux_returns_real_bool_not_env_string(monkeypatch):
    """Regression: `_detect_termux` used to `or` a possibly-string env var
    into its return value, so `is_available` could be a truthy string like
    "0.118" instead of an actual bool."""
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    monkeypatch.setattr("os.path.exists", lambda p: False)
    api = TermuxAPI()
    assert api.is_available is True
    assert type(api.is_available) is bool


def test_not_available_on_a_normal_linux_box(monkeypatch):
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    api = TermuxAPI()
    assert api.is_available is False


def test_commands_degrade_gracefully_when_unavailable(monkeypatch):
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    api = TermuxAPI()

    with patch("subprocess.run") as mock_run:
        success, msg = api.clipboard_get()
        assert success is False
        assert "not available" in msg.lower()
        mock_run.assert_not_called()  # must not shell out when unavailable


def test_get_termux_api_is_a_singleton():
    assert get_termux_api() is get_termux_api()


# ── command construction (mocked subprocess) ────────────────────────────


@pytest.fixture
def available_api(monkeypatch):
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    return TermuxAPI()


def _mock_success(stdout=""):
    result = MagicMock()
    result.returncode = 0
    result.stdout = stdout
    result.stderr = ""
    return result


def test_clipboard_set_command(available_api):
    with patch("subprocess.run", return_value=_mock_success()) as mock_run:
        ok, _ = available_api.clipboard_set("hello world")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert args == ["termux-clipboard-set", "hello world"]


def test_notify_command_includes_sound_and_priority(available_api):
    with patch("subprocess.run", return_value=_mock_success()) as mock_run:
        ok, _ = available_api.notify("Title", "Body", id=3, sound=False, priority="high")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert "--title" in args and "Title" in args
        assert "--no-sound" in args
        assert "--priority" in args and "high" in args
        assert "--id" in args and "3" in args


def test_notify_error_uses_high_priority(available_api):
    with patch("subprocess.run", return_value=_mock_success()) as mock_run:
        available_api.notify_error("Something broke")
        args = mock_run.call_args[0][0]
        assert "high" in args


def test_battery_status_parses_json(available_api):
    payload = '{"health": "GOOD", "percentage": 87, "plugged": "AC", "temperature": 30.1, "voltage": 4000}'
    with patch("subprocess.run", return_value=_mock_success(stdout=payload)):
        ok, data = available_api.battery_status()
        assert ok is True
        assert data["percentage"] == 87


def test_battery_status_handles_bad_json(available_api):
    with patch("subprocess.run", return_value=_mock_success(stdout="not json")):
        ok, data = available_api.battery_status()
        assert ok is False


def test_sms_send_uses_separator_before_message(available_api):
    with patch("subprocess.run", return_value=_mock_success()) as mock_run:
        available_api.sms_send("+15551234567", "hi there")
        args = mock_run.call_args[0][0]
        assert args == ["termux-sms-send", "-n", "+15551234567", "--", "hi there"]


def test_timeout_is_handled_gracefully(available_api):
    import subprocess

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        ok, msg = available_api.clipboard_get()
        assert ok is False
        assert "timed out" in msg.lower()


def test_missing_binary_is_handled_gracefully(available_api):
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        ok, msg = available_api.clipboard_get()
        assert ok is False
        assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_arun_does_not_crash_inside_running_loop(available_api):
    """Regression: `_arun` used `asyncio.get_event_loop()`, deprecated (and
    eventually removed) when called from within a running loop context in
    modern Python. `get_running_loop()` is the correct call here since
    `_arun` is only ever awaited from async code."""
    with patch("subprocess.run", return_value=_mock_success(stdout="ok")):
        ok, out = await available_api._arun(["termux-clipboard-get"])
        assert ok is True
        assert out == "ok"


# ── BatteryStatus ────────────────────────────────────────────────────────


def test_battery_status_icon_thresholds():
    assert BatteryStatus(percentage=90, plugged="none").icon == "🟢"
    assert BatteryStatus(percentage=60, plugged="none").icon == "🟡"
    assert BatteryStatus(percentage=30, plugged="none").icon == "🟠"
    assert BatteryStatus(percentage=10, plugged="none").icon == "🔴"
    assert BatteryStatus(percentage=10, plugged="AC").icon == "⚡"


def test_battery_status_is_charging():
    assert BatteryStatus(plugged="AC").is_charging is True
    assert BatteryStatus(plugged="USB").is_charging is True
    assert BatteryStatus(plugged="none").is_charging is False
    assert BatteryStatus(plugged="unknown").is_charging is False


def test_battery_status_from_dict_defaults():
    status = BatteryStatus.from_dict({"percentage": 55})
    assert status.percentage == 55
    assert status.health == "unknown"


# ── StatusBar ─────────────────────────────────────────────────────────


def test_status_bar_includes_termux_fields_only_in_termux_mode():
    bar = StatusBar(termux_mode=False, battery_pct=50)
    assert "%" not in bar.format(width=200).replace("NEXUS", "")  # no battery shown

    bar2 = StatusBar(termux_mode=True, battery_pct=50, is_charging=True)
    formatted = bar2.format(width=200)
    assert "50%" in formatted
    assert "⚡" in formatted


def test_status_bar_respects_width():
    bar = StatusBar()
    out = bar.format(width=40)
    assert len(out) == 40


def test_status_bar_shows_agent_count_when_present():
    bar = StatusBar(agent_count=3)
    assert "×3" in bar.format(width=200)
