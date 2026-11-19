"""Environment auto-discovery for Nexus.

Nexus runs on phones (Termux), laptops, WSL and containers. Instead of
assuming a desktop dev box, this module *detects* the device it is on:

- platform (termux / wsl / docker / macos / linux), python, shell
- which optional packages ("capabilities") are already installed
- which PATH tools (git, rg, ffmpeg, ...) are available
- RAM / CPU when psutil is around

The snapshot is cached at ``~/.nexus/environment.json`` (atomic write) and
auto-refreshes when older than 24 hours. Everything is probed defensively:
a weird system can never crash discovery, only ever produce a smaller card.

Public API (also consumed by the TUI/REPL boot checklist):

- :func:`discover_environment` — cached environment scan -> :class:`EnvironmentProfile`
- :func:`ensure_capabilities` — the skip-if-installed installer
- :func:`ensure_dependency` — drop-in replacement for
  :func:`nexus.utils.dependencies.ensure_dependency` (same signature)
- :func:`familiarize_project` — light project sniff (no full /init scan)
- :func:`boot_summary` — small dict for boot checklists, never raises
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── capability map ───────────────────────────────────────────────────────
# import name (find_spec key) -> pip spec. Keep in sync with the optional
# imports across the codebase: TUI (textual, rich), dashboard (flask,
# psutil), MCP (httpx), skills/plugins (yaml), automation (playwright),
# voice extras (sounddevice, speech_recognition, pyaudio, faster-whisper),
# networking (requests).
CAPABILITY_MAP: dict[str, str] = {
    "textual": "textual",
    "rich": "rich",
    "psutil": "psutil",
    "flask": "flask",
    "requests": "requests",
    "httpx": "httpx",
    "yaml": "pyyaml",
    "playwright": "playwright",
    "sounddevice": "sounddevice",
    "speech_recognition": "SpeechRecognition",
    "pyaudio": "pyaudio",
    "faster_whisper": "faster-whisper",
}

# PATH tools surfaced on the environment card.
PATH_TOOLS: list[str] = [
    "git", "node", "npm", "rg", "curl", "jq", "gh", "docker", "ffmpeg",
]

# Manifest files counted by familiarize_project (light sniff only — the
# full scan lives in nexus/project.py).
_MANIFESTS: tuple[str, ...] = (
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Gemfile",
    "Makefile", "CMakeLists.txt", "composer.json", "pom.xml", "build.gradle",
    "requirements.txt", "setup.py", "pubspec.yaml", "deno.json",
)

CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
PIP_TIMEOUT_SECONDS = 300

# Minimum free RAM (MB) for the boot checklist to call memory "ok".
MEMORY_OK_FLOOR_MB = 200


def _cache_path() -> Path:
    """Location of the environment snapshot (env override for tests)."""
    override = os.environ.get("NEXUS_ENVIRONMENT_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".nexus" / "environment.json"


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via a temp file + os.replace so a crash can't truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── profile ──────────────────────────────────────────────────────────────

@dataclass
class EnvironmentProfile:
    """What Nexus knows about the device it is running on."""

    platform: str = "linux"  # termux | wsl | docker | macos | linux
    platform_detail: str = ""
    python_version: str = ""
    python_executable: str = ""
    shell: str = "unknown"
    capabilities: dict[str, bool] = field(default_factory=dict)
    tools: dict[str, str | None] = field(default_factory=dict)
    ram_total_mb: int | None = None
    ram_available_mb: int | None = None
    cpu_cores: int | None = None
    scanned_at: str = ""  # ISO timestamp of the scan
    stale: bool = False  # True = served from an expired cache we failed to refresh

    def missing_capabilities(self) -> list[str]:
        """Capability names (import names) not present on this device."""
        return sorted(name for name, ok in self.capabilities.items() if not ok)

    def present_capabilities(self) -> list[str]:
        return sorted(name for name, ok in self.capabilities.items() if ok)

    def missing_tools(self) -> list[str]:
        return sorted(name for name, path in self.tools.items() if not path)

    def present_tools(self) -> list[str]:
        return sorted(name for name, path in self.tools.items() if path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "platform_detail": self.platform_detail,
            "python_version": self.python_version,
            "python_executable": self.python_executable,
            "shell": self.shell,
            "capabilities": dict(self.capabilities),
            "tools": dict(self.tools),
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "cpu_cores": self.cpu_cores,
            "scanned_at": self.scanned_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvironmentProfile":
        known = {f for f in cls.__dataclass_fields__ if f != "stale"}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)


# ── individual probes (each one fails alone, never together) ─────────────

def _detect_platform() -> tuple[str, str]:
    """Best-effort platform name + human detail."""
    try:
        if os.environ.get("TERMUX_VERSION") or os.path.isdir("/data/data/com.termux"):
            detail = f"Termux {os.environ.get('TERMUX_VERSION', '')}".strip()
            return "termux", detail or "Termux (Android)"
    except Exception:
        pass
    try:
        if Path("/.dockerenv").exists():
            return "docker", "Docker container"
    except Exception:
        pass
    system = platform.system()
    if system == "Darwin":
        return "macos", platform.mac_ver()[0] or "macOS"
    if system == "Linux":
        try:
            if "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower():
                return "wsl", "Windows Subsystem for Linux"
        except Exception:
            pass
        return "linux", " ".join(platform.platform().split()[:2])
    return system.lower() or "unknown", platform.platform()


def _module_available(import_name: str) -> bool:
    """Probe a module WITHOUT importing it (find_spec only, no side effects)."""
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:
        return False


def _probe_ram() -> tuple[int | None, int | None]:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return int(vm.total // (1024 * 1024)), int(vm.available // (1024 * 1024))
    except Exception:
        return None, None


def _scan_environment() -> EnvironmentProfile:
    """Fresh scan. Every probe is individually wrapped."""
    plat, detail = _detect_platform()
    capabilities: dict[str, bool] = {}
    for name in CAPABILITY_MAP:
        try:
            capabilities[name] = _module_available(name)
        except Exception:
            capabilities[name] = False

    tools: dict[str, str | None] = {}
    for tool in PATH_TOOLS:
        try:
            tools[tool] = shutil.which(tool)
        except Exception:
            tools[tool] = None

    shell = "unknown"
    try:
        shell = os.environ.get("SHELL") or shutil.which("bash") or "unknown"
    except Exception:
        pass

    ram_total, ram_available = _probe_ram()

    cpu_cores = None
    try:
        cpu_cores = os.cpu_count()
    except Exception:
        pass

    try:
        scanned_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        scanned_at = ""

    return EnvironmentProfile(
        platform=plat,
        platform_detail=detail,
        python_version=platform.python_version(),
        python_executable=sys.executable,
        shell=shell,
        capabilities=capabilities,
        tools=tools,
        ram_total_mb=ram_total,
        ram_available_mb=ram_available,
        cpu_cores=cpu_cores,
        scanned_at=scanned_at,
    )


def discover_environment(force: bool = False) -> EnvironmentProfile:
    """Return the environment profile, using the 24h cache when possible.

    - fresh cache present -> served from cache (``stale=False``)
    - cache expired       -> auto re-scan; if the scan somehow fails the
      cached profile is returned with ``stale=True``
    - ``force=True``      -> always re-scan and rewrite the cache
    """
    cache = _cache_path()
    cached_data: dict | None = None
    cached_age: float | None = None

    if not force and cache.exists():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            cached_data = raw.get("profile", raw)
            cached_age = max(0.0, time.time() - float(raw.get("scanned_at_epoch", 0)))
        except Exception:
            cached_data = None  # corrupt cache = no cache

    if cached_data is not None and cached_age is not None and cached_age < CACHE_MAX_AGE_SECONDS:
        try:
            return EnvironmentProfile.from_dict(cached_data)
        except Exception:
            pass  # fall through to a fresh scan

    try:
        profile = _scan_environment()
        try:
            _atomic_write_json(cache, {
                "scanned_at_epoch": time.time(),
                "profile": profile.to_dict(),
            })
        except Exception:
            logger.debug("environment cache write failed", exc_info=True)
        return profile
    except Exception:
        # Discovery itself is defensive, but if even that failed and we have
        # an expired cache, hand it back flagged as stale rather than crash.
        if cached_data is not None:
            try:
                profile = EnvironmentProfile.from_dict(cached_data)
                profile.stale = True
                return profile
            except Exception:
                pass
        raise


def _patch_cache_capabilities(updates: dict[str, bool]) -> None:
    """Best-effort: mark freshly installed capabilities in the cache file."""
    cache = _cache_path()
    if not cache.exists():
        return
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
        profile = raw.get("profile", raw)
        caps = profile.setdefault("capabilities", {})
        caps.update(updates)
        _atomic_write_json(cache, raw)
    except Exception:
        logger.debug("environment cache patch failed", exc_info=True)


# ── capability report ────────────────────────────────────────────────────

@dataclass
class CapabilityReport:
    """Outcome of one ensure_capabilities() requirement."""

    name: str
    pip_spec: str
    status: str  # present | installed | failed
    skipped: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("present", "installed")


def format_capability_report(reports: Iterable[CapabilityReport]) -> str:
    """Human-readable multi-line summary of an ensure_capabilities() run."""
    lines = []
    for r in reports:
        if r.status == "present":
            lines.append(f"✓ {r.name} present — skipping install")
        elif r.status == "installed":
            lines.append(f"✔ {r.name} installed ({r.pip_spec})")
        else:
            tail = f": {r.detail}" if r.detail else ""
            lines.append(f"✘ {r.name} could not be installed ({r.pip_spec}){tail}")
    return "\n".join(lines)


def _pip_install(spec: str) -> tuple[bool, str]:
    """Install one pip spec into the active interpreter. Returns (ok, tail)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", spec],
            timeout=PIP_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip timed out after {PIP_TIMEOUT_SECONDS}s"
    except Exception as e:  # noqa: BLE001 — pip itself can fail in creative ways
        return False, str(e)
    if proc.returncode == 0:
        return True, ""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, tail[-1] if tail else f"pip exited {proc.returncode}"


