"""Tests for nexus/discovery.py — environment auto-discovery.

Strategy: the cache path is pointed at tmp files via NEXUS_ENVIRONMENT_CACHE,
platform detection is driven by stubbed Path/os.environ, and pip installs are
never actually executed (subprocess is mocked at discovery._pip_install).
"""

import json
import time
from pathlib import Path

import pytest

import nexus.discovery as discovery
from nexus.discovery import (
    CapabilityReport,
    EnvironmentProfile,
    boot_summary,
    discover_environment,
    ensure_capabilities,
    ensure_dependency,
    familiarize_project,
    format_capability_report,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    """Isolated cache file so the user's real ~/.nexus is never touched."""
    path = tmp_path / "environment.json"
    monkeypatch.setenv("NEXUS_ENVIRONMENT_CACHE", str(path))
    return path


class _StubPath:
    """Minimal Path stand-in for platform probes (exists / read_text only)."""

    existing: set[str] = set()
    proc_version: str = "Linux version 5.0"

    def __init__(self, raw):
        self.raw = str(raw)

    def exists(self):
        return self.raw in self.existing

    def is_dir(self):
        return self.raw in self.existing

    def read_text(self, encoding=None, errors=None):
        if self.raw in self.existing:
            return self.proc_version
        raise OSError(self.raw)


# =============================================================================
# Platform detection
# =============================================================================


class TestPlatformDetection:
    def test_termux_via_env(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118")
        plat, detail = discovery._detect_platform()
        assert plat == "termux"
        assert "0.118" in detail

    def test_docker_via_dockerenv(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setattr(discovery, "Path", _StubPath)
        _StubPath.existing = {"/.dockerenv"}
        try:
            plat, detail = discovery._detect_platform()
            assert plat == "docker"
            assert "Docker" in detail
        finally:
            _StubPath.existing = set()

    def test_wsl_via_proc_version(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setattr(discovery, "Path", _StubPath)
        _StubPath.existing = {"/proc/version"}
        _StubPath.proc_version = "Linux version 5.15 microsoft-standard-WSL2"
        try:
            plat, detail = discovery._detect_platform()
            assert plat == "wsl"
        finally:
            _StubPath.existing = set()
            _StubPath.proc_version = "Linux version 5.0"

    def test_plain_linux_fallback(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setattr(discovery, "Path", _StubPath)
        _StubPath.existing = {"/proc/version"}  # exists but no "microsoft"
        try:
            plat, _ = discovery._detect_platform()
            assert plat == "linux"
        finally:
            _StubPath.existing = set()

    def test_macos(self, monkeypatch):
        monkeypatch.setattr(discovery.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(discovery, "Path", _StubPath)  # no /.dockerenv here
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        _StubPath.existing = set()
        plat, _ = discovery._detect_platform()
        assert plat == "macos"


# =============================================================================
# Capability probing + profile roundtrip
# =============================================================================


class TestProbes:
    def test_module_available_real_modules(self):
        assert discovery._module_available("json") is True
        assert discovery._module_available("definitely_not_a_module_xyz") is False

    def test_module_available_never_raises(self, monkeypatch):
        def boom(name):
            raise RuntimeError("crazy sys.meta_path")

        monkeypatch.setattr(discovery.importlib.util, "find_spec", boom)
        assert discovery._module_available("json") is False

    def test_profile_missing_and_present_lists(self):
        profile = EnvironmentProfile(
            capabilities={"yaml": True, "flask": False},
            tools={"git": "/usr/bin/git", "gh": None},
        )
        assert profile.present_capabilities() == ["yaml"]
        assert profile.missing_capabilities() == ["flask"]
        assert profile.present_tools() == ["git"]
        assert profile.missing_tools() == ["gh"]

    def test_profile_dict_roundtrip(self):
        profile = EnvironmentProfile(platform="termux", python_version="3.12.0", cpu_cores=8)
        clone = EnvironmentProfile.from_dict(profile.to_dict())
        assert clone.platform == "termux" and clone.cpu_cores == 8
        assert clone.stale is False


# =============================================================================
# Cache: roundtrip, freshness, staleness, corruption
# =============================================================================


class TestCache:
    def test_force_scan_writes_cache(self, cache_path):
        profile = discover_environment(force=True)
        assert cache_path.exists()
        raw = json.loads(cache_path.read_text())
        assert raw["profile"]["platform"] == profile.platform
        assert "scanned_at_epoch" in raw

    def test_fresh_cache_is_served_without_rescan(self, cache_path, monkeypatch):
        profile = discover_environment(force=True)
        monkeypatch.setattr(discovery, "_scan_environment", lambda: (_ for _ in ()).throw(AssertionError("rescanned!")))
        again = discover_environment()
        assert again.platform == profile.platform
        assert again.stale is False

    def test_expired_cache_triggers_refresh(self, cache_path, monkeypatch):
        # A cache older than 24h must be refreshed automatically.
        old = {"scanned_at_epoch": time.time() - 25 * 3600, "profile": {"platform": "macos"}}
        cache_path.write_text(json.dumps(old))
        scanned = []

        def fake_scan():
            scanned.append(1)
            return EnvironmentProfile(platform="linux", scanned_at="now")

        monkeypatch.setattr(discovery, "_scan_environment", fake_scan)
        profile = discover_environment()
        assert scanned and profile.platform == "linux" and profile.stale is False

    def test_expired_cache_with_failed_scan_is_stale(self, cache_path, monkeypatch):
        old = {"scanned_at_epoch": time.time() - 25 * 3600, "profile": {"platform": "macos", "cpu_cores": 4}}
        cache_path.write_text(json.dumps(old))

        def boom():
            raise RuntimeError("sensor chaos")

        monkeypatch.setattr(discovery, "_scan_environment", boom)
        profile = discover_environment()
        assert profile.stale is True
        assert profile.platform == "macos" and profile.cpu_cores == 4

    def test_corrupt_cache_is_ignored(self, cache_path):
        cache_path.write_text("{not json at all")
        profile = discover_environment()
        assert profile.platform in ("linux", "docker", "wsl", "macos", "termux")


# =============================================================================
# ensure_capabilities — the skip-installed installer
# =============================================================================


class TestEnsureCapabilities:
    def test_present_capability_is_skipped(self, monkeypatch, capsys):
        monkeypatch.setattr(discovery, "_module_available", lambda name: True)
        called = []
        monkeypatch.setattr(discovery, "_pip_install", lambda spec: called.append(spec) or (_ for _ in ()).throw(AssertionError("pip ran!")))
        reports = ensure_capabilities([("httpx", None)])
        assert len(reports) == 1
        r = reports[0]
        assert r.status == "present" and r.skipped is True and r.ok
        assert called == []
        assert "skipping" in capsys.readouterr().out

    def test_missing_capability_is_installed(self, cache_path, monkeypatch):
        state = {"installed": False}

        def fake_available(name):
            return state["installed"]

        monkeypatch.setattr(discovery, "_module_available", fake_available)
        monkeypatch.setattr(discovery, "_pip_install", lambda spec: state.__setitem__("installed", True) or (True, ""))
        reports = ensure_capabilities([("psutil", None)])
        r = reports[0]
        assert r.status == "installed" and r.skipped is False and r.ok
        assert r.pip_spec == "psutil"

    def test_failed_install_reports_detail(self, monkeypatch):
        monkeypatch.setattr(discovery, "_module_available", lambda name: False)
        monkeypatch.setattr(discovery, "_pip_install", lambda spec: (False, "no such distribution"))
        reports = ensure_capabilities([("faster_whisper", None)])
        r = reports[0]
        assert r.status == "failed" and not r.ok
        assert r.detail == "no such distribution"

    def test_pip_spec_defaults_from_capability_map(self):
        assert discovery.CAPABILITY_MAP["yaml"] == "pyyaml"
        assert discovery.CAPABILITY_MAP["speech_recognition"] == "SpeechRecognition"
        assert discovery.CAPABILITY_MAP["faster_whisper"] == "faster-whisper"

    def test_install_success_then_verifiable(self, cache_path, monkeypatch):
        # A green pip that leaves a broken module must be reported as failed.
        monkeypatch.setattr(discovery, "_module_available", lambda name: False)
        monkeypatch.setattr(discovery, "_pip_install", lambda spec: (True, ""))
        reports = ensure_capabilities([("rich", None)])
        assert reports[0].status == "failed"
        assert "not importable" in reports[0].detail

    def test_installed_capabilities_are_patched_into_cache(self, cache_path, monkeypatch):
        discover_environment(force=True)  # create the cache
        state = {"installed": False}
        monkeypatch.setattr(discovery, "_module_available", lambda n: state["installed"])
        monkeypatch.setattr(discovery, "_pip_install", lambda spec: state.__setitem__("installed", True) or (True, ""))
        ensure_capabilities([("httpx", None)])
        raw = json.loads(cache_path.read_text())
        assert raw["profile"]["capabilities"]["httpx"] is True

    def test_report_formatting(self):
        reports = [
            CapabilityReport(name="rich", pip_spec="rich", status="present", skipped=True),
            CapabilityReport(name="psutil", pip_spec="psutil", status="installed", skipped=False),
            CapabilityReport(name="pyaudio", pip_spec="pyaudio", status="failed", skipped=False, detail="portaudio missing"),
        ]
        text = format_capability_report(reports)
        lines = text.splitlines()
        assert "✓ rich present — skipping install" in lines[0]
        assert "✔ psutil installed (psutil)" in lines[1]
        assert "✘ pyaudio could not be installed (pyaudio): portaudio missing" in lines[2]


class TestEnsureDependencyDropIn:
    def test_present_prints_skip_line(self, monkeypatch, capsys):
        monkeypatch.setattr(discovery, "_module_available", lambda name: True)
        assert ensure_dependency("flask") is True
        assert "flask present — skipping install" in capsys.readouterr().out

    def test_missing_delegates_to_interactive_installer(self, monkeypatch):
        monkeypatch.setattr(discovery, "_module_available", lambda name: False)
        seen = {}

        def fake_original(package_name, import_name=None):
            seen["package"] = package_name
            seen["import"] = import_name
            return False

        from nexus.utils import dependencies as deps

        monkeypatch.setattr(deps, "ensure_dependency", fake_original)
        assert ensure_dependency("faster-whisper") is False
        assert seen["package"] == "faster-whisper"
        assert seen["import"] == "faster_whisper"  # underscore-normalized


# =============================================================================
# familiarize_project + boot_summary
# =============================================================================


class TestFamiliarizeProject:
    def test_sniffs_project(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "NEXUS.md").write_text("# Nexus")
        for i in range(3):
            (tmp_path / f"mod{i}.py").write_text("x = 1\n")
        monkeypatch.setattr("nexus.project._run_git", lambda args, cwd: "main")

        info = familiarize_project(tmp_path)
        assert info["manifest_count"] == 2
        assert info["has_nexus_md"] is True
        assert info["git_branch"] == "main" and info["is_git"] is True
        langs = [l["language"] for l in info["languages"]]
        assert "Python" in langs and len(langs) <= 3

    def test_empty_directory_is_quiet(self, tmp_path):
        info = familiarize_project(tmp_path)
        assert info["manifest_count"] == 0
        assert info["languages"] == []
        assert info["git_branch"] is None

    def test_missing_directory_never_raises(self, tmp_path):
        info = familiarize_project(tmp_path / "does-not-exist")
        assert info["has_nexus_md"] is False


class TestBootSummary:
    def test_summary_keys_and_counts(self, tmp_path, monkeypatch):
        mcp_cfg = tmp_path / "mcp-servers.json"
        mcp_cfg.write_text(json.dumps({"server-a": {"transport": "stdio", "command": "true"},
                                       "server-b": {"transport": "stdio", "command": "true"}}))
        monkeypatch.setenv("NEXUS_MCP_CONFIG", str(mcp_cfg))
        summary = boot_summary()
        for key in ("memory_ok", "skills_count", "tools_count", "mcp_servers", "provider"):
            assert key in summary
        assert summary["mcp_servers"] == 2
        assert summary["skills_count"] > 0  # bundled catalog indexed
        assert summary["memory_ok"] is True

    def test_summary_never_raises_on_broken_inputs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NEXUS_MCP_CONFIG", str(tmp_path / "garbage.json"))
        (tmp_path / "garbage.json").write_text("{oops")
        monkeypatch.setenv("NEXUS_ENVIRONMENT_CACHE", str(tmp_path / "env.json"))
        summary = boot_summary()
        assert summary["mcp_servers"] == 0
        assert isinstance(summary["tools_count"], int)
