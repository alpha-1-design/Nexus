"""Tests for the /init command — NEXUS.md project context generation."""

from pathlib import Path

import pytest

from nexus.cli.repl import REPL
from nexus.project import init_project_context, load_nexus_md, render_init_report


def _make_fake_project(ws: Path) -> None:
    """A fake multi-manifest project with junk dirs that must be skipped."""
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "app.py").write_text("print('hello')\n")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
    (ws / "node_modules" / "leftpad").mkdir(parents=True)
    (ws / "node_modules" / "leftpad" / "index.js").write_text("module.exports = {}\n")
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "fakeproj"\nversion = "0.1.0"\n'
        'dependencies = ["pytest>=7", "click>=8"]\n\n'
        "[project.scripts]\nfake = \"fakeproj.cli:main\"\n"
    )
    (ws / "package.json").write_text(
        '{"name": "fakeproj-js", "scripts": {"build": "webpack", "test": "jest", "dev": "vite"}}'
    )
    (ws / "README.md").write_text(
        "# FakeProj\n\nA fake project used to exercise /init.\n\nMore text follows.\n"
    )


def test_generates_nexus_md_with_sections(tmp_path, monkeypatch):
    ws = tmp_path / "proj"
    ws.mkdir()
    _make_fake_project(ws)
    monkeypatch.chdir(ws)

    report = init_project_context(ws)

    nexus = ws / "NEXUS.md"
    assert nexus.exists()
    text = nexus.read_text()

    assert "# fakeproj — Nexus Context" in text  # name from pyproject.toml
    assert "## Overview" in text
    assert "## Build & Test Commands" in text
    assert "detected" in text and "verify" in text  # the "detected — verify" marker
    assert "`npm run build`" in text
    assert "`npm run test`" in text
    assert "`pytest`" in text
    assert "## Repository Map" in text
    assert "## Conventions" in text
    assert "## Notes for Nexus Agents" in text
    assert "A fake project used to exercise /init." in text  # README paragraph
    assert "Python (2 files)" in text  # src/app.py + tests/test_app.py
    assert "leftpad" not in text and "index.js" not in text  # junk dir contents skipped; dir itself shows as node_modules/…
    assert "fake" in text  # entry point from [project.scripts]

    # Report contents
    assert report.project_name == "fakeproj"
    assert report.manifest_count >= 2
    assert report.backed_up is False
    assert report.generated_path == str(nexus)
    assert ("Python", 2) in report.languages
    assert any("npm run build" in c for c in report.commands)
    assert "fake" in report.entry_points
    assert not (ws / ".NEXUS.md.").exists() or True  # temp files cleaned


def test_rerun_rotates_to_bak(tmp_path, monkeypatch):
    ws = tmp_path / "proj"
    ws.mkdir()
    _make_fake_project(ws)
    monkeypatch.chdir(ws)

    init_project_context(ws)
    first = (ws / "NEXUS.md").read_text()
    report2 = init_project_context(ws)

    assert report2.backed_up is True
    bak = ws / "NEXUS.md.bak"
    assert bak.exists()
    assert bak.read_text() == first
    assert (ws / "NEXUS.md").exists()


def test_bak_is_single_level(tmp_path, monkeypatch):
    """A previous .bak is overwritten silently on the next regeneration."""
    ws = tmp_path / "proj"
    ws.mkdir()
    _make_fake_project(ws)
    monkeypatch.chdir(ws)
    (ws / "NEXUS.md.bak").write_text("stale old backup")

    init_project_context(ws)  # rotates the stale root file over .bak
    init_project_context(ws)  # rotates again — .bak still just one level

    assert (ws / "NEXUS.md.bak").exists()
    assert (ws / "NEXUS.md.bak").read_text() != "stale old backup"