def ensure_capabilities(requirements: Iterable[tuple[str, str | None]]) -> list[CapabilityReport]:
    """Install only the capabilities that are actually missing.

    ``requirements`` is a list of ``(capability_name, pip_spec)`` tuples;
    a ``None`` spec is looked up in :data:`CAPABILITY_MAP`. For each item:

    - already present  -> ``status="present", skipped=True`` (and a visible
      "✓ ... skipping" line, so users see Nexus detected it)
    - missing          -> pip install just that spec into the active
      interpreter (300s timeout, output captured)
    """
    reports: list[CapabilityReport] = []
    installed_now: dict[str, bool] = {}
    for name, spec in requirements:
        import_name = name.replace("-", "_")
        pip_spec = spec or CAPABILITY_MAP.get(import_name) or name
        try:
            present = _module_available(import_name)
        except Exception:
            present = False
        if present:
            print(f"  \033[32m✓\033[0m {name} present — skipping install")
            reports.append(CapabilityReport(name=name, pip_spec=pip_spec, status="present", skipped=True))
            continue
        print(f"  \033[36m⬇\033[0m {pip_spec} not found — installing...")
        ok, tail = _pip_install(pip_spec)
        if ok:
            # Verify with find_spec; a green pip can still leave a broken wheel.
            if _module_available(import_name):
                print(f"  \033[32m✔\033[0m {name} installed ({pip_spec})")
                installed_now[import_name] = True
                reports.append(CapabilityReport(name=name, pip_spec=pip_spec, status="installed", skipped=False))
                continue
            ok, tail = False, "installed but still not importable"
        print(f"  \033[31m✘\033[0m {name} could not be installed ({pip_spec})" + (f": {tail}" if tail else ""))
        reports.append(CapabilityReport(name=name, pip_spec=pip_spec, status="failed", skipped=False, detail=tail))
    if installed_now:
        try:
            _patch_cache_capabilities(installed_now)
        except Exception:
            pass
    return reports


