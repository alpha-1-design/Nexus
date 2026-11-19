"""Tests for the ensure_dependency-style Playwright availability probe.

The old module-level ``PLAYWRIGHT_AVAILABLE`` flag was frozen at import
time and never offered an install. ``playwright_available()`` now does a
fresh find_spec probe (skip-if-present) and routes the install offer
through ``nexus.discovery.ensure_dependency`` — with discovery imported
lazily inside the probe so no import cycle is created.
"""

import importlib.util
import sys

import pytest

import nexus.automation.browser as browser_mod

_REAL_FIND_SPEC = importlib.util.find_spec


@pytest.fixture
def fake_spec(monkeypatch):
    """Route find_spec through a controllable fake (playwright only)."""
    state = {"spec": None}

    def fake_find_spec(name, *args, **kwargs):
        if name == "playwright":
            return object() if state["spec"] else None
        return _REAL_FIND_SPEC(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    return state


def test_present_playwright_skips_install(fake_spec, monkeypatch):
    fake_spec["spec"] = object()

    def boom(*args, **kwargs):
        raise AssertionError("install offered for an already-present package")

    monkeypatch.setattr("nexus.discovery.ensure_dependency", boom)
    assert browser_mod.playwright_available() is True


def test_missing_playwright_without_offer_is_false(fake_spec, monkeypatch):
    fake_spec["spec"] = None

    def boom(*args, **kwargs):
        raise AssertionError("install offered despite offer_install=False")

    monkeypatch.setattr("nexus.discovery.ensure_dependency", boom)
    assert browser_mod.playwright_available(offer_install=False) is False


def test_missing_playwright_offers_install(fake_spec, monkeypatch):
    fake_spec["spec"] = None
    calls = []

    def fake_ensure(name, import_name=None):
        calls.append(name)
        return False

    monkeypatch.setattr("nexus.discovery.ensure_dependency", fake_ensure)
    assert browser_mod.playwright_available() is False
    assert calls == ["playwright"]


def test_falls_back_to_bare_find_spec_when_discovery_broken(fake_spec, monkeypatch):
    fake_spec["spec"] = object()
    # sys.modules[name] = None makes `from ..discovery import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "nexus.discovery", None)
    assert browser_mod.playwright_available() is True


def test_is_browser_available_false_when_missing(fake_spec, monkeypatch):
    fake_spec["spec"] = None

    def boom(*args, **kwargs):
        raise AssertionError("availability check prompted for install")

    monkeypatch.setattr("nexus.discovery.ensure_dependency", boom)
    assert browser_mod.is_browser_available() is False


def test_browser_automation_raises_import_error_without_prompting(fake_spec, monkeypatch):
    fake_spec["spec"] = None

    def no_prompt(*args, **kwargs):
        raise AssertionError("__init__ must not prompt for install")

    monkeypatch.setattr("builtins.input", no_prompt)
    with pytest.raises(ImportError):
        browser_mod.BrowserAutomation()
