"""Nexus Doctor — Comprehensive self-diagnostic and system health monitor."""

import os
import platform
import shutil
from pathlib import Path
from typing import Any

from .config import ProviderConfig, load_config, save_config
from .steward import NexusSteward


def _color(s: str, code: int, bold: bool = False) -> str:
    b = "1;" if bold else ""
    return f"\033[{b}{code}m{s}\033[0m"


def _ok(s: str = "OK") -> str:
    return _color(f"\u2713 {s}", 32)


def _fail(s: str = "FAIL") -> str:
    return _color(f"\u2717 {s}", 31)


def _warn(s: str = "WARN") -> str:
    return _color(f"\u26A0 {s}", 33)


def _info(s: str) -> str:
    return _color(s, 36)


def _dim(s: str) -> str:
    return _color(s, 90)


class NexusDoctor:
    """Performs deep diagnostics and environment health checks."""

    def __init__(self):
        self.config = load_config()
        self.steward = NexusSteward(Path("."))
        self.health_checks = {
            "dependencies": self._check_dependencies,
            "environment": self._check_environment,
            "config": self._check_config,
            "provider": self._check_provider,
            "memory": self._check_memory,
            "tools": self._check_tools,
            "network": self._check_network,
            "cache": self._check_cache,
            "git": self._check_git,
            "system": self._check_system,
        }

    def _check_system(self) -> dict[str, Any]:
        """Check system resources."""
        info = {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "cwd": os.getcwd(),
        }
        load_avg = None
        try:
            load_avg = os.getloadavg()
            info["load_1m"] = f"{load_avg[0]:.2f}"
            info["load_5m"] = f"{load_avg[1]:.2f}"
            info["load_15m"] = f"{load_avg[2]:.2f}"
        except (OSError, AttributeError):
            info["load"] = "N/A"

        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        total_kb = int(line.split()[1])
                        info["memory_total"] = f"{total_kb // 1024 // 1024} GB"
                    elif line.startswith("MemAvailable"):
                        avail_kb = int(line.split()[1])
                        info["memory_available"] = f"{avail_kb // 1024 // 1024} GB"
        except Exception:
            info["memory"] = "N/A"

        total, used, free = shutil.disk_usage(os.getcwd())
        info["disk_total"] = f"{total // (2**30)} GB"
        info["disk_used"] = f"{used // (2**30)} GB"
        info["disk_free"] = f"{free // (2**30)} GB"
        info["disk_usage_pct"] = f"{used / total * 100:.0f}%"

        info["cpus"] = str(os.cpu_count())
        return {"passed": True, **info}

    def _check_cache(self) -> dict[str, Any]:
        """Check for non-essential cache files."""
        targets = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", ".ipynb_checkpoints", ".eslintcache", ".DS_Store"}
        found = []
        total_size = 0

        for root, dirs, files in os.walk("."):
            if ".git" in dirs:
                dirs.remove(".git")
            if "node_modules" in dirs:
                cache_path = Path(root) / "node_modules" / ".cache"
                if cache_path.exists():
                    found.append(str(cache_path))
                    for f in cache_path.rglob("*"):
                        if f.is_file():
                            total_size += f.stat().st_size
                dirs.remove("node_modules")
            for target in targets:
                if target in dirs:
                    path = Path(root) / target
                    found.append(str(path))
                    for f in path.rglob("*"):
                        if f.is_file():
                            total_size += f.stat().st_size
                    if target in dirs:
                        dirs.remove(target)
                if target in files:
                    path = Path(root) / target
                    found.append(str(path))
                    total_size += path.stat().st_size

        return {
            "passed": total_size < 100 * 1024 * 1024,
            "found_count": len(found),
            "total_size_bytes": total_size,
            "paths": found[:10],
            "all_paths": found,
        }

    def tactical_cleanup(self, dry_run: bool = True) -> dict[str, Any]:
        """Scans and removes non-essential cache artifacts."""
        report = self._check_cache()
        all_paths = report.get("all_paths", [])
        total_size = report.get("total_size_bytes", 0)

        if not dry_run:
            for path_str in all_paths:
                path = Path(path_str)
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except Exception:
                    pass

        from .utils import format_bytes
        return {
            "freed_bytes": total_size if not dry_run else 0,
            "potential_savings": format_bytes(total_size),
            "files_removed": len(all_paths) if not dry_run else 0,
            "dry_run": dry_run,
        }

    def _check_git(self) -> dict[str, Any]:
        """Check git status."""
        is_repo = self.steward._is_git_repo()
        status = self.steward.get_status().strip() or "Clean"
        branch = "N/A"
        try:
            import subprocess
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            pass
        return {"passed": is_repo, "is_repo": is_repo, "branch": branch, "status": status}

    def run_all(self) -> dict[str, Any]:
        results = {}
        for name, check in self.health_checks.items():
            try:
                results[name] = check()
            except Exception as e:
                results[name] = {"passed": False, "error": str(e)}
        return results

    def print_report(self, report: dict[str, Any] | None = None) -> None:
        """Print a formatted diagnostic report with visual flair."""
        if report is None:
            report = self.run_all()

        term_width = 72
        try:
            import shutil
            term_width = min(shutil.get_terminal_size().columns, 80)
        except Exception:
            pass

        c = "\033[36m"
        g = "\033[32m"
        r = "\033[31m"
        y = "\033[33m"
        d = "\033[90m"
        b = "\033[1m"
        w = "\033[97m"
        n = "\033[0m"

        def hr(char: str = "\u2500", width: int = term_width - 4) -> str:
            return f"  {d}{char * width}{n}"

        def gauge(pct: float, width: int = 20) -> str:
            filled = int(pct * width)
            bar = "\u2588" * filled + "\u2591" * (width - filled)
            if pct >= 0.8:
                col = g
            elif pct >= 0.5:
                col = y
            else:
                col = r
            return f"{col}{bar}{n}"

        # === HEADER ===
        print()
        print(f"  {c}{b}\u250C" + "\u2500" * (term_width - 6) + "\u2510{n}")
        print(f"  {c}{b}\u2502{n}  {w}{b}NEXUS SYSTEM DIAGNOSTICS{n}" + " " * (term_width - 32) + f"{c}{b}\u2502{n}")
        print(f"  {c}{b}\u2502{n}  {d}Comprehensive self-diagnostic and health check{n}" + " " * (term_width - 47) + f"{c}{b}\u2502{n}")
        print(f"  {c}{b}\u2514" + "\u2500" * (term_width - 6) + "\u2518{n}")
        print()

        # === SYSTEM VITALS SUMMARY ===
        sys_data = report.get("system", {})
        if sys_data:
            os_info = sys_data.get("os", "?")
            py_ver = sys_data.get("python", "?")
            cpus = sys_data.get("cpus", "?")
            mem_total = sys_data.get("memory_total", "?")
            disk_total = sys_data.get("disk_total", "?")
            disk_used = sys_data.get("disk_used", "?")
            disk_free = sys_data.get("disk_free", "?")
            load_1m = sys_data.get("load_1m", "?")
            load_5m = sys_data.get("load_5m", "?")
            load_15m = sys_data.get("load_15m", "?")

            print(f"  {c}\u250C" + "\u2500" * (term_width - 6) + "\u2510{n}")
            print(f"  {c}\u2502{n}  {b}{w}SYSTEM{n}" + " " * (term_width - 14) + f"{c}\u2502{n}")

            rows = [
                ("OS", f"{os_info}"),
                ("Python", f"{py_ver}"),
                ("CPUs", f"{cpus} cores"),
                ("Memory", f"{mem_total}"),
                ("Disk", f"{disk_used} / {disk_total}  ({disk_free} free)"),
                ("Load", f"1m: {load_1m}  5m: {load_5m}  15m: {load_15m}"),
            ]
            for label, val in rows:
                padded = label.rjust(8)
                print(f"  {c}\u2502{n}   {d}{padded}{n}  {w}{val}{n}" + " " * (term_width - 22 - len(val)) + f"{c}\u2502{n}")
            print(f"  {c}\u2514" + "\u2500" * (term_width - 6) + "\u2518{n}")
            print()

        # === CHECKS ===
        print(f"  {c}\u250C" + "\u2500" * (term_width - 6) + "\u2510{n}")
        print(f"  {c}\u2502{n}  {b}{w}COMPONENT STATUS{n}" + " " * (term_width - 24) + f"{c}\u2502{n}")
        print(f"  {c}\u2502{n}" + " " * (term_width - 4) + f"{c}\u2502{n}")

        check_order = ["dependencies", "environment", "config", "provider", "memory", "tools", "network", "cache", "git"]
        total_checks = len(check_order)
        passed_checks = 0

        for category in check_order:
            result = report.get(category, {})
            if not result:
                continue
            passed = result.get("passed", True) and "error" not in result
            if passed:
                passed_checks += 1

            # Status icon
            if passed:
                icon = f"{g}\u2713{n}"
            elif result.get("error"):
                icon = f"{r}\u2717{n}"
            else:
                icon = f"{y}\u26A0{n}"

            # Primary label
            label = category.replace("_", " ").upper()
            detail_parts = []

            if category == "dependencies" and "details" in result:
                deps = result["details"]
                good = sum(1 for v in deps.values() if v)
                total = len(deps)
                detail_parts.append(f"{good}/{total}")
            elif category == "config":
                detail_parts.append(result.get("active_provider", "none"))
            elif category == "provider":
                detail_parts.append(result.get("active_provider", "none"))
                if result.get("reachable") is True:
                    detail_parts.append(f"{g}reachable{n}")
                elif result.get("reachable") is False:
                    detail_parts.append(f"{r}unreachable{n}")
            elif category == "memory":
                detail_parts.append(f"{result.get('fact_count', 0)} facts")
            elif category == "tools":
                detail_parts.append(f"{result.get('tool_count', 0)} tools")
            elif category == "network":
                hosts = result.get("hosts", {})
                good_hosts = sum(1 for v in hosts.values() if v)
                detail_parts.append(f"{good_hosts}/{len(hosts)} hosts")
            elif category == "cache":
                count = result.get("found_count", 0)
                from nexus.utils import format_bytes
                size = format_bytes(result.get("total_size_bytes", 0))
                detail_parts.append(f"{count} artifacts ({size})")
            elif category == "git":
                detail_parts.append(result.get("branch", "?"))

            detail_str = "  " + " | ".join(str(x) for x in detail_parts) if detail_parts else ""
            padding = max(2, term_width - 16 - len(label) - len(detail_str))
            print(f"  {c}\u2502{n}   {icon}  {b}{label}{n}{d}{detail_str}{n}" + " " * padding + f"{c}\u2502{n}")

        print(f"  {c}\u2502{n}" + " " * (term_width - 4) + f"{c}\u2502{n}")

        # === SUMMARY SCORE ===
        score = passed_checks / max(total_checks, 1)
        score_color = g if score >= 0.8 else y if score >= 0.5 else r
        bar = gauge(score, width=24)

        print(f"  {c}\u2502{n}  {d}Health Score:{n}  {bar}  {score_color}{b}{score:.0%}{n}" + " " * (term_width - 42) + f"{c}\u2502{n}")
        print(f"  {c}\u2502{n}  {d}Passed:{n} {g}{passed_checks}{n}/{total_checks}" + " " * (term_width - 30) + f"{c}\u2502{n}")
        print(f"  {c}\u2514" + "\u2500" * (term_width - 6) + "\u2518{n}")

        # === FAILURE DETAILS ===
        failures = [(cat, result) for cat, result in report.items()
                     if not (result.get("passed", True) and "error" not in result)]
        if failures:
            print()
            print(f"  {r}\u250C" + "\u2500" * (term_width - 6) + "\u2510{n}")
            print(f"  {r}\u2502{n}  {r}{b}ISSUES FOUND{n}" + " " * (term_width - 20) + f"{r}\u2502{n}")
            print(f"  {r}\u2502{n}" + " " * (term_width - 4) + f"{r}\u2502{n}")
            for cat, result in failures:
                label = cat.replace("_", " ").upper()
                error = result.get("error", "Check failed")
                print(f"  {r}\u2502{n}   {r}\u2717{n}  {b}{label}{n}  {d}{error[:70]}{n}" + " " * max(2, term_width - 80 - len(label)) + f"{r}\u2502{n}")
            print(f"  {r}\u2514" + "\u2500" * (term_width - 6) + "\u2518{n}")

        print()

    def discover_skills(self):
        """Invoke skill discovery."""
        from .skills.discovery import SkillDiscoverer
        discoverer = SkillDiscoverer(self.config.config_dir.parent)
        discoverer.discover()

    def _check_dependencies(self) -> dict[str, Any]:
        """Verify essential Python packages."""
        required = {
            "textual": "textual",
            "requests": "requests",
            "openai": "openai",
        }
        details = {}
        for pkg, _pip_name in required.items():
            try:
                import importlib
                importlib.import_module(pkg)
                details[pkg] = True
            except ImportError:
                details[pkg] = False
        return {"passed": all(details.values()), "details": details}

    def _check_environment(self) -> dict[str, Any]:
        """Check environment constraints."""
        import tempfile
        writable = os.access(".", os.W_OK)
        tmp_writable = True
        try:
            tempfile.mkstemp()
        except Exception:
            tmp_writable = False
        return {
            "passed": writable and tmp_writable,
            "os": os.name,
            "writable": writable,
            "tmp_writable": tmp_writable,
        }

    def _check_config(self) -> dict[str, Any]:
        """Check provider configuration."""
        configured = len(self.config.providers) > 0
        active = self.config.active_provider
        count = len(self.config.providers)
        return {
            "passed": configured,
            "configured": configured,
            "active_provider": active or "none",
            "provider_count": count,
            "providers": list(self.config.providers.keys()) if configured else [],
        }

    def _check_provider(self) -> dict[str, Any]:
        """Check if the active provider is reachable."""
        from .providers import get_manager
        mgr = get_manager()
        active = mgr.active_provider
        if not active:
            return {"passed": False, "active_provider": "none", "note": "No provider configured — set one with /provider or --provider"}

        try:
            import asyncio
            result = asyncio.run(mgr.check_connection())
            return {
                "passed": result,
                "active_provider": active,
                "reachable": result,
            }
        except Exception as e:
            return {"passed": False, "active_provider": active, "error": str(e)}

    def _check_memory(self) -> dict[str, Any]:
        """Check memory system status."""
        try:
            from .memory import get_memory
            memory = get_memory()
            facts = memory.get_all_facts() if hasattr(memory, 'get_all_facts') else {}
            return {
                "passed": True,
                "fact_count": len(facts),
            }
        except Exception as e:
            return {"passed": False, "error": str(e)}

    def _check_tools(self) -> dict[str, Any]:
        """Check registered tools."""
        try:
            from .tools import get_registry
            registry = get_registry()
            tools = registry.list_all()
            return {
                "passed": True,
                "tool_count": len(tools),
                "tools": [t.name for t in tools][:20],
            }
        except Exception as e:
            return {"passed": False, "error": str(e)}

    def _check_network(self) -> dict[str, Any]:
        """Quick network connectivity check."""
        import socket
        hosts = [("api.github.com", 443), ("pypi.org", 443)]
        results = {}
        all_ok = True
        for host, port in hosts:
            try:
                sock = socket.create_connection((host, port), timeout=3)
                sock.close()
                results[host] = True
            except Exception:
                results[host] = False
                all_ok = False
        return {"passed": all_ok, "hosts": results}

    def interactive_setup(self):
        """Guide user through provider configuration."""
        print(f"\n  {_info('Nexus configuration not found')}")
        print(f"  {_dim('Configure your AI provider to get started')}\n")

        providers = [
            {"name": "OpenAI", "type": "openai", "model": "gpt-4o"},
            {"name": "Groq", "type": "groq", "model": "llama-3.3-70b-versatile"},
            {"name": "Anthropic", "type": "anthropic", "model": "claude-3-5-sonnet-latest"},
            {"name": "Google Gemini", "type": "google", "model": "gemini-2.0-flash"},
        ]

        print("  " + _color("Select an AI provider:", 97))
        for i, p in enumerate(providers):
            print("  {}. {} {}".format(i + 1, _color(p["name"], 36), _dim("(" + p["model"] + ")")))

        choice = input("  " + _color("Enter choice (1-4):", 97) + " ")
        if not choice.isdigit() or int(choice) not in range(1, 5):
            print("  " + _warn("Invalid selection."))
            return

        p = providers[int(choice) - 1]
        key = input("  " + _color("Enter your " + p["name"] + " API key:", 97) + " ")

        new_provider = ProviderConfig(name=p["type"], provider_type=p["type"], api_key=key, model=p["model"])
        self.config.providers[p["type"]] = new_provider
        self.config.active_provider = p["type"]
        save_config(self.config)

        print("\n  " + _ok("Nexus bound to " + p["name"] + " using " + p["model"]) + "\n")


def run_doctor(interactive: bool = True):
    """Run full diagnostics and print the report."""
    doctor = NexusDoctor()
    report = doctor.run_all()
    doctor.print_report(report)

    if interactive and not report["config"]["configured"]:
        doctor.interactive_setup()

    doctor.discover_skills()