def ensure_dependency(package_name: str, import_name: str | None = None) -> bool:
    """Drop-in replacement for nexus.utils.dependencies.ensure_dependency.

    Adds the skip-if-present path on top of the original behaviour: when the
    module is already importable, a visible "present — skipping" line is
    printed and no install is attempted. When missing, the original
    interactive installer (Termux warnings, confirm prompt, pip) takes over.
    """
    import_name = (import_name or package_name).replace("-", "_")
    if _module_available(import_name):
        print(f"  \033[32m✓\033[0m {package_name} present — skipping install")
        return True
    # Missing: keep the original interactive flow (env warnings + prompt).
    # Pass the underscore-normalized import name — e.g. ensure_dependency(
    # "faster-whisper") used to try import_module("faster-whisper") and
    # re-prompt on every call; find_spec("faster_whisper") fixes that.
    from nexus.utils import dependencies as _deps

    return _deps.ensure_dependency(package_name, import_name)


# ── project sniff + boot summary ─────────────────────────────────────────

def familiarize_project(workspace: str | Path | None = None) -> dict[str, Any]:
    """Light sniff of the current project (NOT the full /init scan).

    Returns a small dict: top-3 languages by file count, manifest count,
    NEXUS.md presence and git branch. Never raises.
    """
    ws = Path(workspace) if workspace else Path.cwd()
    info: dict[str, Any] = {
        "path": str(ws),
        "languages": [],  # top-3 as {"language": str, "files": int}
        "manifest_count": 0,
        "has_nexus_md": False,
        "git_branch": None,
        "is_git": False,
    }
    try:
        info["has_nexus_md"] = (ws / "NEXUS.md").exists()
    except Exception:
        pass
    try:
        info["manifest_count"] = sum(1 for m in _MANIFESTS if (ws / m).exists())
    except Exception:
        pass
    try:
        from nexus.project import _detect_languages

        info["languages"] = [
            {"language": lang, "files": count} for lang, count in _detect_languages(ws)[:3]
        ]
    except Exception:
        logger.debug("language sniff failed", exc_info=True)
    try:
        from nexus.project import _run_git

        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], ws)
        if branch:
            info["git_branch"] = branch
            info["is_git"] = True
    except Exception:
        pass
    return info


