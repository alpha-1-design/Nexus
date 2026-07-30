"""REST API layer for the Nexus dashboard.

Provides all data-access methods used by the Flask routes.
Kept clean — no HTTP, no request/response objects.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..agent import AgentOrchestrator
from ..agents import AgentRole, MultiAgentTeam
from ..config import NexusConfig, ProviderConfig, load_config, save_config
from ..memory import VectorMemory, get_memory
from ..providers import get_manager
from ..skills import SkillsManager
from ..tools import get_registry
from ..utils import run_async


class NexusAPI:
    """Pure data-access layer. No request/response objects."""

    def __init__(self):
        self._orchestrator: AgentOrchestrator | None = None
        self._skills_manager: SkillsManager | None = None
        self._vector_memory: VectorMemory | None = None
        self._team: MultiAgentTeam | None = None

    # ── helpers ──────────────────────────────────────────────

    def _get_config(self) -> NexusConfig:
        return load_config()

    def _get_pm(self):
        return get_manager()

    def _get_skills(self) -> SkillsManager:
        if self._skills_manager is None:
            self._skills_manager = SkillsManager()
            self._skills_manager.load_all()
        return self._skills_manager

    def _get_vector_memory(self) -> VectorMemory:
        if self._vector_memory is None:
            cfg = self._get_config()
            self._vector_memory = VectorMemory(backend=cfg.search_provider or "keyword")
        return self._vector_memory

    def _get_team(self) -> MultiAgentTeam:
        if self._team is None:
            self._team = MultiAgentTeam(lead_name="nexus", provider_manager=self._get_pm())
        return self._team

    # ── status ───────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        cfg = self._get_config()
        pm = self._get_pm()
        skills = self._get_skills()
        memory = get_memory()
        return {
            "nexus_version": "0.1.0",
            "config_dir": str(cfg.config_dir),
            "providers": {
                "configured": list(pm.configs.keys()),
                "active": pm.active_provider,
            },
            "skills": {
                "total": len(skills.list_all()),
                "active": skills.list_active(),
                "categories": skills.list_categories(),
            },
            "memory": {
                "facts": len(memory._facts),
                "sessions": len(memory.list_sessions(limit=1)),
            },
            "vector_memory": {"entries": run_async(self._get_vector_memory().count())},
            "system": {
                "python_version": __import__("sys").version.split()[0],
                "platform": __import__("platform").platform(),
            },
            "termux_env": cfg.termux_mode,
        }

    # ── providers ────────────────────────────────────────────

    def get_providers(self) -> dict[str, Any]:
        pm = self._get_pm()
        return {
            "providers": [
                {
                    "name": name,
                    "type": cfg.provider_type,
                    "model": cfg.model or "default",
                    "base_url": cfg.base_url or "",
                    "active": name == pm.active_provider,
                }
                for name, cfg in pm.configs.items()
            ]
        }

    def add_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        pm = self._get_pm()
        cfg = ProviderConfig(
            name=data["name"],
            provider_type=data["provider_type"],
            model=data.get("model"),
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
        )
        pm.add_provider(cfg)
        return {"status": "success", "provider": data["name"]}

    def remove_provider(self, name: str) -> dict[str, Any]:
        cfg = self._get_config()
        if name in cfg.providers:
            del cfg.providers[name]
            save_config(cfg)
            if cfg.active_provider == name:
                cfg.active_provider = next(iter(cfg.providers)) if cfg.providers else ""
                save_config(cfg)
        return {"status": "removed", "provider": name}

    def set_active_provider(self, name: str) -> dict[str, Any]:
        cfg = self._get_config()
        pm = self._get_pm()
        if name not in cfg.providers:
            return {"status": "error", "error": f"Provider '{name}' not found"}
        cfg.active_provider = name
        save_config(cfg)
        pm.set_active(name)
        return {"status": "success", "active": name}

    # ── tools ────────────────────────────────────────────────

    def get_tools(self) -> dict[str, Any]:
        registry = get_registry()
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "category": t.category,
                    "requires_permission": t.requires_permission,
                }
                for t in registry.list_all()
            ]
        }

    # ── skills ───────────────────────────────────────────────

    def get_skills(self) -> dict[str, Any]:
        skills = self._get_skills()
        active = skills.list_active()
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "category": s.category,
                    "tags": s.tags,
                    "active": s.name in active,
                }
                for s in skills.list_all()
            ]
        }

    def activate_skill(self, name: str) -> dict[str, Any]:
        skills = self._get_skills()
        success = skills.activate(name)
        return {"status": "success" if success else "error", "skill": name}

    # ── mcp marketplace ──────────────────────────────────────

    def _load_mcp_catalog(self) -> dict[str, Any]:
        import json
        catalog_path = Path(__file__).parent.parent / "data" / "mcp_catalog.json"
        if not catalog_path.exists():
            return {}
        try:
            return json.loads(catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _mcp_config_path(self) -> Path:
        return Path.home() / ".nexus" / "mcp-servers.json"

    def _load_installed_mcp(self) -> dict[str, Any]:
        import json
        path = self._mcp_config_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get_mcp_catalog(self, query: str = "", category: str = "") -> dict[str, Any]:
        catalog = self._load_mcp_catalog()
        installed = self._load_installed_mcp()
        q = query.lower().strip()
        cat = category.strip()
        items = []
        for name, cfg in catalog.items():
            if cat and cfg.get("category") != cat:
                continue
            if q and q not in name.lower() and q not in cfg.get("description", "").lower() and q not in cfg.get("category", "").lower():
                continue
            items.append({
                "name": name,
                "description": cfg.get("description", ""),
                "category": cfg.get("category", "other"),
                "transport": cfg.get("transport", "stdio"),
                "env_vars": cfg.get("env_vars", []),
                "installed": name in installed,
            })
        items.sort(key=lambda i: (i["category"], i["name"]))
        categories = sorted({cfg.get("category", "other") for cfg in catalog.values()})
        return {"servers": items, "categories": categories, "total": len(catalog)}

    def get_installed_mcp(self) -> dict[str, Any]:
        installed = self._load_installed_mcp()
        return {
            "servers": [
                {"name": name, **cfg} for name, cfg in installed.items()
            ]
        }

    def install_mcp(self, name: str) -> dict[str, Any]:
        import json
        catalog = self._load_mcp_catalog()
        cfg = catalog.get(name)
        if cfg is None:
            return {"status": "error", "message": f"'{name}' not found in catalog"}

        servers = self._load_installed_mcp()
        entry: dict[str, Any] = {"transport": cfg.get("transport", "stdio")}
        if entry["transport"] == "sse":
            entry["url"] = cfg.get("url", "")
        else:
            entry["command"] = cfg.get("command", "")
            entry["args"] = cfg.get("args", [])
        env_vars = cfg.get("env_vars") or []
        if env_vars:
            entry["env"] = {k: "" for k in env_vars}

        servers[name] = entry
        path = self._mcp_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(servers, indent=2))
        return {"status": "success", "name": name, "requires_env": env_vars}

    def uninstall_mcp(self, name: str) -> dict[str, Any]:
        import json
        servers = self._load_installed_mcp()
        if name not in servers:
            return {"status": "error", "message": f"'{name}' is not installed"}
        del servers[name]
        path = self._mcp_config_path()
        path.write_text(json.dumps(servers, indent=2))
        return {"status": "success", "name": name}

    # ── memory ───────────────────────────────────────────────

    def search_memory(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        vm = self._get_vector_memory()
        entries = run_async(vm.recall(query, limit=limit))
        return [
            {
                "id": e.id,
                "content": e.content[:200],
                "metadata": e.metadata,
                "created_at": e.created_at,
                "access_count": e.access_count,
            }
            for e in entries
        ]

    def store_memory(self, content: str, metadata: dict | None = None) -> dict[str, Any]:
        entry_id = run_async(self._get_vector_memory().store(content, metadata))
        return {"status": "success", "id": entry_id}

    def get_facts(self) -> dict[str, Any]:
        memory = get_memory()
        return {"facts": [asdict(fact) for fact in memory._facts.values()]}

    def add_fact(self, key: str, value: Any, category: str = "general") -> dict[str, Any]:
        get_memory().add_fact(key, value, category)
        return {"status": "success", "key": key}

    def list_sessions(self, limit: int = 20) -> dict[str, Any]:
        return {"sessions": [s.to_dict() for s in get_memory().list_sessions(limit=limit)]}

    # ── agent / tasks ────────────────────────────────────────

    async def run_agent_task(self, task: str, stream_callback=None) -> dict[str, Any]:
        if self._orchestrator is None:
            pm = self._get_pm()
            self._orchestrator = AgentOrchestrator(
                provider_manager=pm,
                tool_registry=get_registry(),
                memory=get_memory(),
            )
        turn = await self._orchestrator.run(task, stream_callback=stream_callback)
        return {
            "message": turn.assistant_message,
            "tool_calls": len(turn.tool_calls),
            "duration_ms": turn.duration_ms,
            "error": turn.error,
        }

    def get_agent_stats(self) -> dict[str, Any]:
        if self._orchestrator is None:
            return {"turns": 0, "tool_calls": 0, "history": []}
        return {
            "turns": self._orchestrator.turn_count,
            "tool_calls": self._orchestrator.tool_call_count,
            "history": [
                {
                    "user": t.user_message[:100],
                    "assistant": t.assistant_message[:100] if t.assistant_message else "",
                    "tool_calls": len(t.tool_calls),
                    "error": t.error,
                }
                for t in self._orchestrator.get_history()
            ],
        }

    def list_agents(self) -> dict[str, Any]:
        agents = getattr(self._get_team(), "_agents", [])
        return {
            "agents": [
                {
                    "id": a.agent_id,
                    "name": a.name,
                    "role": a.role.value if hasattr(a.role, "value") else str(a.role),
                    "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                }
                for a in agents
            ]
        }

    def spawn_agent(self, task: str, role: str = "coder", name: str | None = None) -> dict[str, Any]:
        team = self._get_team()
        role_map = {r.value: r for r in AgentRole}
        agent_role = role_map.get(role, AgentRole.CODER)
        try:
            agent = team.spawn(task=task, role=agent_role, name=name)
            return {"status": "spawned", "id": agent.agent_id, "name": agent.name, "role": role}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def kill_agent(self, agent_id: str) -> dict[str, Any]:
        team = self._get_team()
        if hasattr(team, "remove_agent"):
            team.remove_agent(agent_id)
            return {"status": "killed", "id": agent_id}
        return {"status": "error", "error": "Agent removal not supported"}

    # ── projects ─────────────────────────────────────────────

    def list_projects(self, root: str | None = None) -> dict[str, Any]:
        base = Path(root or ".").resolve()
        if not base.exists():
            return {"projects": [], "error": "Directory not found"}
        projects = []
        try:
            for entry in sorted(base.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir() and self._looks_like_project(entry):
                    projects.append({
                        "name": entry.name,
                        "path": str(entry),
                        "type": self._detect_project_type(entry),
                    })
        except PermissionError:
            pass
        return {"projects": projects, "root": str(base)}

    def get_project_tree(self, project_path: str, max_depth: int = 4) -> dict[str, Any]:
        base = Path(project_path).resolve()
        if not base.exists():
            return {"error": "Not found", "path": project_path}
        return {"root": str(base), "tree": self._build_tree(base, max_depth)}

    def read_project_file(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            return {"error": "File not found", "path": file_path}
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return {"path": str(path), "content": content, "size": len(content)}
        except Exception as e:
            return {"error": str(e), "path": file_path}

    def write_project_file(self, file_path: str, content: str) -> dict[str, Any]:
        path = Path(file_path).resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"status": "saved", "path": str(path), "size": len(content)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _looks_like_project(self, path: Path) -> bool:
        markers = {".git", "README.md", "package.json", "pyproject.toml", "Cargo.toml",
                   "go.mod", "Gemfile", "Makefile", "setup.py", "index.html"}
        return any((path / m).exists() for m in markers) or bool(list(path.glob("*.sln")))

    def _detect_project_type(self, path: Path) -> str:
        if (path / "pyproject.toml").exists() or (path / "setup.py").exists():
            return "python"
        if (path / "package.json").exists():
            return "node"
        if (path / "Cargo.toml").exists():
            return "rust"
        if (path / "go.mod").exists():
            return "go"
        if (path / "Gemfile").exists():
            return "ruby"
        if (path / "Makefile").exists() or (path / "CMakeLists.txt").exists():
            return "c/c++"
        return "unknown"

    def _build_tree(self, path: Path, max_depth: int, _depth: int = 0) -> list[dict[str, Any]]:
        if _depth > max_depth:
            return []
        entries = []
        try:
            for entry in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if entry.name.startswith(".") or entry.name.startswith("__"):
                    continue
                node = {"name": entry.name, "type": "dir" if entry.is_dir() else "file", "path": str(entry)}
                if entry.is_dir():
                    node["children"] = self._build_tree(entry, max_depth, _depth + 1)
                else:
                    try:
                        node["size"] = entry.stat().st_size
                    except OSError:
                        node["size"] = 0
                entries.append(node)
        except PermissionError:
            pass
        return entries

    # ── settings ─────────────────────────────────────────────

    def get_settings(self) -> dict[str, Any]:
        cfg = self._get_config()
        return {
            "active_provider": cfg.active_provider,
            "tool_profile": cfg.tool_profile,
            "search_provider": cfg.search_provider,
            "log_level": cfg.log_level,
            "sandbox_mode": cfg.sandbox_mode,
            "termux_mode": cfg.termux_mode,
            "user_name": getattr(cfg, "user_name", ""),
            "config_dir": str(cfg.config_dir),
        }

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = self._get_config()
        for key in ("tool_profile", "search_provider", "log_level", "sandbox_mode", "termux_mode", "user_name"):
            if key in data:
                setattr(cfg, key, data[key])
        save_config(cfg)
        return {"status": "saved", "settings": self.get_settings()}

    # ── automation ───────────────────────────────────────────

    def get_automation_status(self) -> dict[str, Any]:
        browser_available = False
        has_session = False
        try:
            from ..automation.browser import BrowserManager, is_browser_available
            browser_available = is_browser_available()
            has_session = BrowserManager.get() is not None
        except Exception:
            pass
        httpx_available = False
        try:
            import httpx
            httpx_available = True
        except ImportError:
            pass
        registry = get_registry()
        browser_tools = [t for t in registry.list_all() if t.category == "automation"]
        return {
            "playwright_available": browser_available,
            "browser_session_active": has_session,
            "httpx_available": httpx_available,
            "automation_tools": [t.name for t in browser_tools],
            "tool_count": len(browser_tools),
        }

    def run_automation_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        registry = get_registry()
        tool = registry.get(tool_name)
        if not tool:
            return {"success": False, "error": f"Tool '{tool_name}' not found"}
        if tool.category != "automation":
            return {"success": False, "error": f"Tool '{tool_name}' is not an automation tool"}
        try:
            result = run_async(tool.execute(**params))
            return {"success": result.success, "content": result.content, "error": result.error}
        except Exception as e:
            return {"success": False, "error": str(e)}


_api: NexusAPI | None = None


def get_api() -> NexusAPI:
    global _api
    if _api is None:
        _api = NexusAPI()
    return _api
