"""nexus-uptime — a minimal Nexus tool plugin, and the plugin-author guide.

Nexus auto-discovers plugins in ``~/.nexus/plugins/``: copy this file there
as ``nexus-uptime.py`` (or as ``nexus-uptime/plugin.py``) and the ``uptime``
tool appears in the registry as ``plugin_uptime_uptime`` — no restart, no
pip install. List what Nexus found with ``nexus plugins`` (``--reload`` to
re-scan a running install).

THE MANIFEST CONTRACT
    A plugin module exposes EITHER a ``PLUGIN`` dict OR a ``register(registry)``
    function. The dict form is preferred:

        PLUGIN = {
            "name": "uptime",          # slug: lowercase letters, digits, '-', '_'
            "version": "1.0.0",        # shown by `nexus plugins`
            "description": "One line.",  # shown by `nexus plugins`
            "tools": [UptimeTool()],   # BaseTool instances (a BaseTool
        }                              # subclass is also accepted and
                                       # instantiated for you)

    Tool names are registered as ``plugin_<name>_<toolname>`` so plugins can
    never collide with core tools or each other; duplicates are skipped with
    a warning. With the ``register(registry)`` form you call
    ``registry.register(tool)`` yourself — prefix your tool names with
    ``plugin_<name>_`` for the same collision safety.

THE TOOL BASE
    Tools subclass ``nexus.tools.base.BaseTool`` and implement two things:
    a ``definition`` property returning a ``ToolDefinition`` (name,
    description, JSON-schema ``input_schema``, ``requires_permission``,
    ``category``) and an ``async execute(**kwargs)`` returning a
    ``ToolResult(success=..., content=...)``. The description and the schema
    are what the LLM sees — write them for the model, not for humans.

NAMING & SAFETY
    - Plugin file name does not matter; the manifest ``name`` does. Keep it
      a slug — invalid names fail validation and show up in
      ``nexus plugins`` with the error.
    - Anything raising inside your tool is converted to a failed ToolResult
      by the orchestrator, but a plugin whose *import* crashes is listed as
      failed and never breaks Nexus boot.

TESTING
    Point the loader at a scratch dir and check the registry:

        import os, tempfile
        from nexus.plugins import reload_plugins
        from nexus.tools.base import ToolRegistry, register_plugin_tools

        os.environ["NEXUS_PLUGINS_DIR"] = tempfile.mkdtemp()
        # ...drop this file in as <dir>/nexus-uptime.py...
        records = reload_plugins()                 # one PluginRecord per file
        registry = ToolRegistry()
        registered = register_plugin_tools(registry)
        assert "plugin_uptime_uptime" in registered
"""

from __future__ import annotations

import time
from pathlib import Path

from nexus.tools.base import BaseTool, ToolDefinition, ToolResult


class UptimeTool(BaseTool):
    """Report how long the host has been up."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="uptime",
            description="Show the system uptime (how long the device has been running). "
            "Takes no arguments. Useful before long builds or installs on phones.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            requires_permission=False,
            category="plugin",
        )

    async def execute(self, **kwargs) -> ToolResult:
        seconds = self._uptime_seconds()
        if seconds is None:
            return ToolResult(success=False, content="", error="uptime unavailable on this platform")
        return ToolResult(success=True, content=self._humanize(seconds), metadata={"seconds": seconds})

    @staticmethod
    def _uptime_seconds() -> float | None:
        try:
            raw = Path("/proc/uptime").read_text(encoding="utf-8").split()[0]
            return float(raw)  # linux + termux
        except Exception:
            pass
        try:
            import psutil

            return max(0.0, time.time() - psutil.boot_time())  # mac / windows
        except Exception:
            return None

    @staticmethod
    def _humanize(seconds: float) -> str:
        minutes, sec = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return f"up {', '.join(parts)} ({seconds:.0f}s)"


PLUGIN = {
    "name": "uptime",
    "version": "1.0.0",
    "description": "Adds an `uptime` tool: how long the host device has been running.",
    "tools": [UptimeTool()],
}
