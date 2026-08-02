"""Tests for SkillDiscoverer -- a real bug fix.

`run_doctor()` previously called `doctor.discover_skills()`
unconditionally regardless of the `interactive` flag, and
`SkillDiscoverer.discover()` itself called bare `input()` with no TTY
check. Together this meant any non-interactive invocation of `nexus tui`,
`nexus dashboard`, or `nexus repl` on a fresh install (no provider
configured yet) would hang forever the first time a known local tool
(node, docker, rustc, pytest, sqlite3) was detected on the system --
including in CI, scripts, and piped/redirected output.
"""

from unittest.mock import patch

import pytest

from nexus.skills.discovery import SkillDiscoverer


@pytest.fixture
def discoverer(tmp_path):
    return SkillDiscoverer(tmp_path)


def test_discover_does_not_block_on_non_tty(discoverer):
    """The core regression test: discover() must never call input() when
    stdin is not a TTY, no matter how many tools are detected."""
    with patch("shutil.which", return_value="/usr/bin/fake-tool"), \
         patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.input") as mock_input:
        discoverer.discover()
        mock_input.assert_not_called()


def test_discover_prompts_only_when_interactive_tty(discoverer):
    with patch("shutil.which", return_value="/usr/bin/fake-tool"), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n") as mock_input:
        discoverer.discover()
        assert mock_input.called


def test_discover_handles_eof_gracefully(discoverer):
    """If stdin is a TTY but gets closed mid-prompt (e.g. piped input
    runs out), discover() must not crash with an unhandled EOFError."""
    with patch("shutil.which", return_value="/usr/bin/fake-tool"), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=EOFError):
        discoverer.discover()  # should not raise


def test_register_skill_creates_parent_dir(discoverer, tmp_path):
    """Regression: _register_skill used to .touch() a file inside
    skills_dir without ensuring that directory existed first, which would
    FileNotFoundError on a genuinely fresh ~/.nexus install."""
    assert not discoverer.skills_dir.exists()
    discoverer._register_skill("some-new-skill")
    assert (discoverer.skills_dir / "some-new-skill.py").exists()


def test_discover_skills_respects_interactive_flag_in_run_doctor(monkeypatch, tmp_path):
    """Regression: run_doctor(interactive=False) used to still call
    discover_skills() unconditionally."""
    from nexus import doctor as doctor_module

    called = {"discover_skills": False}

    class FakeDoctor:
        def __init__(self):
            pass

        def run_all(self):
            return {"config": {"configured": True}}

        def print_report(self, report):
            pass

        def interactive_setup(self):
            pass

        def discover_skills(self):
            called["discover_skills"] = True

    monkeypatch.setattr(doctor_module, "NexusDoctor", FakeDoctor)
    doctor_module.run_doctor(interactive=False)
    assert called["discover_skills"] is False