def boot_summary() -> dict[str, Any]:
    """The dict boot checklists (TUI/REPL) want. Never raises."""
    summary: dict[str, Any] = {
        "platform": "unknown",
        "python_version": platform.python_version(),
        "memory_ok": True,
        "memory_total_mb": None,
        "memory_available_mb": None,
        "cpu_cores": None,
        "skills_count": 0,
        "tools_count": 0,
        "mcp_servers": 0,
        "provider": None,
    }
    try:
        profile = discover_environment()
        summary["platform"] = profile.platform
        summary["memory_total_mb"] = profile.ram_total_mb
        summary["memory_available_mb"] = profile.ram_available_mb
        summary["cpu_cores"] = profile.cpu_cores
        if profile.ram_available_mb is not None:
            summary["memory_ok"] = profile.ram_available_mb >= MEMORY_OK_FLOOR_MB
    except Exception:
        logger.debug("environment summary failed", exc_info=True)
    try:
        from nexus.skills.matcher import get_skill_matcher

        matcher = get_skill_matcher()
        matcher.ensure_index()  # skill_count reads the index without building it
        summary["skills_count"] = matcher.skill_count
    except Exception:
        logger.debug("skills count failed", exc_info=True)
    try:
        from nexus.tools import get_registry

        summary["tools_count"] = len(get_registry().list_all())
    except Exception:
        logger.debug("tools count failed", exc_info=True)
    try:
        from nexus.mcp import default_config_path

        path = default_config_path()
        if path.exists():
            import json as _json

            summary["mcp_servers"] = len(_json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.debug("mcp count failed", exc_info=True)
    try:
        from nexus.config import load_config

        summary["provider"] = load_config().active_provider
    except Exception:
        logger.debug("provider lookup failed", exc_info=True)
    return summary
