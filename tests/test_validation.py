"""Tests for the shared validation module (Refiner's Fire, tool failure hints)."""

import json
import os
import tempfile

import pytest

from nexus.utils.validation import run_refiners_fire, get_tool_failure_hint


@pytest.mark.asyncio
async def test_refiners_fire_valid_python():
    """Valid Python should pass the fire."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def hello():\n    return 'world'\n")
        path = f.name
    try:
        passed, error = await run_refiners_fire(path)
        assert passed is True
        assert error is None
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_refiners_fire_invalid_python():
    """Invalid Python syntax should fail the fire."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def broken(:")
        path = f.name
    try:
        passed, error = await run_refiners_fire(path)
        assert passed is False
        assert error is not None
        assert "Syntax Error" in error
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_refiners_fire_valid_json():
    """Valid JSON should pass the fire."""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump({"key": "value", "nested": [1, 2, 3]}, f)
        path = f.name
    try:
        passed, error = await run_refiners_fire(path)
        assert passed is True
        assert error is None
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_refiners_fire_invalid_json():
    """Invalid JSON should fail the fire."""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        f.write("{invalid json here}")
        path = f.name
    try:
        passed, error = await run_refiners_fire(path)
        assert passed is False
        assert error is not None
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_refiners_fire_nonexistent_path():
    """Non-existent files should pass (nothing to validate)."""
    passed, error = await run_refiners_fire("/nonexistent/path.py")
    assert passed is True
    assert error is None


@pytest.mark.asyncio
async def test_refiners_fire_none_path():
    """None path should pass."""
    passed, error = await run_refiners_fire(None)
    assert passed is True
    assert error is None


@pytest.mark.asyncio
async def test_refiners_fire_unknown_extension():
    """Unknown file extensions should pass (no validator registered)."""
    with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
        f.write("arbitrary content")
        path = f.name
    try:
        passed, error = await run_refiners_fire(path)
        assert passed is True
        assert error is None
    finally:
        os.unlink(path)


def test_tool_failure_edit_context_mismatch():
    """Edit tool context mismatch should produce a recovery hint."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def foo():\n    return 42\n")
        path = f.name
    try:
        hint = get_tool_failure_hint(
            "edit",
            {"path": path, "old_string": "def foo()"},
            "Context mismatch: could not find exact match",
        )
        assert "RECOVERY HINT" in hint
    finally:
        os.unlink(path)


def test_tool_failure_bash_not_found():
    """Bash tool command not found should produce a recovery hint."""
    hint = get_tool_failure_hint(
        "bash",
        {"command": "nonexistent-tool"},
        "Command not found: nonexistent-tool",
    )
    assert "RECOVERY HINT" in hint
    assert "pkg install" in hint or "apt install" in hint


def test_tool_failure_file_not_found():
    """File not found should produce a recovery hint."""
    hint = get_tool_failure_hint(
        "read",
        {"path": "/missing/file.txt"},
        "No such file or directory: /missing/file.txt",
    )
    assert "RECOVERY HINT" in hint
    assert "glob" in hint


def test_tool_failure_unknown_error():
    """Unknown errors should still produce a basic hint."""
    hint = get_tool_failure_hint(
        "random_tool",
        {"option": "value"},
        "Something went wrong",
    )
    assert "Error:" in hint
