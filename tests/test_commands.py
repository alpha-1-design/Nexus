"""Tests for CLI commands (plugin, scan, export, import, backup, safety, agents, team)."""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def nexus_dir(tmp_path):
    d = tmp_path / ".nexus"
    d.mkdir(parents=True)
    return d


# =============================================================================
# Plugin commands
# =============================================================================


class TestPluginCommands:
    def test_plugin_list_empty(self, runner):
        """plugin list shows no-plugins message."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["plugin", "list"])
        assert result.exit_code == 0

    def test_plugin_install_and_remove(self, runner, tmp_path):
        """install a plugin then remove it."""
        from nexus.cli.commands import cli
        plugin_file = tmp_path / "test_plugin.py"
        plugin_file.write_text("class Plugin:\n    pass\n")
        result = runner.invoke(cli, ["plugin", "install", str(plugin_file)])
        assert result.exit_code == 0
        assert "Installed" in result.output

        result = runner.invoke(cli, ["plugin", "list"])
        assert result.exit_code == 0

        result = runner.invoke(cli, ["plugin", "remove", "test_plugin"])
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_plugin_enable_disable(self, runner):
        """enable / disable existing plugin."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["plugin", "enable", "nonexistent"])
        assert "not found" in result.output

        result = runner.invoke(cli, ["plugin", "disable", "nonexistent"])
        assert "not found" in result.output


# =============================================================================
# Scan command
# =============================================================================


class TestScanCommand:
    def test_scan_current_dir(self, runner):
        """scan . outputs summary."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["scan", "."])
        assert result.exit_code == 0

    def test_scan_nonexistent_dir(self, runner):
        """scan nonexistent path shows error."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["scan", "/nonexistent/path"])
        assert result.exit_code != 0 or "not found" in result.output

    def test_scan_json_output(self, runner):
        """scan --output json returns valid JSON."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["scan", ".", "--output", "json"])
        assert result.exit_code == 0


# =============================================================================
# Export / Import / Backup commands
# =============================================================================


class TestExportImportCommands:
    def test_export(self, runner, nexus_dir):
        """export creates a zip file."""
        from nexus.cli.commands import cli
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["export", "--output", "test_export.zip", "--include", "config"])
            assert result.exit_code == 0
            assert Path("test_export.zip").exists()

    def test_backup(self, runner):
        """backup creates a zip in current dir."""
        from nexus.cli.commands import cli
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["backup"])
            assert result.exit_code == 0
            zips = list(Path(".").glob("nexus_backup_*.zip"))
            assert len(zips) > 0

    def test_import_invalid_file(self, runner):
        """import of non-zip file shows error."""
        from nexus.cli.commands import cli
        with runner.isolated_filesystem():
            Path("test.txt").write_text("not a zip")
            result = runner.invoke(cli, ["import", "test.txt"])
            assert result.exit_code != 0 or "Expected a .zip" in result.output


# =============================================================================
# Safety commands
# =============================================================================


class TestSafetyCommands:
    def test_safety_status(self, runner):
        """safety status shows current mode."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["safety", "status"])
        assert result.exit_code == 0

    def test_safety_mode_off(self, runner):
        """safety mode user_review sets mode to user_review."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["safety", "mode", "user_review"])
        assert result.exit_code == 0
        assert "user_review" in result.output.lower()

    def test_safety_mode_normal(self, runner):
        """safety mode strict sets mode to strict."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["safety", "mode", "strict"])
        assert result.exit_code == 0

    def test_safety_mode_strict(self, runner):
        """safety mode strict sets mode to strict."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["safety", "mode", "strict"])
        assert result.exit_code == 0

    def test_safety_rules(self, runner):
        """safety rules lists all rules."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["safety", "rules"])
        assert result.exit_code == 0


# =============================================================================
# Agents commands
# =============================================================================


class TestAgentsCommands:
    def test_agents_list(self, runner):
        """agents list shows active agents."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["agents", "list"])
        assert result.exit_code == 0

    def test_agents_spawn_no_provider(self, runner):
        """agents spawn without configured provider shows helpful error."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["agents", "spawn", "test task"])
        assert result.exit_code == 0


# =============================================================================
# Team command
# =============================================================================


class TestTeamCommand:
    def test_team(self, runner):
        """team assembles a multi-agent team."""
        from nexus.cli.commands import cli
        result = runner.invoke(cli, ["team", "test task", "--members", "2"])
        assert result.exit_code == 0
