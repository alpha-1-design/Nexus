"""Tests for the drop-in tool-plugin loader (nexus/plugins/loader.py).

Every test points NEXUS_PLUGINS_DIR at a tmp directory, so the user's real
~/.nexus/plugins is never touched. Plugin files are written as real .py
modules — the loader imports them for real, so these are integration-shaped
unit tests (no process spawning, fully hermetic). The synthetic plugins
embed their own tool class so they never depend on this file's importability.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from nexus.plugins import (
    PluginRecord,
    default_plugins_dir,
    list_plugins,
    reload_plugins,
)
from nexus.plugins.loader import register_plugin_tools
from nexus.tools.base import BaseTool, ToolDefinition, ToolRegistry, ToolResult, _register_core_tools

EXAMPLE_PLUGIN = Path(__file__).resolve().parent.parent / "examples" / "plugins" / "nexus-uptime.py"

# A self-contained tool source shared by the synthetic plugins below.
ECHO_TOOL_SRC = '''
from nexus.tools.base import BaseTool, ToolDefinition, ToolResult

class EchoTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Echo the given text back.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            category="plugin",
        )

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, content=str(kwargs.get("text", "")))
'''

GOOD_PLUGIN = ECHO_TOOL_SRC + '''
PLUGIN = {
    "name": "greeter",
    "version": "1.2.0",
    "description": "Says hello via the echo tool",
    "tools": [EchoTool()],
}
'''

CLASS_PLUGIN = ECHO_TOOL_SRC + '''
PLUGIN = {"name": "clsplug", "tools": [EchoTool]}  # class, not instance
'''

REGISTER_PLUGIN = ECHO_TOOL_SRC + '''
def register(registry):
    registry.register(EchoTool())
'''

REGISTER_FAILING_PLUGIN = '''
def register(registry):
    raise RuntimeError("boom")
'''

BROKEN_PLUGIN = "this is definaaately not python (((\n"

NO_MANIFEST_PLUGIN = "X = 42\n"

BAD_NAME_PLUGIN = 'PLUGIN = {"name": "Not A Slug!", "tools": []}\n'

BAD_TOOL_PLUGIN = 'PLUGIN = {"name": "badtool", "tools": ["i am a string, not a tool"]}\n'


# =============================================================================
# Fixtures + helpers
# =============================================================================


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setenv("NEXUS_PLUGINS_DIR", str(d))
    return d


def _write(plugins_dir: Path, filename: str, source: str) -> Path:
    path = plugins_dir / filename
    path.write_text(source)
    return path


# =============================================================================
# Discovery + validation
# =============================================================================


class TestDiscovery:
    def test_default_dir_env_override(self, plugins_dir):
        assert default_plugins_dir() == plugins_dir

    def test_empty_dir(self, plugins_dir):
        assert reload_plugins() == []

    def test_missing_dir_is_empty_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NEXUS_PLUGINS_DIR", str(tmp_path / "nope"))
        assert reload_plugins() == []

    def test_good_manifest_plugin(self, plugins_dir):
        _write(plugins_dir, "greeter.py", GOOD_PLUGIN)
        records = reload_plugins()
        assert len(records) == 1
        r = records[0]
        assert (r.name, r.status, r.kind) == ("greeter", "loaded", "manifest")
        assert r.version == "1.2.0"
        assert r.tools == ["plugin_greeter_echo"]  # provisional collision-safe name
        assert r.error is None

    def test_broken_plugin_is_failed_not_fatal(self, plugins_dir):
        _write(plugins_dir, "broken.py", BROKEN_PLUGIN)
        r = reload_plugins()[0]
        assert r.status == "failed"
        assert r.error and "import failed" in r.error

    def test_no_manifest_is_failed_with_hint(self, plugins_dir):
        _write(plugins_dir, "nomani.py", NO_MANIFEST_PLUGIN)
        r = reload_plugins()[0]
        assert r.status == "failed"
        assert "no PLUGIN manifest" in r.error

    def test_invalid_name_slug_rejected(self, plugins_dir):
        _write(plugins_dir, "badname.py", BAD_NAME_PLUGIN)
        r = reload_plugins()[0]
        assert r.status == "failed"
        assert "invalid plugin name" in r.error

    def test_non_tool_entry_rejected(self, plugins_dir):
        _write(plugins_dir, "badtool.py", BAD_TOOL_PLUGIN)
        r = reload_plugins()[0]
        assert r.status == "failed"
        assert "not a BaseTool" in r.error

    def test_tool_class_is_instantiated(self, plugins_dir):
        _write(plugins_dir, "cls.py", CLASS_PLUGIN)
        r = reload_plugins()[0]
        assert r.status == "loaded" and r.tools == ["plugin_clsplug_echo"]

    def test_subdir_with_plugin_py(self, plugins_dir):
        sub = plugins_dir / "greeter"
        sub.mkdir()
        (sub / "plugin.py").write_text(GOOD_PLUGIN)
        r = reload_plugins()[0]
        assert r.status == "loaded" and r.name == "greeter"
        assert r.source.endswith("plugin.py")

    def test_underscore_and_non_py_files_ignored(self, plugins_dir):
        _write(plugins_dir, "_hidden.py", GOOD_PLUGIN)
        (plugins_dir / "__init__.py").write_text(GOOD_PLUGIN)
        (plugins_dir / "notes.txt").write_text("hello")
        assert reload_plugins() == []

    def test_empty_module_fails_with_clear_error(self, plugins_dir):
        (plugins_dir / "empty.py").write_text("")
        records = reload_plugins()
        assert [r.status for r in records] == ["failed"]
        assert "no PLUGIN manifest" in records[0].error

    def test_scan_cache_and_reload(self, plugins_dir):
        _write(plugins_dir, "greeter.py", GOOD_PLUGIN)
        first = list_plugins()
        assert list_plugins() is first  # cached, no rescan
        _write(plugins_dir, "broken.py", BROKEN_PLUGIN)
        assert len(list_plugins()) == 1  # still cached
        records = reload_plugins()  # forced rescan picks the new file up
        assert len(records) == 2


# =============================================================================
# Registration into the ToolRegistry
# =============================================================================


class TestRegistration:
    def test_manifest_tools_registered_with_prefix(self, plugins_dir):
        _write(plugins_dir, "greeter.py", GOOD_PLUGIN)
        registry = ToolRegistry()
        registered = register_plugin_tools(registry)
        assert registered == ["plugin_greeter_echo"]
        tool = registry.get("plugin_greeter_echo")
        assert tool is not None
        assert asyncio.run(tool.execute(text="hi")).content == "hi"

    def test_register_function_plugin(self, plugins_dir):
        _write(plugins_dir, "regpl.py", REGISTER_PLUGIN)
        registry = ToolRegistry()
        registered = register_plugin_tools(registry)
        assert "echo" in registered  # register() plugins manage their own names
        r = list_plugins()[0]
        assert r.kind == "register" and r.status == "loaded"
        assert r.tools == ["echo"]

    def test_register_function_failure_is_contained(self, plugins_dir):
        _write(plugins_dir, "badreg.py", REGISTER_FAILING_PLUGIN)
        registry = ToolRegistry()
        assert register_plugin_tools(registry) == []
        r = list_plugins()[0]
        assert r.status == "failed" and "boom" in r.error

    def test_duplicate_tool_skipped_with_warning(self, plugins_dir):
        # Two files declaring the same manifest name+tool -> the second
        # registration is skipped with a warning, the first one wins.
        _write(plugins_dir, "one.py", GOOD_PLUGIN)
        _write(plugins_dir, "two.py", GOOD_PLUGIN)
        registry = ToolRegistry()
        registered = register_plugin_tools(registry)
        assert registered == ["plugin_greeter_echo"]
        loaded = [r for r in list_plugins() if r.ok]
        warned = [r for r in loaded if any("already registered" in w for w in r.warnings)]
        assert len(warned) == 1

    def test_core_hook_registers_plugins(self, plugins_dir):
        """The lazy hook in tools/base.py must land plugins in the registry."""
        _write(plugins_dir, "greeter.py", GOOD_PLUGIN)
        reload_plugins()
        registry = ToolRegistry()
        _register_core_tools(registry)
        assert registry.get("plugin_greeter_echo") is not None

    def test_record_defaults(self):
        r = PluginRecord(name="x")
        assert r.ok and r.kind == "manifest" and r.tools == []


# =============================================================================
# The shipped example plugin
# =============================================================================


class TestExamplePlugin:
    def test_example_file_exists(self):
        assert EXAMPLE_PLUGIN.exists()

    def test_example_discovery_record(self, plugins_dir):
        shutil.copy(EXAMPLE_PLUGIN, plugins_dir / "nexus-uptime.py")
        r = reload_plugins()[0]
        assert (r.name, r.status, r.version) == ("uptime", "loaded", "1.0.0")
        assert r.description

    def test_example_loads_and_executes(self, plugins_dir):
        shutil.copy(EXAMPLE_PLUGIN, plugins_dir / "nexus-uptime.py")
        registry = ToolRegistry()
        registered = register_plugin_tools(registry)
        assert "plugin_uptime_uptime" in registered
        result = asyncio.run(registry.get("plugin_uptime_uptime").execute())
        assert result.success is True
        assert result.content.startswith("up ")

    def test_example_docstring_documents_contract(self):
        doc = EXAMPLE_PLUGIN.read_text(encoding="utf-8")
        for needle in ("PLUGIN", "register(registry)", "BaseTool", "plugin_<name>", "NEXUS_PLUGINS_DIR"):
            assert needle in doc, f"author guide should mention {needle!r}"
