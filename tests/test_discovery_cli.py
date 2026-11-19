"""CLI smoke tests for the discovery/plugin surface (2026-11-19 session).

Covers: `nexus mcp list` (collect_server_status swap), `nexus skills match`,
`nexus discover` (+ --refresh) and `nexus plugins` (+ --reload). All external
state is pointed at tmp paths via the same env overrides the modules read in
production (NEXUS_MCP_CONFIG, NEXUS_PLUGINS_DIR, NEXUS_ENVIRONMENT_CACHE).
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point every discovery/plugin/MCP path at tmp so no ~/.nexus state leaks."""
    monkeypatch.setenv("NEXUS_ENVIRONMENT_CACHE", str(tmp_path / "environment.json"))
    monkeypatch.setenv("NEXUS_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("NEXUS_MCP_CONFIG", str(tmp_path / "mcp-servers.json"))
    return tmp_path


GOOD_PLUGIN = '''
from nexus.tools.base import BaseTool, ToolDefinition, ToolResult

class HelloTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="hello",
            description="Wave at the user.",
            input_schema={"type": "object", "properties": {}},
            category="plugin",
        )

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, content="hello!")

PLUGIN = {
    "name": "hello",
    "version": "1.0.0",
    "description": "A hello tool for CLI smoke tests",
    "tools": [HelloTool()],
}
'''


# =============================================================================
# nexus mcp list
# =============================================================================


class TestMcpList:
    def _config(self, tmp_path: Path) -> Path:
        cfg = tmp_path / "mcp-servers.json"
        cfg.write_text(json.dumps({
            "quick-server": {"transport": "stdio", "command": "/bin/echo", "args": ["hi"]},
        }))
        return cfg

    def test_quick_snapshot_lists_servers(self, runner, isolated_env):
        self._config(isolated_env)
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["mcp", "list", "--quick"])
        assert result.exit_code == 0
        assert "MCP Servers" in result.output
        assert "quick-server" in result.output
        assert "(stdio)" in result.output
        assert "config snapshot" in result.output

    def test_no_servers_hint(self, runner, isolated_env):
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["mcp", "list", "--quick"])
        assert result.exit_code == 0
        assert "No MCP servers configured" in result.output

    def test_dead_server_renders_error_not_traceback(self, runner, isolated_env):
        # connect=True with a nonexistent command -> per-server error row.
        cfg = isolated_env / "mcp-servers.json"
        cfg.write_text(json.dumps({
            "dead-server": {"transport": "stdio", "command": "nexus-definitely-missing-cmd"},
        }))
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["mcp", "list"])
        assert result.exit_code == 0
        assert "dead-server" in result.output
        assert "✗" in result.output
        assert "error:" in result.output


# =============================================================================
# nexus skills match (skills group alias + matcher)
# =============================================================================


class TestSkillsMatch:
    def test_match_prints_scored_skills(self, runner):
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["skills", "match", "debug a failing test"])
        assert result.exit_code == 0
        assert "Skills matching" in result.output
        assert "score" in result.output

    def test_alias_works_via_skill_group(self, runner):
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["skill", "match", "write pytest tests"])
        assert result.exit_code == 0
        assert "score" in result.output

    def test_no_match_is_quiet(self, runner):
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["skills", "match", "zzz qqq xxyy wvvv"])
        assert result.exit_code == 0
        assert "No bundled skills match" in result.output


# =============================================================================
# nexus discover
# =============================================================================


class TestDiscover:
    def test_environment_card(self, runner, isolated_env):
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["discover"])
        assert result.exit_code == 0
        assert "Nexus Environment" in result.output
        assert "Platform:" in result.output
        assert "Python:" in result.output
        assert "Capabilities" in result.output
        assert "PATH tools" in result.output
        assert "Project:" in result.output

    def test_refresh_writes_cache(self, runner, isolated_env):
        from nexus.cli.commands import cli

        cache = isolated_env / "environment.json"
        result = runner.invoke(cli, ["discover", "--refresh"])
        assert result.exit_code == 0
        assert cache.exists()
        raw = json.loads(cache.read_text())
        assert "profile" in raw

    def test_cached_card_mentions_cache_location(self, runner, isolated_env):
        from nexus.cli.commands import cli

        runner.invoke(cli, ["discover", "--refresh"])
        result = runner.invoke(cli, ["discover"])
        assert result.exit_code == 0
        assert "environment.json" in result.output


# =============================================================================
# nexus plugins
# =============================================================================


class TestPluginsCommand:
    def test_empty_plugins_hint(self, runner, isolated_env):
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["plugins"])
        assert result.exit_code == 0
        assert "No plugins discovered" in result.output
        assert "nexus-uptime.py" in result.output  # points at the example

    def test_lists_loaded_plugin(self, runner, isolated_env):
        (isolated_env / "plugins").mkdir()
        (isolated_env / "plugins" / "hello.py").write_text(GOOD_PLUGIN)
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["plugins", "--reload"])
        assert result.exit_code == 0
        assert "Tool Plugins" in result.output
        assert "hello" in result.output
        assert "1 loaded" in result.output
        assert "plugin_hello_hello" in result.output or "1 tool(s)" in result.output

    def test_broken_plugin_shows_error(self, runner, isolated_env):
        (isolated_env / "plugins").mkdir()
        (isolated_env / "plugins" / "broken.py").write_text("definitely (( not python")
        from nexus.cli.commands import cli

        result = runner.invoke(cli, ["plugins", "--reload"])
        assert result.exit_code == 0
        assert "broken" in result.output
        assert "error:" in result.output
        assert "0 loaded" in result.output
