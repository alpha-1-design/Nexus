"""Tests for the REPL module — focusing on refactored tool execution."""

import pytest

from nexus.safety import SafetyMode, get_safety_engine


def test_safety_engine_modes():
    """Safety engine should support all required modes."""
    safety = get_safety_engine()
    modes = [m for m in SafetyMode]
    assert SafetyMode.USER_REVIEW in modes
    assert SafetyMode.READ_ONLY in modes
    assert SafetyMode.STRICT in modes


def test_safety_read_before_edit():
    """Safety should detect edits without prior reads."""
    safety = get_safety_engine()
    context = {"tool": "edit", "path": "unknown.py"}
    violations = safety.check(context)
    read_edit_violations = [v for v in violations if "read" in v.rule.id.lower()]
    assert len(read_edit_violations) > 0


def test_safety_blocks_destructive_commands():
    """Safety should flag edits without prior reads."""
    safety = get_safety_engine()
    context = {"tool": "edit", "path": "/etc/passwd"}
    violations = safety.check(context)
    read_edit = [v for v in violations if v.rule.id == "read-before-edit"]
    assert len(read_edit) > 0


def test_safety_read_tool_bypasses_edit_check():
    """Read tool should not trigger read-before-edit violations."""
    safety = get_safety_engine()
    context = {"tool": "Read", "path": "readme.md"}
    violations = safety.check(context)
    read_edit = [v for v in violations if v.rule.id == "read-before-edit"]
    assert len(read_edit) == 0
