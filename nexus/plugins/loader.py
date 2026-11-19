"""Drop-in tool-plugin auto-discovery for Nexus.

While :mod:`nexus.plugins` (the PluginManager above) handles *hook* plugins
(middleware), this loader handles *tool* plugins: drop a ``*.py`` file — or
a subdirectory containing ``plugin.py`` — into ``~/.nexus/plugins/`` and its
tools land in the same ToolRegistry the orchestrator uses.

Plugin contract (see ``examples/plugins/nexus-uptime.py`` for a working,
documented example)::

    # ~/.nexus/plugins/my-plugin.py
    from nexus.tools.base import BaseTool, ToolDefinition, ToolResult

    PLUGIN = {
        "name": "my-plugin",        # slug: [a-z0-9][a-z0-9_-]*
        "version": "1.0.0",
        "description": "What it does",
        "tools": [MyTool()],        # BaseTool instances (or subclasses)
    }

Alternatively the module may expose ``register(registry)`` and register
tools itself (prefix names with ``plugin_<name>_`` to avoid collisions).

Guarantees: each plugin is imported in isolation inside try/except — a
broken plugin is *listed with its error* and never crashes boot. Tool names
are registered as ``plugin_<name>_<toolname>``; duplicates (against the
registry or within one scan) are skipped with a warning.

Public API (also consumed by the TUI/REPL ``/plugins`` panel):

- :func:`discover_plugins`   — scan + import + validate (no side effects)
- :func:`register_plugin_tools` — land validated tools in a ToolRegistry
- :func:`list_plugins`       — last scan (cached; scans on first call)
- :func:`reload_plugins`     — force a re-scan (``nexus plugins --reload``)
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NAME_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Env override so tests (and paranoid users) can point the loader elsewhere.
_PLUGINS_DIR_ENV = "NEXUS_PLUGINS_DIR"


def default_plugins_dir() -> Path:
    """Directory scanned for tool plugins."""
    override = os.environ.get(_PLUGINS_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".nexus" / "plugins"


@dataclass
class PluginRecord:
    """One discovered plugin and what happened to it."""

    name: str
    version: str = ""
    description: str = ""
    source: str = ""  # path of the .py file / plugin.py
    kind: str = "manifest"  # manifest | register | invalid
    status: str = "loaded"  # loaded | failed
    tools: list[str] = field(default_factory=list)  # registered tool names
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "loaded"


def _candidate_paths(plugins_dir: Path) -> list[Path]:
    """*.py files + subdirs containing plugin.py, sorted for stable output."""
    try:
        entries = sorted(plugins_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    found: list[Path] = []
    for entry in entries:
        try:
            if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                found.append(entry)
            elif entry.is_dir() and not entry.name.startswith(("_", ".")):
                inner = entry / "plugin.py"
                if inner.is_file():
                    found.append(inner)
        except OSError:
            continue
    return found


def _import_module(path: Path) -> Any:
    """Import one plugin file in isolation (no package context, no sys.modules entry)."""
    stem = path.parent.name if path.name == "plugin.py" else path.stem
    module_name = f"nexus_plugin_{re.sub(r'[^a-zA-Z0-9_-]+', '_', stem)}_{abs(hash(str(path)) % 10_000)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(module_name, None)
    spec.loader.exec_module(module)
    return module


def _as_tool(obj: Any) -> tuple[Any, str | None]:
    """Coerce a manifest entry to a BaseTool instance, or return (obj, error)."""
    # Import lazily: loader.py sits under nexus.plugins which base.py lazily
    # imports too, so a top-level import would risk an import cycle.
    try:
        from nexus.tools.base import BaseTool
    except Exception as e:  # pragma: no cover — core tools broken is fatal anyway
        return None, f"cannot import BaseTool: {e}"
    try:
        if isinstance(obj, BaseTool):
            return obj, None
        if isinstance(obj, type) and issubclass(obj, BaseTool):
            return obj(), None  # allow registering the class itself
    except Exception as e:
        return None, f"tool {obj!r} could not be instantiated: {e}"
    return None, f"tool {obj!r} is not a BaseTool instance or subclass"


def _load_one(path: Path) -> PluginRecord:
    """Import + validate a single plugin file. Never raises."""
    record = PluginRecord(name=path.stem, source=str(path))
    try:
        module = _import_module(path)
    except Exception as e:
        record.status = "failed"
        record.kind = "invalid"
        record.error = f"import failed: {e}"
        return record

    manifest = getattr(module, "PLUGIN", None)
    if isinstance(manifest, dict):
        name = manifest.get("name")
        if not isinstance(name, str) or not _NAME_SLUG_RE.match(name.strip()):
            record.status = "failed"
            record.kind = "invalid"
            record.error = (
                f"invalid plugin name {name!r} — must be a slug like 'my-plugin' "
                "(lowercase letters, digits, '-', '_')"
            )
            return record
        record.name = name.strip()
        record.kind = "manifest"
        record.version = str(manifest.get("version", "1.0.0"))
        record.description = str(manifest.get("description", ""))

        tools = manifest.get("tools", [])
        if not isinstance(tools, (list, tuple)):
            record.status = "failed"
            record.error = "'tools' must be a list of BaseTool instances"
            return record
        record.tools = []  # cleared; filled with *provisional* names on register
        bad = []
        for entry in tools:
            tool, err = _as_tool(entry)
            if tool is None:
                bad.append(err or str(entry))
            else:
                record.tools.append(_tool_full_name(record.name, tool))
        if bad:
            record.status = "failed"
            record.tools = []
            record.error = "; ".join(bad)
            return record
        if not record.tools:
            record.warnings.append("manifest declares no tools")
        return record

    register = getattr(module, "register", None)
    if callable(register):
        record.kind = "register"
        record.status = "loaded"
        record.tools = []  # unknown until register() runs against a registry
        record.warnings.append(
            "register() plugin: name your tools 'plugin_<name>_...' to avoid collisions"
        )
        return record

    record.status = "failed"
    record.kind = "invalid"
    record.error = "no PLUGIN manifest dict and no register(registry) function"
    return record


def _base_tool_name(tool: Any) -> str:
    try:
        return str(tool.definition.name)
    except Exception:
        return "unnamed"


# ── scan cache (process-wide; `nexus plugins --reload` / reload_plugins refresh) ──

_last_scan: list[PluginRecord] | None = None
_last_scan_dir: str = ""


def discover_plugins(plugins_dir: Path | None = None, force: bool = False) -> list[PluginRecord]:
    """Scan the plugin directory: import each candidate, validate, never raise.

    Results are cached process-wide (see :func:`list_plugins`); pass
    ``force=True`` to re-scan even if a previous scan exists.
    """
    global _last_scan, _last_scan_dir
    directory = Path(plugins_dir) if plugins_dir else default_plugins_dir()
    if not force and _last_scan is not None and _last_scan_dir == str(directory):
        return _last_scan

    records: list[PluginRecord] = []
    for path in _candidate_paths(directory):
        try:
            records.append(_load_one(path))
        except Exception as e:  # belt and braces: one wild plugin file
            records.append(PluginRecord(
                name=path.stem, source=str(path), kind="invalid",
                status="failed", error=f"unexpected loader error: {e}",
            ))
    _last_scan = records
    _last_scan_dir = str(directory)
    return records


def reload_plugins(plugins_dir: Path | None = None) -> list[PluginRecord]:
    """Force a fresh scan (mirrors ``nexus plugins --reload``)."""
    return discover_plugins(plugins_dir, force=True)


def list_plugins(plugins_dir: Path | None = None) -> list[PluginRecord]:
    """Last scan results; scans on first call. Safe to call repeatedly."""
    return discover_plugins(plugins_dir, force=False)


def _tool_full_name(plugin_name: str, tool: Any) -> str:
    """Register tool names as plugin_<name>_<toolname> to avoid collisions."""
    raw = _base_tool_name(tool)
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("_") or "tool"
    safe = re.sub(r"^plugin_[A-Za-z0-9_-]+_", "", safe)  # avoid plugin_x_plugin_y_z
    return f"plugin_{plugin_name}_{safe}"


def _renamed_tool(tool: Any, full_name: str) -> Any:
    """Wrap a plugin tool so it registers under its collision-safe name.

    ``definition`` is often a computed property, so mutating its ``name``
    does not stick; a thin proxy returns a renamed copy instead.
    """
    try:
        from dataclasses import replace

        from nexus.tools.base import BaseTool, ToolDefinition

        class _RenamedTool(BaseTool):
            def __init__(self, inner: Any):
                super().__init__()
                self._inner = inner
                self._full_name = full_name

            @property
            def definition(self) -> ToolDefinition:
                base = self._inner.definition
                if isinstance(base, ToolDefinition) and base.name != self._full_name:
                    return replace(base, name=self._full_name)
                return base

            async def execute(self, **kwargs) -> Any:
                return await self._inner.execute(**kwargs)

        return _RenamedTool(tool)
    except Exception:
        return None


def register_plugin_tools(registry: Any, plugins_dir: Path | None = None) -> list[str]:
    """Land discovered plugin tools in ``registry`` (same one the orchestrator uses).

    Mirrors ``nexus.mcp.register_mcp_tools``: lazy, non-fatal, returns the
    registered tool names. Manifest plugins get ``plugin_<name>_<toolname>``
    names; duplicates are skipped with a warning. ``register(registry)``
    plugins are invoked directly and manage their own names.
    """
    registered: list[str] = []
    try:
        records = discover_plugins(plugins_dir)
    except Exception:
        return registered
    try:
        existing = {d.name for d in registry.list_all()}
    except Exception:
        existing = set()

    for record in records:
        if not record.ok:
            logger.warning("plugin '%s' failed to load: %s", record.name, record.error)
            continue
        if record.kind == "register":
            try:
                module = _import_module(Path(record.source))
                before = {d.name for d in registry.list_all()}
                module.register(registry)
                added = [n for n in (d.name for d in registry.list_all()) if n not in before]
                record.tools = added
                registered.extend(added)
                for name in added:
                    if name in existing:
                        logger.warning("plugin '%s' overwrote existing tool '%s'", record.name, name)
                existing.update(added)
            except Exception as e:
                record.status = "failed"
                record.error = f"register() failed: {e}"
                record.tools = []
                logger.warning("plugin '%s' register() failed: %s", record.name, e)
            continue

        # Manifest plugin: re-import to get fresh tool instances (records only
        # carry names; instances must not be shared across registries).
        names: list[str] = []
        try:
            module = _import_module(Path(record.source))
            manifest = getattr(module, "PLUGIN", {})
            entries = manifest.get("tools", []) if isinstance(manifest, dict) else []
            for entry in entries:
                tool, err = _as_tool(entry)
                if tool is None:
                    record.warnings.append(err or "invalid tool entry skipped")
                    continue
                full_name = _tool_full_name(record.name, tool)
                if full_name in existing:
                    msg = f"tool '{full_name}' already registered — skipped"
                    record.warnings.append(msg)
                    logger.warning("plugin '%s': %s", record.name, msg)
                    continue
                wrapped = _renamed_tool(tool, full_name)
                if wrapped is None:
                    record.warnings.append(f"cannot wrap tool '{full_name}'")
                    continue
                registry.register(wrapped)
                existing.add(full_name)
                names.append(full_name)
        except Exception as e:
            record.status = "failed"
            record.error = f"tool registration failed: {e}"
            for n in names:
                record.warnings.append(f"'{n}' was registered before the failure")
            logger.warning("plugin '%s' failed during registration: %s", record.name, e)
            continue
        record.tools = names
        registered.extend(names)
    return registered
