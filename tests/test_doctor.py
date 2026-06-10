"""Tests for the Nexus Doctor diagnostics module."""

import pytest

from nexus.doctor import NexusDoctor, _color, _ok, _fail, _warn, _info, _dim


def test_color_without_bold():
    result = _color("hello", 32)
    assert "\033[32m" in result
    assert "hello" in result
    assert "\033[0m" in result
    assert ";1" not in result


def test_color_with_bold():
    result = _color("hello", 32, bold=True)
    assert "\033[1;32m" in result


def test_ok_returns_green_checkmark():
    result = _ok("test")
    assert "\u2713" in result
    assert "test" in result
    assert "\033[32m" in result


def test_fail_returns_red_cross():
    result = _fail("test")
    assert "\u2717" in result
    assert "test" in result
    assert "\033[31m" in result


def test_warn_returns_yellow_warning():
    result = _warn("test")
    assert "\u26A0" in result
    assert "test" in result
    assert "\033[33m" in result


def test_info_returns_cyan():
    result = _info("test")
    assert "test" in result
    assert "\033[36m" in result


def test_dim_returns_gray():
    result = _dim("test")
    assert "test" in result
    assert "\033[90m" in result


def test_doctor_initializes():
    doctor = NexusDoctor()
    assert hasattr(doctor, "health_checks")
    assert "dependencies" in doctor.health_checks
    assert "environment" in doctor.health_checks
    assert "config" in doctor.health_checks
    assert "provider" in doctor.health_checks
    assert "memory" in doctor.health_checks
    assert "tools" in doctor.health_checks
    assert "network" in doctor.health_checks
    assert "cache" in doctor.health_checks
    assert "git" in doctor.health_checks
    assert "system" in doctor.health_checks


def test_check_dependencies():
    doctor = NexusDoctor()
    result = doctor._check_dependencies()
    assert "passed" in result
    assert "details" in result
    # Should at least have textual and requests
    assert "textual" in result["details"]
    assert "requests" in result["details"]


def test_check_environment():
    doctor = NexusDoctor()
    result = doctor._check_environment()
    assert "passed" in result
    assert "os" in result
    assert "writable" in result
    assert "tmp_writable" in result


def test_check_config():
    doctor = NexusDoctor()
    result = doctor._check_config()
    assert "configured" in result
    assert "active_provider" in result
    assert "provider_count" in result
    assert "providers" in result


def test_check_git():
    doctor = NexusDoctor()
    result = doctor._check_git()
    assert "passed" in result
    assert "is_repo" in result
    assert "branch" in result
    assert "status" in result


def test_check_system():
    doctor = NexusDoctor()
    result = doctor._check_system()
    assert result["passed"] is True
    assert "os" in result
    assert "python" in result
    assert "cpus" in result
    assert "disk_total" in result
    assert "disk_free" in result


def test_check_memory_never_crashes():
    doctor = NexusDoctor()
    # Memory check should not crash even without proper init
    result = doctor._check_memory()
    assert "passed" in result


def test_check_tools_never_crashes():
    doctor = NexusDoctor()
    result = doctor._check_tools()
    assert "tool_count" in result
    assert isinstance(result["tool_count"], int)


def test_run_all_returns_all_checks():
    doctor = NexusDoctor()
    results = doctor.run_all()
    for name in doctor.health_checks:
        assert name in results
    assert len(results) == len(doctor.health_checks)


def test_tactical_cleanup_dry_run():
    doctor = NexusDoctor()
    result = doctor.tactical_cleanup(dry_run=True)
    assert result["dry_run"] is True
    assert isinstance(result["potential_savings"], str)


def test_tactical_cleanup_returns_valid_keys():
    doctor = NexusDoctor()
    result = doctor.tactical_cleanup(dry_run=True)
    assert "freed_bytes" in result
    assert "potential_savings" in result
    assert "files_removed" in result