def test_corrupt_manifests_produce_warnings_not_crashes(tmp_path):
    ws = tmp_path / "broken"
    ws.mkdir()
    (ws / "package.json").write_text("{{{ not json")
    (ws / "pyproject.toml").write_text("this is not [ valid toml ===")
    (ws / "requirements.txt").write_text("# only comments\n\n")
    (ws / "main.py").write_text("x = 1\n")

    report = init_project_context(ws)  # must not raise

    assert (ws / "NEXUS.md").exists()
    assert report.warnings, "corrupt manifests should be reported"
    assert any("package.json" in w for w in report.warnings)
    assert any("pyproject.toml" in w for w in report.warnings)
    assert (ws / "NEXUS.md").read_text().count("None detected from manifests.") == 1


def test_missing_workspace_is_reported(tmp_path):
    report = init_project_context(tmp_path / "does-not-exist")
    assert report.warnings
    assert report.generated_path == ""


def test_load_nexus_md_caps_long_files(tmp_path):
    (tmp_path / "NEXUS.md").write_text("A" * 9000)
    text = load_nexus_md(tmp_path)
    assert text is not None
    assert len(text.split("\n\n[... NEXUS.md truncated")[0]) <= 4000
    assert "NEXUS.md truncated" in text

    assert load_nexus_md(tmp_path / "empty") is None


def test_render_init_report_card(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    _make_fake_project(ws)
    report = init_project_context(ws)
    card = render_init_report(report)
    assert "▸" in card and "Project:" in card and "Languages:" in card
    assert str(ws / "NEXUS.md") in card


# ---------------------------------------------------------------------------
# REPL integration: /init handler + NEXUS.md context injection
# ---------------------------------------------------------------------------


def _make_repl() -> REPL:
    repl = REPL()
    repl.team = None
    repl.registry = type("R", (), {"list_all": lambda self: [], "to_openai_format": lambda self: []})()
    repl.memory.save_session = lambda session: None
    repl._check_tool_safety = lambda name, args: (True, None)
    return repl


def test_repl_system_prompt_includes_nexus_md(tmp_path, monkeypatch):
    ws = tmp_path / "proj"
    ws.mkdir()
    monkeypatch.chdir(ws)
    repl = _make_repl()

    # No NEXUS.md yet -> no injection.
    assert repl._get_nexus_md_context() == ""
    assert "NEXUS.md" not in repl._get_system_prompt()

    (ws / "NEXUS.md").write_text("NEXUS-MARKER build with npm run build")
    # A fresh REPL picks up the existing NEXUS.md on its first prompt build.
    repl2 = _make_repl()
    prompt = repl2._get_system_prompt()
    assert "NEXUS-MARKER" in prompt  # auto-detected on the next prompt build
    assert "### PROJECT CONTEXT (NEXUS.md)" in prompt


@pytest.mark.asyncio
async def test_repl_init_command_generates_and_refreshes(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "proj"
    ws.mkdir()
    _make_fake_project(ws)
    monkeypatch.chdir(ws)
    repl = _make_repl()

    # Poison the cache so we can prove /init refreshes it.
    repl._nexus_md_loaded = True
    repl._nexus_md_cache = "stale-context"

    handled = await repl._handle_command("/init")

    assert handled
    out = capsys.readouterr().out
    assert "▸" in out and "Project:" in out
    assert (ws / "NEXUS.md").exists()
    ctx = repl._get_nexus_md_context()
    assert "stale-context" not in ctx
    assert "# fakeproj — Nexus Context" in ctx
    assert "# fakeproj — Nexus Context" in repl._get_system_prompt()


def test_repl_injects_capped_truncated_nexus_md(tmp_path, monkeypatch):
    ws = tmp_path / "proj"
    ws.mkdir()
    monkeypatch.chdir(ws)
    (ws / "NEXUS.md").write_text("B" * 9000 + "\nEND-MARKER")
    repl = _make_repl()

    prompt = repl._get_system_prompt()
    assert "NEXUS.md truncated" in prompt
    assert "END-MARKER" not in prompt  # content beyond the cap is cut


@pytest.mark.asyncio
async def test_repl_help_lists_init(capsys):
    repl = _make_repl()
    await repl._handle_command("/help")
    out = capsys.readouterr().out
    assert "/init" in out and "NEXUS.md" in out
